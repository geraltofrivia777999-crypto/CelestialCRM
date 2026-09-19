import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret, hash_password
from app.main import app
from app.models import (
    IntegrationConnection,
    MetaAdAccount,
    MetaEntity,
    Permission,
    Role,
    User,
)
from app.services.meta import MetaError

ACTIONS = "/api/v1/meta/entities/actions"


class FakeClient:
    """Клиент Meta, который только записывает, что его попросили сделать."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def set_status(self, external_id: str, status: str) -> dict:
        self.calls.append(("status", external_id, status))
        return {"success": True}

    async def update_object(self, external_id: str, data: dict) -> dict:
        if external_id == "cmp-broken":
            raise MetaError("Meta отклонила изменение")
        self.calls.append(("update", external_id, data))
        return {"success": True}

    async def delete_object(self, external_id: str) -> dict:
        self.calls.append(("delete", external_id))
        return {"success": True}

    async def copy_object(self, external_id: str, deep: bool) -> dict:
        self.calls.append(("copy", external_id, deep))
        return {"copied_campaign_id": external_id + "-copy"}

    async def entities_by_ids(self, level: str, ids: list[str]) -> list[dict]:
        return [
            {"id": value, "daily_budget": "2500", "bid_strategy": "COST_CAP", "bid_amount": "150",
             "effective_status": "ACTIVE"}
            for value in ids
        ]


@pytest.fixture
async def structure(database, monkeypatch):
    from app.api.routers import meta as meta_router

    fake = FakeClient()

    async def fake_client_for(connection, db=None):
        return fake

    monkeypatch.setattr(meta_router, "client_for", fake_client_for)
    suffix = uuid.uuid4().hex[:6]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            owner_id=admin.id,
            name=f"Meta actions {suffix}",
            kind="meta",
            base_url="https://graph.facebook.com/v23.0",
            api_key_encrypted=encrypt_secret("meta-actions-token-long-enough"),
        )
        db.add(connection)
        await db.flush()
        account = MetaAdAccount(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id=f"act_{suffix}",
            name="Actions cabinet",
            currency="USD",
            owner_id=admin.id,
        )
        db.add(account)
        await db.flush()
        for external_id, name in (("cmp-1", "Campaign one"), ("cmp-broken", "Broken")):
            db.add(MetaEntity(
                workspace_id=admin.workspace_id,
                connection_id=connection.id,
                account_id=account.id,
                level="campaign",
                external_id=external_id,
                name=name,
                effective_status="ACTIVE",
                daily_budget=Decimal("10"),
                external_payload={},
            ))
        await db.commit()
        made = {"connection": connection.id, "account": account.id}
    yield fake, made
    async with SessionLocal() as db:
        await db.execute(delete(MetaEntity).where(MetaEntity.connection_id == made["connection"]))
        await db.execute(delete(MetaAdAccount).where(MetaAdAccount.id == made["account"]))
        await db.execute(
            delete(IntegrationConnection).where(IntegrationConnection.id == made["connection"])
        )
        await db.commit()


async def _entity(external_id: str, connection_id) -> MetaEntity:
    async with SessionLocal() as db:
        return await db.scalar(
            select(MetaEntity).where(
                MetaEntity.connection_id == connection_id,
                MetaEntity.external_id == external_id,
            )
        )


def _admin() -> TestClient:
    client = TestClient(app)
    client.__enter__()
    assert client.post(
        "/api/v1/auth/login", json={"login": "admin", "password": "test-password"}
    ).status_code == 200
    return client


async def test_actions_reach_meta_and_update_the_structure(structure) -> None:
    fake, made = structure
    client = _admin()
    try:
        paused = client.post(
            ACTIONS, json={"level": "campaigns", "action": "pause", "items": [{"id": "cmp-1"}]}
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["done"] == 1
        assert ("status", "cmp-1", "PAUSED") in fake.calls
        assert (await _entity("cmp-1", made["connection"])).effective_status == "PAUSED"

        renamed = client.post(ACTIONS, json={
            "level": "campaigns", "action": "rename",
            "items": [{"id": "cmp-1", "name": "Renamed"}],
        })
        assert renamed.json()["done"] == 1
        assert (await _entity("cmp-1", made["connection"])).name == "Renamed"

        budget = client.post(ACTIONS, json={
            "level": "campaigns", "action": "budget",
            "items": [
                {"id": "cmp-1", "daily_budget": "25.5", "bid_strategy": "LOWEST_COST_WITH_BID_CAP",
                 "bid_amount": "1.2"},
                {"id": "cmp-broken", "daily_budget": "5"},
                {"id": "cmp-1", "bid_strategy": "COST_CAP"},
                {"id": "unknown"},
            ],
        })
        rows = budget.json()["results"]
        assert rows[0]["ok"] is True
        assert ("update", "cmp-1", {
            "daily_budget": "2550", "bid_strategy": "LOWEST_COST_WITH_BID_CAP", "bid_amount": "120",
        }) in fake.calls
        assert rows[1] == {"id": "cmp-broken", "ok": False, "error": "Meta отклонила изменение"}
        assert rows[2]["ok"] is False and "размер ставки" in rows[2]["error"]
        assert rows[3]["ok"] is False
        assert (await _entity("cmp-1", made["connection"])).daily_budget == Decimal("25.50")

        copied = client.post(
            ACTIONS, json={"level": "campaigns", "action": "duplicate", "items": [{"id": "cmp-1"}]}
        )
        assert copied.json()["results"][0]["new_id"] == "cmp-1-copy"
        assert ("copy", "cmp-1", True) in fake.calls

        refused = client.post(
            ACTIONS, json={"level": "ads", "action": "budget", "items": [{"id": "cmp-1"}]}
        )
        assert refused.status_code == 422

        details = client.get(
            "/api/v1/meta/entities/details", params={"level": "campaigns", "ids": ["cmp-1"]}
        ).json()
        assert details["items"][0]["daily_budget"] == 25.0
        assert details["items"][0]["bid_strategy"] == "COST_CAP"
        assert details["items"][0]["bid_amount"] == 1.5
        assert "LOWEST_COST_WITHOUT_CAP" in details["bid_strategies"]
    finally:
        client.__exit__(None, None, None)


async def test_actions_need_the_launch_permission(structure) -> None:
    suffix = uuid.uuid4().hex[:6]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        view = await db.scalar(select(Permission).where(Permission.code == "meta.view"))
        role = Role(workspace_id=admin.workspace_id, name=f"Meta viewer {suffix}",
                    description="", permissions=[view])
        db.add(role)
        await db.flush()
        user = User(workspace_id=admin.workspace_id, role_id=role.id, name=f"viewer{suffix}",
                    login=f"viewer{suffix}", password_hash=hash_password("viewer-password"))
        db.add(user)
        await db.commit()
        ids = (user.id, role.id)
    try:
        with TestClient(app) as client:
            assert client.post("/api/v1/auth/login", json={
                "login": f"viewer{suffix}", "password": "viewer-password",
            }).status_code == 200
            response = client.post(
                ACTIONS, json={"level": "campaigns", "action": "pause", "items": [{"id": "cmp-1"}]}
            )
            assert response.status_code == 403
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == ids[0]))
            await db.execute(delete(Role).where(Role.id == ids[1]))
            await db.commit()
