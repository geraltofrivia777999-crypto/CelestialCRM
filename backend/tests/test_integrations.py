import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models import (
    IntegrationConnection,
    MediaRecord,
    Offer,
    User,
)
from app.services.keitaro import KeitaroClient, KeitaroError
from tests.test_media_finance import _admin_client

# The Keitaro tests sync "the first connection in the table", so every connection
# created here is either deleted by the endpoint under test or by the fixture.
DISPOSABLE = "integrations-disposable"


@pytest.fixture
async def disposable_connection(database):
    """A connection nothing else depends on, plus the ids needed to load it up."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Disposable tracker",
            base_url="https://disposable.example",
            api_key_encrypted=encrypt_secret("test-key"),
        )
        db.add(connection)
        await db.flush()
        offer = Offer(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id=DISPOSABLE,
            name="Disposable offer",
        )
        db.add(offer)
        await db.commit()
        ids = {
            "connection": str(connection.id),
            "offer": str(offer.id),
            "workspace": admin.workspace_id,
            "admin": str(admin.id),
        }

    yield ids

    async with SessionLocal() as db:
        await db.execute(
            delete(MediaRecord).where(MediaRecord.offer_id == uuid.UUID(ids["offer"]))
        )
        await db.execute(delete(Offer).where(Offer.external_id == DISPOSABLE))
        await db.execute(
            delete(IntegrationConnection).where(
                IntegrationConnection.id == uuid.UUID(ids["connection"])
            )
        )
        await db.commit()


async def test_connection_can_be_renamed_and_switched_off(disposable_connection) -> None:
    connection_id = disposable_connection["connection"]
    with _admin_client() as client:
        response = client.patch(
            f"/api/v1/integrations/keitaro/{connection_id}",
            json={"name": "Renamed tracker", "status": "inactive"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed tracker"
        # An inactive connection is what stops the scheduler from picking it up.
        assert response.json()["status"] == "inactive"

    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, uuid.UUID(connection_id))
        # The key was not part of the request and must survive it.
        assert connection.api_key_encrypted


async def test_connection_delete_removes_it(disposable_connection) -> None:
    connection_id = disposable_connection["connection"]
    with _admin_client() as client:
        response = client.delete(f"/api/v1/integrations/keitaro/{connection_id}")
        assert response.status_code == 200
        assert response.json()["deleted"] == connection_id
        assert client.delete(
            f"/api/v1/integrations/keitaro/{connection_id}"
        ).status_code == 404

    async with SessionLocal() as db:
        assert await db.get(IntegrationConnection, uuid.UUID(connection_id)) is None


async def test_connection_delete_refuses_to_drop_manual_records(
    disposable_connection,
) -> None:
    connection_id = disposable_connection["connection"]
    async with SessionLocal() as db:
        db.add(
            MediaRecord(
                workspace_id=disposable_connection["workspace"],
                record_date=date(2026, 4, 1),
                buyer_id=uuid.UUID(disposable_connection["admin"]),
                offer_id=uuid.UUID(disposable_connection["offer"]),
            )
        )
        await db.commit()

    with _admin_client() as client:
        blocked = client.delete(f"/api/v1/integrations/keitaro/{connection_id}")
        assert blocked.status_code == 409
        assert "Медиаборд — 1" in blocked.json()["error"]["message"]

        purged = client.delete(f"/api/v1/integrations/keitaro/{connection_id}?purge=true")
        assert purged.status_code == 200
        assert purged.json()["media_records_removed"] == 1

    async with SessionLocal() as db:
        assert await db.get(IntegrationConnection, uuid.UUID(connection_id)) is None
        remaining = await db.scalar(
            select(MediaRecord).where(
                MediaRecord.offer_id == uuid.UUID(disposable_connection["offer"])
            )
        )
        assert remaining is None


async def test_check_reports_what_keitaro_said(disposable_connection, monkeypatch) -> None:
    """A tracker that answers 404 must produce advice, not a 500."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    original_init = KeitaroClient.__init__

    def patched_init(self, base_url, api_key, **kwargs):
        kwargs.setdefault("max_attempts", 1)
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, base_url, api_key, **kwargs)

    monkeypatch.setattr(KeitaroClient, "__init__", patched_init)

    with _admin_client() as client:
        response = client.post(
            f"/api/v1/integrations/keitaro/{disposable_connection['connection']}/check"
        )
        assert response.status_code == 422
        assert "домен для трафика" in response.json()["error"]["message"]


async def test_status_hints_replace_the_bare_http_code() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Access denied"})

    client = KeitaroClient(
        "https://tracker.example",
        "secret-must-not-leak",
        max_attempts=1,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(KeitaroError) as caught:
        await client.check()
    message = str(caught.value)
    assert "API-ключ" in message
    assert "Access denied" in message
    assert "secret-must-not-leak" not in message
