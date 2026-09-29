import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.clock import business_today
from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.main import app
from app.models import (
    IntegrationConnection,
    KeitaroCampaign,
    KeitaroStatDaily,
    MediaRecord,
    Offer,
    OfferStatus,
    Partner,
    Status,
    SyncRun,
    SyncStatus,
    User,
)
from app.schemas import ConnectionCreate
from app.services.keitaro import (
    CONVERSION_COLUMNS,
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


async def test_conversions_use_supported_columns_and_fetch_every_page() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        offset = payload["offset"]
        page = {
            0: [{"event_id": "e1"}, {"event_id": "e2"}],
            2: [{"event_id": "e3"}],
        }.get(offset, [])
        return httpx.Response(200, json={"rows": page, "total": 3})

    client = KeitaroClient(
        "https://tracker.example",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    rows = await client.conversions(date(2026, 9, 1), date(2026, 9, 2), limit=2)

    assert [row["event_id"] for row in rows] == ["e1", "e2", "e3"]
    payloads = [json.loads(request.content) for request in requests]
    assert [payload["offset"] for payload in payloads] == [0, 2]
    assert all(payload["columns"] == CONVERSION_COLUMNS for payload in payloads)
    assert "payout" not in CONVERSION_COLUMNS


async def test_click_times_are_looked_up_in_one_batch() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "rows": [
                    {"sub_id": "click-a", "datetime": "2026-09-03 15:19:40"},
                    {"sub_id": "click-b", "datetime": "2026-09-03 15:20:10"},
                ],
                "total": 2,
            },
        )

    client = KeitaroClient(
        "https://tracker.example",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    result = await client.click_times(
        ["click-a", "click-b", "click-a"],
        start=date(2026, 9, 2),
        end=date(2026, 9, 3),
    )

    assert result == {
        "click-a": "2026-09-03 15:19:40",
        "click-b": "2026-09-03 15:20:10",
    }
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/admin_api/v1/clicks/log"
    assert payload["filters"][0] == {
        "name": "sub_id",
        "operator": "IN_LIST",
        "expression": ["click-a", "click-b"],
    }
    assert payload["range"] == {
        "from": "2026-09-02",
        "to": "2026-09-03",
        "timezone": "Europe/Moscow",
    }


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
        # В общем прогоне другие модули уже могли создать ручные офферы.
        # Синхронизация отвечает только за строки своего подключения.
        assert await db.scalar(
            select(func.count()).select_from(Offer).where(
                Offer.connection_id == uuid.UUID(connection_id)
            )
        ) == 1
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


async def test_manual_catalog_status_survives_sync(database) -> None:
    async with SessionLocal() as db:
        connection = await db.scalar(
            select(IntegrationConnection).where(IntegrationConnection.name == "Test Keitaro")
        )
        offer = await db.scalar(select(Offer).where(Offer.connection_id == connection.id))
        partner = await db.scalar(
            select(Partner).where(Partner.connection_id == connection.id)
        )
        assert offer is not None
        assert partner is not None
        offer_id = str(offer.id)
        partner_id = str(partner.id)
        connection_id = str(offer.connection_id)

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        )
        assert login.status_code == 200
        offer_update = client.patch(
            f"/api/v1/offers/{offer_id}/status",
            json={"status": "hold"},
        )
        partner_update = client.patch(
            f"/api/v1/partners/{partner_id}/status",
            json={"status": "inactive"},
        )
        assert offer_update.status_code == 200
        assert partner_update.status_code == 200
        assert offer_update.json()["status"] == "hold"
        assert partner_update.json()["status_overridden"] is True

    async with SessionLocal() as db:
        run = SyncRun(connection_id=uuid.UUID(connection_id), mode="incremental")
        db.add(run)
        await db.commit()
        run_id = str(run.id)

    engine = KeitaroSyncEngine(SessionLocal, client_factory=FakeKeitaroClient)
    result = await engine.run(connection_id, run_id, "incremental")
    assert result["status"] == "success"

    async with SessionLocal() as db:
        offer = await db.get(Offer, uuid.UUID(offer_id))
        partner = await db.get(Partner, uuid.UUID(partner_id))
        assert offer is not None
        assert partner is not None
        # The workflow status is ours; Keitaro only ever writes `keitaro_state`.
        assert offer.status == OfferStatus.hold
        assert offer.keitaro_state == Status.active
        assert partner.status == Status.inactive
        assert partner.status_overridden is True


async def test_sync_keeps_manual_media_values_and_spend(database) -> None:
    async with SessionLocal() as db:
        connection = await db.scalar(
            select(IntegrationConnection).where(IntegrationConnection.name == "Test Keitaro")
        )
        media = await db.scalar(
            select(MediaRecord)
            .join(Offer, Offer.id == MediaRecord.offer_id)
            .where(Offer.connection_id == connection.id)
            .order_by(MediaRecord.record_date.desc())
        )
        assert media is not None
        media.manual_fields = ["installs", "revenue"]
        media.installs = 4242
        media.revenue = Decimal("999")
        media.registrations = 0
        # Owned by the "Агенты и платёжки" block, never by Keitaro (ТЗ 2.4.4).
        media.spend_calculated = Decimal("77.5")
        media_id = media.id
        connection_id = str(connection.id)
        await db.commit()

    async with SessionLocal() as db:
        run = SyncRun(connection_id=uuid.UUID(connection_id), mode="incremental")
        db.add(run)
        await db.commit()
        run_id = str(run.id)

    engine = KeitaroSyncEngine(SessionLocal, client_factory=FakeKeitaroClient)
    assert (await engine.run(connection_id, run_id, "incremental"))["status"] == "success"

    async with SessionLocal() as db:
        media = await db.get(MediaRecord, media_id)
        assert media.installs == 4242
        assert media.revenue == Decimal("999.0000")
        assert media.spend_calculated == Decimal("77.5000")
        # Unpinned fields still track Keitaro.
        assert media.registrations == 10
        assert media.external_payload["keitaro_cost"] == "50.25"


def test_sidebar_status_uses_real_keitaro_state(database) -> None:
    with TestClient(app) as client:
        anonymous = client.get("/api/v1/integrations/keitaro/sidebar-status")
        assert anonymous.status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        )
        assert login.status_code == 200
        response = client.get("/api/v1/integrations/keitaro/sidebar-status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["configured"] is True
        assert payload["state"] == "active"
        assert payload["connection_id"]
        assert payload["last_sync_at"]


class EmptyKeitaroClient(FakeKeitaroClient):
    """Трекер, в котором статистики за любой день больше нет."""

    async def report(self, start: date, _end: date, *, timezone: str) -> list[dict]:
        return []


class ZeroMetricOfferClient(EmptyKeitaroClient):
    """Активный оффер есть в справочнике, но отсутствует в отчёте за день."""

    async def groups(self, resource_type: str) -> list[dict]:
        return [{"id": 20 if resource_type == "offers" else 30, "name": "ZERO"}]

    async def offers(self) -> list[dict]:
        return [
            {
                "id": 4040,
                "name": "Zero metrics offer",
                "group_id": 20,
                "affiliate_network_id": 10,
                "country": ["DE"],
                "state": "active",
            }
        ]


async def test_sync_creates_media_row_for_active_offer_with_zero_metrics(database) -> None:
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        buyer = User(
            workspace_id=admin.workspace_id,
            role_id=admin.role_id,
            name="Zero Buyer",
            login="zero-buyer",
            password_hash="not-used",
            keitaro_company_group="ZERO",
            status=Status.active,
        )
        db.add(buyer)
        await db.commit()
        buyer_id = buyer.id

    connection_id = await _keitaro_connection("Zero metrics Keitaro")
    run_id = await _new_run(connection_id, "incremental")
    engine = KeitaroSyncEngine(SessionLocal, client_factory=ZeroMetricOfferClient)
    assert (await engine.run(connection_id, run_id, "incremental"))["status"] == "success"

    async with SessionLocal() as db:
        media = await db.scalar(
            select(MediaRecord)
            .join(Offer, Offer.id == MediaRecord.offer_id)
            .where(
                Offer.connection_id == uuid.UUID(connection_id),
                Offer.external_id == "4040",
                MediaRecord.buyer_id == buyer_id,
                MediaRecord.record_date == business_today(),
            )
        )
        assert media is not None
        assert media.source == "keitaro"
        assert media.installs == 0
        assert media.registrations == 0
        assert media.ftd == 0
        assert media.revenue == Decimal("0")


async def _keitaro_connection(name: str) -> str:
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name=name,
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("test-key"),
            timezone="UTC",
            buyer_sub_id=1,
            lookback_days=1,
        )
        db.add(connection)
        await db.commit()
        return str(connection.id)


async def _new_run(connection_id: str, mode: str) -> str:
    async with SessionLocal() as db:
        run = SyncRun(connection_id=uuid.UUID(connection_id), mode=mode)
        db.add(run)
        await db.commit()
        return str(run.id)


def test_resync_window_matches_the_chosen_period() -> None:
    today = business_today()
    assert KeitaroSyncEngine._date_window("resync", {}, 30) == (
        today - timedelta(days=29),
        today,
    )
    assert KeitaroSyncEngine._date_window("resync", {}, 365) == (
        today - timedelta(days=364),
        today,
    )
    # Первичная загрузка нового подключения не меняется: по-прежнему 90 дней.
    assert KeitaroSyncEngine._date_window("backfill", {}) == (
        today - timedelta(days=89),
        today,
    )


async def test_resync_clears_what_keitaro_no_longer_reports(database) -> None:
    """Пересинхронизация убирает пропавшее из отчёта; обычная синхронизация — нет.

    Ручное поле записи Медиаборда при этом остаётся: это данные человека.
    """
    connection_id = await _keitaro_connection("Resync Keitaro")
    engine = KeitaroSyncEngine(SessionLocal, client_factory=FakeKeitaroClient)
    run_id = await _new_run(connection_id, "incremental")
    assert (await engine.run(connection_id, run_id, "incremental"))["status"] == "success"

    connection_uuid = uuid.UUID(connection_id)

    async def stats_count() -> int:
        async with SessionLocal() as db:
            return await db.scalar(
                select(func.count())
                .select_from(KeitaroStatDaily)
                .where(KeitaroStatDaily.connection_id == connection_uuid)
            )

    async def today_media() -> MediaRecord:
        async with SessionLocal() as db:
            return await db.scalar(
                select(MediaRecord)
                .join(Offer, Offer.id == MediaRecord.offer_id)
                .where(
                    Offer.connection_id == connection_uuid,
                    MediaRecord.record_date == business_today(),
                )
            )

    assert await stats_count() == 2
    async with SessionLocal() as db:
        media = await db.get(MediaRecord, (await today_media()).id)
        media.revenue = Decimal("999")
        media.manual_fields = ["revenue"]
        await db.commit()

    empty = KeitaroSyncEngine(SessionLocal, client_factory=EmptyKeitaroClient)
    run_id = await _new_run(connection_id, "incremental")
    assert (await empty.run(connection_id, run_id, "incremental"))["status"] == "success"
    assert await stats_count() == 2
    assert (await today_media()).installs == 80

    run_id = await _new_run(connection_id, "resync")
    result = await empty.run(connection_id, run_id, "resync", 30)
    assert result["status"] == "success"
    assert result["range_from"] == (business_today() - timedelta(days=29)).isoformat()
    assert await stats_count() == 0
    media = await today_media()
    assert media is not None
    assert media.installs == 0
    assert media.registrations == 0
    assert media.ftd == 0
    assert media.revenue == Decimal("999")
    assert media.external_payload["clicks"] == 0


async def test_resync_needs_a_supported_period(database, monkeypatch) -> None:
    from app.api.routers import integrations

    queued: list[tuple] = []
    monkeypatch.setattr(
        integrations.sync_keitaro_connection, "delay", lambda *args: queued.append(args)
    )
    connection_id = await _keitaro_connection("Resync API Keitaro")
    url = f"/api/v1/integrations/keitaro/{connection_id}/sync"
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        )
        assert login.status_code == 200
        assert client.post(url, params={"mode": "resync"}).status_code == 422
        assert client.post(url, params={"mode": "resync", "days": 45}).status_code == 422
        assert client.post(url, params={"mode": "incremental", "days": 30}).status_code == 422
        assert queued == []

        started = client.post(url, params={"mode": "resync", "days": 30})
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        assert queued == [(connection_id, run_id, "resync", 30)]

        again = client.post(url, params={"mode": "resync", "days": 90})
        assert again.json()["already_running"] is True
        assert again.json()["mode"] == "resync"
        assert len(queued) == 1

    async with SessionLocal() as db:
        run = await db.get(SyncRun, uuid.UUID(run_id))
        assert run.mode == "resync"
        assert run.details["days"] == 30


class NamedNetworkClient(FakeKeitaroClient):
    """Трекер, у которого сеть оффера не заведена отдельной сущностью.

    Так выглядит боевой Keitaro: /affiliate_networks отдаёт несколько сетей,
    а имена партнёрок у большинства офферов приходят строкой.
    """

    async def offers(self) -> list[dict]:
        return [
            {
                "id": 41,
                "name": "Offer by name",
                "group_id": 20,
                "affiliate_network": "Fame Partners",
                "country": ["DE"],
                "state": "active",
            }
        ]


async def test_a_partner_named_by_an_offer_stays_active_across_syncs(database) -> None:
    """Раньше каждый прогон гасил такие партнёрки: их нет в списке сетей."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Named networks",
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("test-key"),
            timezone="UTC",
            buyer_sub_id=1,
            lookback_days=1,
        )
        db.add(connection)
        await db.flush()
        runs = [SyncRun(connection_id=connection.id, mode="incremental") for _ in range(2)]
        db.add_all(runs)
        await db.commit()
        connection_id = str(connection.id)
        run_ids = [str(run.id) for run in runs]

    engine = KeitaroSyncEngine(SessionLocal, client_factory=NamedNetworkClient)
    for run_id in run_ids:
        assert (await engine.run(connection_id, run_id, "incremental"))["status"] == "success"

    async with SessionLocal() as db:
        partner = await db.scalar(
            select(Partner).where(
                Partner.connection_id == uuid.UUID(connection_id),
                Partner.name == "Fame Partners",
            )
        )
        assert partner is not None
        assert partner.status == Status.active


class LateNetworkClient(FakeKeitaroClient):
    """Сеть, которая была строкой в оффере, приехала своей записью.

    Так выглядит трекер после того, как ключу API открыли доступ к разделу
    сетей: то же имя теперь приходит с собственным id.
    """

    async def affiliate_networks(self) -> list[dict]:
        return [{"id": 77, "name": "Fame Partners", "state": "active"}]


async def test_a_network_opened_later_adopts_its_partner_instead_of_doubling_it(
    database,
) -> None:
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Late networks",
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("test-key"),
            timezone="UTC",
            buyer_sub_id=1,
            lookback_days=1,
        )
        db.add(connection)
        await db.flush()
        runs = [SyncRun(connection_id=connection.id, mode="incremental") for _ in range(2)]
        db.add_all(runs)
        await db.commit()
        connection_id = str(connection.id)
        first_run, second_run = (str(run.id) for run in runs)

    # Первый прогон: ключ сеть не видит, партнёрка заводится по имени оффера.
    await KeitaroSyncEngine(SessionLocal, client_factory=NamedNetworkClient).run(
        connection_id, first_run, "incremental"
    )
    # Второй: доступ открыли, та же сеть пришла со своим id.
    await KeitaroSyncEngine(SessionLocal, client_factory=LateNetworkClient).run(
        connection_id, second_run, "incremental"
    )

    async with SessionLocal() as db:
        partners = list(
            (
                await db.execute(
                    select(Partner).where(
                        Partner.connection_id == uuid.UUID(connection_id),
                        Partner.name == "Fame Partners",
                    )
                )
            ).scalars()
        )
        assert len(partners) == 1
        assert partners[0].external_id == "77"
        assert partners[0].status == Status.active
