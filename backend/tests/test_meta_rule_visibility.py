import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import MetaRule, MetaRuleEvent, Permission, Role, User

RULES = "/api/v1/meta/rules"
EVENTS = "/api/v1/meta/rule-events"


async def _stage(suffix: str) -> dict:
    """Два баера с областью «только свои», у каждого своё правило и срабатывание."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        permissions = list(
            (
                await db.execute(
                    select(Permission).where(Permission.code.in_(["meta.view", "meta.launch"]))
                )
            ).scalars()
        )
        role = Role(
            workspace_id=admin.workspace_id,
            name=f"Rules own {suffix}",
            description="",
            data_scope="own",
            permissions=permissions,
        )
        db.add(role)
        await db.flush()
        people, rules, events = {}, {}, {}
        for key in ("mine", "other"):
            person = User(
                workspace_id=admin.workspace_id,
                role_id=role.id,
                name=f"Rules {key} {suffix}",
                login=f"rules{key}{suffix}",
                password_hash=hash_password("rules-password"),
            )
            db.add(person)
            await db.flush()
            rule = MetaRule(
                workspace_id=admin.workspace_id,
                name=f"STOP {key} {suffix}",
                created_by_id=person.id,
            )
            db.add(rule)
            await db.flush()
            event = MetaRuleEvent(
                workspace_id=admin.workspace_id,
                rule_id=rule.id,
                campaign_name=f"Campaign {key} {suffix}",
                metric="spend_total",
                action="notify",
                message=f"«STOP {key}» сработало {suffix}",
            )
            db.add(event)
            await db.flush()
            people[key], rules[key], events[key] = person, rule, event
        made = {
            "role": role.id,
            "logins": {key: person.login for key, person in people.items()},
            "users": [person.id for person in people.values()],
            "rules": {key: rule.id for key, rule in rules.items()},
            "events": {key: event.id for key, event in events.items()},
        }
        await db.commit()
        return made


async def test_rule_events_are_visible_only_to_their_owner(database) -> None:
    """Срабатывания и правила — свои у каждого, у администратора — все."""
    suffix = uuid.uuid4().hex[:6]
    made = await _stage(suffix)
    mine_rule, other_rule = str(made["rules"]["mine"]), str(made["rules"]["other"])
    mine_event, other_event = str(made["events"]["mine"]), str(made["events"]["other"])
    try:
        with TestClient(app) as client:
            signed = client.post(
                "/api/v1/auth/login",
                json={"login": made["logins"]["mine"], "password": "rules-password"},
            )
            assert signed.status_code == 200, signed.text

            rule_ids = {row["id"] for row in client.get(RULES).json()["items"]}
            assert mine_rule in rule_ids
            assert other_rule not in rule_ids

            event_ids = {row["id"] for row in client.get(EVENTS).json()["items"]}
            assert mine_event in event_ids
            assert other_event not in event_ids

            # Чужое правило — «не найдено»: ни изменить, ни удалить.
            assert client.patch(
                f"{RULES}/{other_rule}", json={"is_enabled": False}
            ).status_code == 404
            assert client.delete(f"{RULES}/{other_rule}").status_code == 404

            # «Прочитать все» не трогает чужие срабатывания.
            assert client.post(f"{EVENTS}/ack", json=[]).status_code == 200

        async with SessionLocal() as db:
            assert (await db.get(MetaRuleEvent, made["events"]["mine"])).acknowledged_at
            assert (await db.get(MetaRuleEvent, made["events"]["other"])).acknowledged_at is None
            assert await db.get(MetaRule, made["rules"]["other"]) is not None

        with TestClient(app) as client:
            assert client.post(
                "/api/v1/auth/login", json={"login": "admin", "password": "test-password"}
            ).status_code == 200
            rule_ids = {row["id"] for row in client.get(RULES).json()["items"]}
            assert {mine_rule, other_rule} <= rule_ids
            listed = client.get(EVENTS, params={"limit": 200}).json()["items"]
            assert {mine_event, other_event} <= {row["id"] for row in listed}
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(MetaRuleEvent).where(
                    MetaRuleEvent.id.in_(list(made["events"].values()))
                )
            )
            await db.execute(delete(MetaRule).where(MetaRule.id.in_(list(made["rules"].values()))))
            await db.execute(delete(User).where(User.id.in_(made["users"])))
            await db.execute(delete(Role).where(Role.id == made["role"]))
            await db.commit()
