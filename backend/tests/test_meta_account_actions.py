import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models import IntegrationConnection, MetaAdAccount, User
from app.services.meta import MetaError
from tests.test_meta_entity_actions import _admin


class FakeClient:
    """Кабинет в Meta: хранит название и лимит так, как их отдаёт Graph API."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.live = {"name": "Old", "currency": "USD", "spend_cap": "0", "amount_spent": "1234"}

    async def update_object(self, external_id: str, data: dict) -> dict:
        self.calls.append(("update", external_id, data))
        if "name" in data:
            self.live["name"] = data["name"]
        if "spend_cap" in data:
            # Пишем в долларах, читаем в центах — как у Meta.
            self.live["spend_cap"] = str(int(Decimal(data["spend_cap"]) * 100))
        if data.get("spend_cap_action") == "delete":
            self.live["spend_cap"] = "0"
        if data.get("spend_cap_action") == "reset":
            self.live["amount_spent"] = "0"
        return {"success": True}

    async def object_fields(self, external_id: str, fields: list[str]) -> dict:
        return {key: self.live[key] for key in fields}

    async def pixels(self, account_external_id: str) -> list[dict]:
        return [{"id": "px-1", "name": "Main pixel"}]

    async def create_pixel(self, account_external_id: str, name: str) -> dict:
        if name == "broken":
            raise MetaError("Лимит пикселей исчерпан")
        self.calls.append(("pixel", account_external_id, name))
        return {"id": "px-new"}


@pytest.fixture
async def cabinet(database, monkeypatch):
    from app.api.routers import meta as meta_router

    fake = FakeClient()

    async def fake_client_for(connection, db=None):
        return fake

    monkeypatch.setattr(meta_router, "client_for", fake_client_for)
    suffix = uuid.uuid4().hex[:6]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id, owner_id=admin.id, name=f"Cabinet {suffix}",
            kind="meta", base_url="https://graph.facebook.com/v23.0",
            api_key_encrypted=encrypt_secret("meta-cabinet-token-long-enough"),
        )
        db.add(connection)
        await db.flush()
        account = MetaAdAccount(
            workspace_id=admin.workspace_id, connection_id=connection.id,
            external_id=f"act_{suffix}", name="Old", currency="USD", owner_id=admin.id,
        )
        db.add(account)
        await db.commit()
        made = {"connection": connection.id, "account": account.id, "external": account.external_id}
    yield fake, made
    async with SessionLocal() as db:
        await db.execute(delete(MetaAdAccount).where(MetaAdAccount.id == made["account"]))
        await db.execute(
            delete(IntegrationConnection).where(IntegrationConnection.id == made["connection"])
        )
        await db.commit()


async def _account(account_id) -> MetaAdAccount:
    async with SessionLocal() as db:
        return await db.get(MetaAdAccount, account_id)


async def test_account_actions_reach_meta(cabinet) -> None:
    fake, made = cabinet
    base = f"/api/v1/meta/accounts/{made['account']}"
    client = _admin()
    try:
        billing = client.get(base + "/billing")
        assert billing.status_code == 200, billing.text
        assert billing.json()["spend_cap"] is None
        assert billing.json()["amount_spent"] == 12.34
        assert billing.json()["pixels"] == [{"id": "px-1", "name": "Main pixel"}]

        renamed = client.post(base + "/actions", json={"action": "rename", "name": " New name "})
        assert renamed.status_code == 200, renamed.text
        assert ("update", made["external"], {"name": "New name"}) in fake.calls
        assert (await _account(made["account"])).name == "New name"

        capped = client.post(base + "/actions", json={"action": "spend_cap", "spend_cap": "150.5"})
        assert capped.status_code == 200, capped.text
        assert ("update", made["external"], {"spend_cap": "150.5"}) in fake.calls
        assert capped.json()["spend_cap"] == 150.5 and "warning" not in capped.json()
        assert (await _account(made["account"])).spend_cap == Decimal("150.50")

        reset = client.post(
            base + "/actions", json={"action": "spend_cap", "spend_cap_action": "reset"}
        )
        assert reset.json()["amount_spent"] == 0
        removed = client.post(
            base + "/actions", json={"action": "spend_cap", "spend_cap_action": "delete"}
        )
        assert removed.json()["spend_cap"] is None
        assert (await _account(made["account"])).spend_cap is None

        pixel = client.post(base + "/actions", json={"action": "pixel", "name": "Leads"})
        assert pixel.json() == {"ok": True, "pixel_id": "px-new"}
        assert ("pixel", made["external"], "Leads") in fake.calls

        failed = client.post(base + "/actions", json={"action": "pixel", "name": "broken"})
        assert failed.status_code == 422 and "Лимит пикселей" in failed.json()["error"]["message"]
        empty = client.post(base + "/actions", json={"action": "rename", "name": "  "})
        assert empty.status_code == 422
        missing = client.post(base + "/actions", json={"action": "spend_cap"})
        assert missing.status_code == 422
    finally:
        client.__exit__(None, None, None)


async def test_account_actions_hidden_account_is_not_found(cabinet) -> None:
    client = _admin()
    try:
        response = client.post(
            f"/api/v1/meta/accounts/{uuid.uuid4()}/actions", json={"action": "rename", "name": "x"}
        )
        assert response.status_code == 404
    finally:
        client.__exit__(None, None, None)
