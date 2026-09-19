import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import AlertChannel, CapRule, Permission, Role, Status, User, UserParent

CAPS = "/api/v1/utilities/caps"


async def _stage(suffix: str) -> dict:
    """Тимлид с областью «своя команда», его баер с областью «только свои»."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        codes = ["utilities.view", "utilities.manage"]
        permissions = list(
            (await db.execute(select(Permission).where(Permission.code.in_(codes)))).scalars()
        )
        roles = {}
        for scope in ("team", "own"):
            role = Role(
                workspace_id=admin.workspace_id,
                name=f"Caps {scope} {suffix}",
                description="",
                data_scope=scope,
                permissions=permissions,
            )
            db.add(role)
            roles[scope] = role
        await db.flush()
        lead = User(
            workspace_id=admin.workspace_id,
            role_id=roles["team"].id,
            name=f"Caps lead {suffix}",
            login=f"capslead{suffix}",
            password_hash=hash_password("caps-password"),
        )
        buyer = User(
            workspace_id=admin.workspace_id,
            role_id=roles["own"].id,
            name=f"Caps buyer {suffix}",
            login=f"capsbuyer{suffix}",
            password_hash=hash_password("caps-password"),
        )
        db.add_all([lead, buyer])
        await db.flush()
        db.add(UserParent(user_id=buyer.id, parent_id=lead.id))
        channel = AlertChannel(
            workspace_id=admin.workspace_id,
            name=f"Caps channel {suffix}",
            chat_id="-100123",
            status=Status.active,
        )
        db.add(channel)
        await db.flush()
        caps = {}
        for key, owner in (("lead", lead.id), ("buyer", buyer.id), ("free", None)):
            rule = CapRule(
                workspace_id=admin.workspace_id,
                name=f"CAP {key} {suffix}",
                channel_id=channel.id,
                user_id=owner,
                metric="ftd",
                limit_value=50,
                period="day",
                notify_at=[100],
            )
            db.add(rule)
            caps[key] = rule
        await db.flush()
        made = {
            "roles": [role.id for role in roles.values()],
            "lead": lead.id,
            "buyer": buyer.id,
            "channel": channel.id,
            "caps": {key: rule.id for key, rule in caps.items()},
            "logins": {"lead": lead.login, "buyer": buyer.login},
        }
        await db.commit()
        return made


def _names(client: TestClient) -> set[str]:
    listed = client.get(CAPS)
    assert listed.status_code == 200, listed.text
    return {item["name"] for item in listed.json()["items"]}


async def test_cap_assigned_to_a_user_is_hidden_from_the_others(database) -> None:
    """Капа видна тому, на кого назначена, и тем, кому он виден по области доступа.

    Капа без пользователя — общий лимит на связку офферов: она ничья и видна
    всем, кто открывает раздел.
    """
    suffix = uuid.uuid4().hex[:6]
    made = await _stage(suffix)
    lead_cap = f"CAP lead {suffix}"
    buyer_cap = f"CAP buyer {suffix}"
    free_cap = f"CAP free {suffix}"
    try:
        with TestClient(app) as client:
            signed = client.post(
                "/api/v1/auth/login",
                json={"login": made["logins"]["buyer"], "password": "caps-password"},
            )
            assert signed.status_code == 200, signed.text
            visible = _names(client)
            assert buyer_cap in visible
            assert free_cap in visible
            assert lead_cap not in visible

            # Чужая капа — «не найдена», а не «нельзя».
            foreign = client.patch(
                f"{CAPS}/{made['caps']['lead']}",
                json={
                    "name": "Перехват",
                    "channel_id": str(made["channel"]),
                    "user_id": str(made["buyer"]),
                    "metric": "sales",
                    "limit_value": 10,
                    "period": "day",
                    "notify_at": [100],
                },
            )
            assert foreign.status_code == 404

            # Назначить капу на тимлида баер тоже не может.
            outside = client.post(
                CAPS,
                json={
                    "name": f"CAP outside {suffix}",
                    "channel_id": str(made["channel"]),
                    "user_id": str(made["lead"]),
                    "metric": "sales",
                    "limit_value": 10,
                    "period": "day",
                    "notify_at": [100],
                },
            )
            assert outside.status_code == 422

        with TestClient(app) as client:
            signed = client.post(
                "/api/v1/auth/login",
                json={"login": made["logins"]["lead"], "password": "caps-password"},
            )
            assert signed.status_code == 200
            visible = _names(client)
            assert {lead_cap, buyer_cap, free_cap} <= visible
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(CapRule).where(CapRule.id.in_(list(made["caps"].values())))
            )
            await db.execute(delete(AlertChannel).where(AlertChannel.id == made["channel"]))
            await db.execute(
                delete(UserParent).where(UserParent.user_id.in_([made["buyer"], made["lead"]]))
            )
            await db.execute(delete(User).where(User.id.in_([made["buyer"], made["lead"]])))
            await db.execute(delete(Role).where(Role.id.in_(made["roles"])))
            await db.commit()
