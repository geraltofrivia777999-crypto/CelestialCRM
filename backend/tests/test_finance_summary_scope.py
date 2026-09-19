import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import Permission, Role, User, UserParent


async def _people(suffix: str) -> dict:
    """Тимлид с областью «своя команда» и его баер с областью «только свои»."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        view = await db.scalar(select(Permission).where(Permission.code == "finance.view"))
        roles = {}
        for scope in ("team", "own"):
            role = Role(
                workspace_id=admin.workspace_id,
                name=f"Finance {scope} {suffix}",
                description="",
                data_scope=scope,
                # Как после миграции: у «Только свои данные» сводки выключены.
                show_finance_summaries=scope != "own",
                permissions=[view],
            )
            db.add(role)
            roles[scope] = role
        await db.flush()
        lead = User(
            workspace_id=admin.workspace_id,
            role_id=roles["team"].id,
            name=f"Fin lead {suffix}",
            login=f"finlead{suffix}",
            password_hash=hash_password("fin-password"),
        )
        buyer = User(
            workspace_id=admin.workspace_id,
            role_id=roles["own"].id,
            name=f"Fin buyer {suffix}",
            login=f"finbuyer{suffix}",
            password_hash=hash_password("fin-password"),
        )
        db.add_all([lead, buyer])
        await db.flush()
        db.add(UserParent(user_id=buyer.id, parent_id=lead.id))
        made = {
            "roles": [role.id for role in roles.values()],
            "lead": lead.id,
            "buyer": buyer.id,
            "logins": {"lead": lead.login, "buyer": buyer.login},
        }
        await db.commit()
        return made


async def test_only_team_roles_see_finance_summaries(database) -> None:
    """Сводки «Общая», «Tier1», «Tier2/3» — не для роли «Только свои данные»."""
    suffix = uuid.uuid4().hex[:6]
    made = await _people(suffix)
    now = datetime.now(UTC)
    period = {"year": now.year, "month": now.month}
    try:
        with TestClient(app) as client:
            signed = client.post(
                "/api/v1/auth/login",
                json={"login": made["logins"]["buyer"], "password": "fin-password"},
            )
            assert signed.status_code == 200, signed.text
            scopes = client.get("/api/v1/finance/scopes", params=period)
            assert scopes.status_code == 200, scopes.text
            payload = scopes.json()
            assert payload["summaries"] == []
            assert payload["teams"] == []
            assert [row["id"] for row in payload["buyers"]] == [str(made["buyer"])]

            refused = client.get(
                "/api/v1/finance/summary", params={**period, "scope": "all"}
            )
            assert refused.status_code == 403

        with TestClient(app) as client:
            signed = client.post(
                "/api/v1/auth/login",
                json={"login": made["logins"]["lead"], "password": "fin-password"},
            )
            assert signed.status_code == 200
            payload = client.get("/api/v1/finance/scopes", params=period).json()
            assert [row["scope"] for row in payload["summaries"]] == ["all", "tier1", "tier23"]
            allowed = client.get(
                "/api/v1/finance/summary", params={**period, "scope": "all"}
            )
            assert allowed.status_code == 200, allowed.text
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(UserParent).where(UserParent.user_id.in_([made["buyer"], made["lead"]]))
            )
            await db.execute(delete(User).where(User.id.in_([made["buyer"], made["lead"]])))
            await db.execute(delete(Role).where(Role.id.in_(made["roles"])))
            await db.commit()


async def test_the_role_switch_hides_workspace_summaries(database) -> None:
    """Переключатель в роли прячет «Общая», «Tier1», «Tier2/3» даже у тимлида."""
    suffix = uuid.uuid4().hex[:6]
    made = await _people(suffix)
    now = datetime.now(UTC)
    period = {"year": now.year, "month": now.month}
    try:
        async with SessionLocal() as db:
            lead = await db.get(User, made["lead"])
            role = await db.get(Role, lead.role_id)
            role.show_finance_summaries = False
            await db.commit()
        with TestClient(app) as client:
            signed = client.post(
                "/api/v1/auth/login",
                json={"login": made["logins"]["lead"], "password": "fin-password"},
            )
            assert signed.status_code == 200
            payload = client.get("/api/v1/finance/scopes", params=period).json()
            assert payload["summaries"] == []
            # Своя книга и книга подчинённого по-прежнему открываются.
            assert {str(made["lead"]), str(made["buyer"])} <= {
                row["id"] for row in payload["buyers"]
            }
            refused = client.get("/api/v1/finance/summary", params={**period, "scope": "tier1"})
            assert refused.status_code == 403
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(UserParent).where(UserParent.user_id.in_([made["buyer"], made["lead"]]))
            )
            await db.execute(delete(User).where(User.id.in_([made["buyer"], made["lead"]])))
            await db.execute(delete(Role).where(Role.id.in_(made["roles"])))
            await db.commit()


async def test_role_api_keeps_the_summaries_switch(database) -> None:
    role_id = None
    try:
        with TestClient(app) as client:
            assert client.post(
                "/api/v1/auth/login", json={"login": "admin", "password": "test-password"}
            ).status_code == 200
            created = client.post(
                "/api/v1/roles",
                json={
                    "name": f"Summaries {uuid.uuid4().hex[:6]}",
                    "show_finance_summaries": False,
                    "permission_codes": ["finance.view"],
                },
            )
            assert created.status_code == 201, created.text
            role_id = created.json()["id"]
            assert created.json()["show_finance_summaries"] is False
            updated = client.patch(
                f"/api/v1/roles/{role_id}", json={"show_finance_summaries": True}
            )
            assert updated.json()["show_finance_summaries"] is True
    finally:
        if role_id:
            async with SessionLocal() as db:
                await db.execute(delete(Role).where(Role.id == uuid.UUID(role_id)))
                await db.commit()
