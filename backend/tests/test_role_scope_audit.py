"""Аудит областей доступа: чужие данные не видны и не меняются по id.

Два баера с областью «только свои» — у каждого своё подключение Meta, кабинет,
связка, автоправило, группа правил и капа. Всё, что заведено одним, для второго
не существует: в списках его нет, а обращение по id отвечает как на пустое место.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret, hash_password
from app.main import app
from app.models import (
    AlertChannel,
    AlertEvent,
    CapRule,
    IntegrationConnection,
    MetaAdAccount,
    MetaLaunch,
    MetaRule,
    MetaRuleGroup,
    MetaTemplate,
    Permission,
    Role,
    Status,
    User,
)
from app.services.meta_session import MetaSessionState, get_session_manager

PASSWORD = "scope-password"
PERMISSIONS = [
    "meta.view", "meta.launch", "salary.view", "utilities.view", "utilities.events",
]


@pytest.fixture
async def stage(database):
    suffix = uuid.uuid4().hex[:6]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        workspace_id = admin.workspace_id
        permissions = list(
            (await db.execute(select(Permission).where(Permission.code.in_(PERMISSIONS)))).scalars()
        )
        role = Role(workspace_id=workspace_id, name=f"Scope own {suffix}", description="",
                    data_scope="own", permissions=permissions)
        db.add(role)
        await db.flush()
        channel = AlertChannel(workspace_id=workspace_id, name=f"Scope {suffix}",
                               chat_id="-100777", status=Status.active)
        db.add(channel)
        await db.flush()
        made: dict = {"role": role.id, "channel": channel.id, "people": {}}
        for key in ("mine", "other"):
            person = User(workspace_id=workspace_id, role_id=role.id, name=f"Scope {key} {suffix}",
                          login=f"scope{key}{suffix}", password_hash=hash_password(PASSWORD))
            db.add(person)
            await db.flush()
            connection = IntegrationConnection(
                workspace_id=workspace_id, owner_id=person.id, name=f"Meta {key} {suffix}",
                kind="meta", base_url="https://graph.facebook.com/v23.0",
                api_key_encrypted=encrypt_secret("scope-token-long-enough-for-meta"),
            )
            db.add(connection)
            await db.flush()
            account = MetaAdAccount(workspace_id=workspace_id, connection_id=connection.id,
                                    external_id=f"act_{key}{suffix}", name=f"Cab {key}",
                                    currency="USD")
            template = MetaTemplate(workspace_id=workspace_id, name=f"Польша языки {suffix}",
                                    created_by_id=person.id, settings={})
            rule = MetaRule(workspace_id=workspace_id, name=f"STOP {key} {suffix}",
                            created_by_id=person.id)
            group = MetaRuleGroup(workspace_id=workspace_id, name=f"Group {key} {suffix}",
                                  created_by_id=person.id)
            cap = CapRule(workspace_id=workspace_id, name=f"CAP {key} {suffix}",
                          channel_id=channel.id, user_id=person.id, metric="sales",
                          limit_value=50, period="day", notify_at=[100])
            db.add_all([account, template, rule, group, cap])
            await db.flush()
            rule.group_id = group.id
            event = AlertEvent(workspace_id=workspace_id, cap_rule_id=cap.id,
                               rule_name=cap.name, message=f"CAP {key} {suffix} 100%")
            launch = MetaLaunch(workspace_id=workspace_id, account_id=account.id,
                                owner_id=person.id, name=f"Launch {key} {suffix}",
                                daily_budget=Decimal("20"), link_url="https://track.example/x")
            db.add_all([event, launch])
            await db.flush()
            made["people"][key] = {
                "id": person.id, "login": person.login, "connection": connection.id,
                "account": account.id, "template": template.id, "rule": rule.id,
                "group": group.id, "cap": cap.id, "event": event.id, "launch": launch.id,
            }
        await db.commit()
    yield made
    async with SessionLocal() as db:
        for row in made["people"].values():
            await db.execute(delete(AlertEvent).where(AlertEvent.id == row["event"]))
            await db.execute(delete(CapRule).where(CapRule.id == row["cap"]))
            await db.execute(delete(MetaLaunch).where(MetaLaunch.owner_id == row["id"]))
            await db.execute(delete(MetaRule).where(MetaRule.created_by_id == row["id"]))
            await db.execute(delete(MetaRuleGroup).where(MetaRuleGroup.created_by_id == row["id"]))
            await db.execute(delete(MetaTemplate).where(MetaTemplate.created_by_id == row["id"]))
            await db.execute(delete(MetaAdAccount).where(MetaAdAccount.id == row["account"]))
            await db.execute(
                delete(IntegrationConnection).where(IntegrationConnection.id == row["connection"])
            )
        await db.execute(delete(User).where(User.id.in_([r["id"] for r in made["people"].values()])))
        await db.execute(delete(AlertChannel).where(AlertChannel.id == made["channel"]))
        await db.execute(delete(Role).where(Role.id == made["role"]))
        await db.commit()


def _login(client: TestClient, login: str) -> None:
    signed = client.post("/api/v1/auth/login", json={"login": login, "password": PASSWORD})
    assert signed.status_code == 200, signed.text


def test_bundles_belong_to_their_author(stage) -> None:
    mine, other = stage["people"]["mine"], stage["people"]["other"]
    with TestClient(app) as client:
        _login(client, mine["login"])
        listed = {row["id"] for row in client.get("/api/v1/meta/templates").json()["items"]}
        assert str(mine["template"]) in listed
        assert str(other["template"]) not in listed
        foreign = f"/api/v1/meta/templates/{other['template']}"
        assert client.patch(foreign, json={"name": "Захват"}).status_code == 404
        assert client.delete(foreign).status_code == 404
        # Одинаковое название у разных владельцев — не конфликт и не утечка.
        own_again = client.post("/api/v1/meta/templates", json={"name": "Уникальная у меня"})
        assert own_again.status_code == 201, own_again.text
        same = client.post("/api/v1/meta/templates", json={"name": "Уникальная у меня"})
        assert same.status_code == 422

    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login", json={"login": "admin", "password": "test-password"}
        ).status_code == 200
        listed = {row["id"] for row in client.get("/api/v1/meta/templates").json()["items"]}
        assert {str(mine["template"]), str(other["template"])} <= listed


def test_launch_cannot_reach_foreign_objects(stage) -> None:
    mine, other = stage["people"]["mine"], stage["people"]["other"]
    base = {
        "name": "Scope launch", "account_id": str(mine["account"]), "daily_budget": "40.00",
        "link_url": "https://track.example/click",
    }
    with TestClient(app) as client:
        _login(client, mine["login"])
        launches = "/api/v1/meta/launches"
        assert client.post(
            launches, json={**base, "template_id": str(other["template"])}
        ).status_code == 404
        assert client.post(launches, json={**base, "owner_id": str(other["id"])}).status_code == 422
        assert client.post(launches, json={**base, "rule_ids": [str(other["rule"])]}).status_code == 422
        assert client.post(
            launches, json={**base, "account_id": str(other["account"])}
        ).status_code == 404
        created = client.post(launches, json={
            **base, "template_id": str(mine["template"]), "rule_ids": [str(mine["rule"])],
        })
        assert created.status_code == 201, created.text
        moved = client.patch(
            f"{launches}/{created.json()['id']}", json={"account_id": str(other["account"])}
        )
        assert moved.status_code == 404


def test_rule_groups_and_cap_events_follow_the_scope(stage) -> None:
    mine, other = stage["people"]["mine"], stage["people"]["other"]
    with TestClient(app) as client:
        _login(client, mine["login"])
        groups = {row["id"] for row in client.get("/api/v1/meta/rule-groups").json()["items"]}
        assert str(mine["group"]) in groups and str(other["group"]) not in groups
        foreign = f"/api/v1/meta/rule-groups/{other['group']}"
        assert client.patch(foreign, json={"rule_ids": []}).status_code == 404
        assert client.delete(foreign).status_code == 404
        # Свою группу нельзя набить чужими правилами.
        client.patch(
            f"/api/v1/meta/rule-groups/{mine['group']}",
            json={"rule_ids": [str(mine["rule"]), str(other["rule"])]},
        )
        events = client.get("/api/v1/utilities/events", params={"limit": 200}).json()["items"]
        ids = {row["id"] for row in events}
        assert str(mine["event"]) in ids and str(other["event"]) not in ids

    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login", json={"login": "admin", "password": "test-password"}
        ).status_code == 200
        groups = {row["id"]: row for row in client.get("/api/v1/meta/rule-groups").json()["items"]}
        # Чужое правило осталось в своей группе и не переехало в мою.
        assert [rule["id"] for rule in groups[str(other["group"])]["rules"]] == [str(other["rule"])]
        assert [rule["id"] for rule in groups[str(mine["group"])]["rules"]] == [str(mine["rule"])]


def test_browser_session_is_private(stage) -> None:
    mine, other = stage["people"]["mine"], stage["people"]["other"]
    session_id = f"meta_scope_{uuid.uuid4().hex[:8]}"
    manager = get_session_manager()
    manager.sessions[session_id] = MetaSessionState(id=session_id, owner_id=other["id"])
    try:
        with TestClient(app) as client:
            _login(client, mine["login"])
            path = f"/api/v1/meta/session/{session_id}"
            assert client.get(f"{path}/status").status_code == 404
            assert client.post(f"{path}/token").status_code == 404
            assert client.post(f"{path}/close").status_code == 404
            assert session_id in manager.sessions
            # Тот же клиент: при остановке приложения менеджер закрывает все сессии.
            _login(client, other["login"])
            assert client.get(f"/api/v1/meta/session/{session_id}/status").status_code == 200
    finally:
        manager.sessions.pop(session_id, None)


def test_payroll_shows_only_visible_people(stage, monkeypatch) -> None:
    from app.api.routers import salary as salary_router

    mine, other = stage["people"]["mine"], stage["people"]["other"]

    async def fake_payroll(db, workspace_id, year, month):
        rows = [
            {"user_id": str(person["id"]), "payout": Decimal(amount)}
            for person, amount in ((mine, "100"), (other, "900"))
        ]
        return {"year": year, "month": month, "rows": rows, "total": Decimal("1000"), "people": 2}

    monkeypatch.setattr(salary_router, "payroll", fake_payroll)
    with TestClient(app) as client:
        _login(client, mine["login"])
        payload = client.get("/api/v1/salary/calculate").json()
    assert [row["user_id"] for row in payload["rows"]] == [str(mine["id"])]
    assert payload["people"] == 1
    assert Decimal(str(payload["total"])) == Decimal("100")
