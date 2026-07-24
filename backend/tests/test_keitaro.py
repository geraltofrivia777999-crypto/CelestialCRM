import json
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models import (
    IntegrationConnection,
    KeitaroCampaign,
    KeitaroStatDaily,
    MediaRecord,
    Offer,
    Partner,
    SyncRun,
    SyncStatus,
    User,
)
from app.schemas import ConnectionCreate
from app.services.keitaro import (
    REPORT_DIMENSIONS,
    REPORT_MEASURES,
    KeitaroClient,
    KeitaroError,
    stat_dimension_key,
)
from app.services.keitaro_sync import KeitaroSyncEngine


async def test_report_uses_current_keitaro_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"rows": [{"clicks": 10}]})

    client = KeitaroClient(
        "https://tracker.example",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    rows = await client.report(date(2025, 6, 1), date(2025, 6, 2))

    assert rows == [{"clicks": 10}]
    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/admin_api/v1/report/build"
    assert payload["dimensions"] == REPORT_DIMENSIONS
    assert payload["measures"] == REPORT_MEASURES
    assert "columns" not in payload
    assert "metrics" not in payload


async def test_keitaro_error_is_safe_and_classified() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Access denied"})

    client = KeitaroClient(
        "https://tracker.example",
        "secret-must-not-leak",
        max_attempts=1,
        transport=httpx.MockTransport(handler),
    )

    try:
        await client.check()
    except KeitaroError as exc:
        assert exc.status_code == 401
        assert not exc.retryable
        assert "secret-must-not-leak" not in str(exc)
    else:
        raise AssertionError("KeitaroError was not raised")


def test_stat_dimension_key_covers_campaign_geo_and_subs() -> None:
    base = {
        "day": "2025-06-01",
        "campaign_id": 1,
        "offer_id": 2,
        "country_code": "DE",
        "sub_id_1": "buyer",
    }
    assert stat_dimension_key(base) == stat_dimension_key(dict(base))
    assert stat_dimension_key(base) != stat_dimension_key({**base, "campaign_id": 3})
    assert stat_dimension_key(base) != stat_dimension_key({**base, "country_code": "IT"})
    assert stat_dimension_key(base) != stat_dimension_key({**base, "sub_id_2": "creative"})


def test_connection_rejects_documentation_url() -> None:
    with pytest.raises(ValueError, match="адрес документации"):
        ConnectionCreate(
            name="Docs",
            base_url="https://admin-api.docs.keitaro.io/",
            api_key="not-a-real-key",
        )


def test_connection_accepts_tracker_url() -> None:
    connection = ConnectionCreate(
        name="Main",
        base_url="https://tracker.example.com/",
        api_key="not-a-real-key",
    )
    assert connection.base_url == "https://tracker.example.com"


class FakeKeitaroClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def affiliate_networks(self) -> list[dict]:
        return [{"id": 10, "name": "Network", "state": "active"}]

    async def groups(self, resource_type: str) -> list[dict]:
        return [{"id": 20 if resource_type == "offers" else 30, "name": resource_type}]

    async def offers(self) -> list[dict]:
        return [
            {
                "id": 40,
                "name": "Offer",
                "group_id": 20,
                "affiliate_network_id": 10,
                "country": ["DE"],
                "state": "active",
            }
        ]

    async def campaigns(self) -> list[dict]:
        return [
            {
                "id": 50,
                "name": "Campaign",
                "group_id": 30,
                "state": "active",
                "cost_type": "CPC",
            }
        ]

    async def report(self, start: date, _end: date, *, timezone: str) -> list[dict]:
        assert timezone == "UTC"
        return [
            {
                "day": start.isoformat(),
                "campaign_id": 50,
                "offer_id": 40,
                "country_code": "DE",
                "sub_id_1": "admin",
                "clicks": 100,
                "campaign_unique_clicks": 80,
                "conversions": 5,
                "leads": 10,
                "sales": 4,
                "rejected": 1,
                "cost": "50.25",
                "revenue": "120.50",
            }
        ]


async def test_sync_engine_upserts_references_stats_and_media(database) -> None:
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Test Keitaro",
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("test-key"),
            timezone="UTC",
            buyer_sub_id=1,
            lookback_days=1,
        )
        db.add(connection)
        await db.flush()
        run = SyncRun(connection_id=connection.id, mode="incremental")
        db.add(run)
        await db.commit()
        connection_id = str(connection.id)
        run_id = str(run.id)

    engine = KeitaroSyncEngine(SessionLocal, client_factory=FakeKeitaroClient)
    result = await engine.run(connection_id, run_id, "incremental")

    assert result["status"] == "success"
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Partner)) == 1
        assert await db.scalar(select(func.count()).select_from(Offer)) == 1
        assert await db.scalar(select(func.count()).select_from(KeitaroCampaign)) == 1
        assert await db.scalar(select(func.count()).select_from(KeitaroStatDaily)) == 2
        media = await db.scalar(select(MediaRecord).order_by(MediaRecord.record_date.desc()))
        assert media.installs == 80
        assert media.registrations == 10
        assert media.ftd == 4
        assert str(media.revenue) == "120.5000"
        completed = await db.get(SyncRun, uuid.UUID(run_id))
        assert completed.status == SyncStatus.success
        assert completed.progress_pct == 100
