import uuid
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret, hash_password
from app.models import (
    IntegrationConnection,
    KeitaroStatDaily,
    MetaAdAccount,
    MetaBusiness,
    MetaEntity,
    MetaFanPage,
    MetaSocialAccount,
    MetaStatDaily,
    Role,
    Session,
    SyncRun,
    SyncStatus,
    User,
    UserParent,
)
from app.services.meta import (
    MetaClient,
    MetaError,
    account_status_label,
    action_counts,
    money_from_minor,
)
from app.services.meta_sync import MetaSyncEngine
from tests.test_media_finance import _admin_client

ACCOUNT_ID = "act_555000111"
CAMPAIGN_ID = "23848000001"
CAMPAIGN_QUIET_ID = "23848000009"
ADSET_ID = "23848000002"
AD_ID = "23848000003"
BUSINESS_ID = "77770001"
PAGE_ID = "88880001"
ATTRIBUTION_SUB = 7


def _graph_response(request: httpx.Request) -> httpx.Response:
    """Минимальный, но правдоподобный Graph API: суммы в центах, курсорная постраничка."""
    path = request.url.path
    if path.endswith("/me"):
        return httpx.Response(200, json={"id": "10001", "name": "Celestial System User"})
    if path.endswith("/businesses"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": BUSINESS_ID,
                        "name": "Celestial BM",
                        "verification_status": "verified",
                    }
                ]
            },
        )
    if path.endswith("/owned_pages") or path.endswith("/me/accounts"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": PAGE_ID, "name": "Nervio Official", "category": "Health"}
                ]
            },
        )
    if path.endswith("/adaccounts") or path.endswith("/owned_ad_accounts"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": ACCOUNT_ID,
                        "account_id": "555000111",
                        "name": "Celestial Main",
                        "account_status": 1,
                        "currency": "USD",
                        "timezone_name": "Europe/Kiev",
                        "spend_cap": "500000",
                        "amount_spent": "128450",
                        "balance": "12000",
                    }
                ],
                "paging": {"cursors": {"after": "MQ=="}},
            },
        )
    if path.endswith("/campaigns"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": CAMPAIGN_ID,
                        "name": "DE | Nervio | broad",
                        "effective_status": "ACTIVE",
                        "objective": "OUTCOME_LEADS",
                        "daily_budget": "5000",
                    }
                ]
            },
        )
    if path.endswith("/adsets"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": ADSET_ID,
                        "name": "DE 25-45",
                        "campaign_id": CAMPAIGN_ID,
                        "effective_status": "ACTIVE",
                    }
                ]
            },
        )
    if path.endswith("/ads"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": AD_ID,
                        "name": "creative-01",
                        "adset_id": ADSET_ID,
                        "effective_status": "ACTIVE",
                        "creative": {
                            "id": "9001",
                            "effective_object_story_id": PAGE_ID + "_50001",
                        },
                    }
                ]
            },
        )
    if path.endswith("/insights"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "date_start": "2026-07-01",
                        "campaign_id": CAMPAIGN_ID,
                        "adset_id": ADSET_ID,
                        "ad_id": AD_ID,
                        "impressions": "12000",
                        "clicks": "400",
                        "inline_link_clicks": "310",
                        "reach": "9000",
                        "spend": "180.50",
                        "actions": [
                            {"action_type": "lead", "value": "22"},
                            {"action_type": "link_click", "value": "400"},
                        ],
                    }
                ]
            },
        )
    return httpx.Response(200, json={"data": []})


def _client_factory(handler=_graph_response):
    def factory(access_token: str, **kwargs) -> MetaClient:
        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    return factory


@pytest.fixture
async def meta_connection(database):
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Meta test BM",
            kind="meta",
            base_url="https://graph.facebook.com/v23.0",
            api_key_encrypted=encrypt_secret("meta-test-token-value-long-enough"),
            lookback_days=3,
            attribution_sub_id=ATTRIBUTION_SUB,
        )
        tracker = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Meta test tracker",
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("tracker-key"),
        )
        db.add_all([connection, tracker])
        await db.commit()
        ids = {
            "connection": connection.id,
            "tracker": tracker.id,
            "workspace": admin.workspace_id,
            "admin": admin.id,
        }

    yield ids

    async with SessionLocal() as db:
        for table in (
            MetaStatDaily,
            MetaEntity,
            MetaAdAccount,
            MetaFanPage,
            MetaBusiness,
            MetaSocialAccount,
        ):
            await db.execute(
                delete(table).where(table.connection_id == ids["connection"])
            )
        await db.execute(
            delete(KeitaroStatDaily).where(
                KeitaroStatDaily.connection_id == ids["tracker"]
            )
        )
        await db.execute(
            delete(SyncRun).where(SyncRun.connection_id == ids["connection"])
        )
        await db.execute(
            delete(IntegrationConnection).where(
                IntegrationConnection.id.in_([ids["connection"], ids["tracker"]])
            )
        )
        await db.commit()


async def _run_sync(connection_id: uuid.UUID, mode: str = "backfill") -> dict:
    async with SessionLocal() as db:
        run = SyncRun(connection_id=connection_id, mode=mode, status=SyncStatus.queued)
        db.add(run)
        await db.commit()
        run_id = run.id
    engine = MetaSyncEngine(SessionLocal, client_factory=_client_factory())
    return await engine.run(str(connection_id), str(run_id), mode)


def test_money_from_minor_keeps_no_limit_distinct_from_zero() -> None:
    # spend_cap = 0 у Meta значит «лимита нет», а отсутствие поля — что его не отдали.
    assert money_from_minor("500000") == Decimal("5000.00")
    assert money_from_minor("0") == Decimal("0.00")
    assert money_from_minor(None) is None


def test_account_status_is_readable() -> None:
    assert account_status_label(1) == "ACTIVE"
    assert account_status_label(2) == "DISABLED"
    assert account_status_label(999) == "CODE_999"
    assert account_status_label(None) is None


def test_action_counts_separates_leads_from_clicks() -> None:
    leads, purchases, totals = action_counts(
        [
            {"action_type": "lead", "value": "10"},
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "3"},
            {"action_type": "link_click", "value": "400"},
        ]
    )
    assert (leads, purchases) == (10, 3)
    assert totals["link_click"] == 400


async def test_expired_token_gets_an_actionable_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "Error validating access token", "code": 190}},
        )

    client = MetaClient("dead-token", transport=httpx.MockTransport(handler))
    with pytest.raises(MetaError) as raised:
        await client.check()
    assert raised.value.error_code == 190
    assert "токен" in str(raised.value).lower()
    assert raised.value.retryable is False


async def test_rate_limit_is_retried_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(
                400,
                json={"error": {"message": "User request limit reached", "code": 17}},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(200, json={"id": "10001", "name": "System User"})

    client = MetaClient("token", transport=httpx.MockTransport(handler))
    assert (await client.check())["id"] == "10001"
    assert attempts["count"] == 2


async def test_paging_stops_without_a_next_link() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "act_1", "name": "One"}],
                    "paging": {"cursors": {"after": "CUR"}, "next": "https://next"},
                },
            )
        return httpx.Response(
            200,
            json={"data": [{"id": "act_2", "name": "Two"}], "paging": {}},
        )

    client = MetaClient("token", transport=httpx.MockTransport(handler))
    accounts = await client.ad_accounts()
    assert [account["id"] for account in accounts] == ["act_1", "act_2"]
    assert calls["count"] == 2


async def test_sync_stores_accounts_entities_and_stats(meta_connection) -> None:
    result = await _run_sync(meta_connection["connection"])
    assert result["status"] == "success"

    async with SessionLocal() as db:
        account = await db.scalar(
            select(MetaAdAccount).where(
                MetaAdAccount.connection_id == meta_connection["connection"]
            )
        )
        assert account.name == "Celestial Main"
        assert account.spend_cap == Decimal("5000.00")
        assert account.amount_spent == Decimal("1284.50")
        assert account.account_status == "ACTIVE"

        levels = list(
            (
                await db.execute(
                    select(MetaEntity.level).where(MetaEntity.account_id == account.id)
                )
            ).scalars()
        )
        assert set(levels) == {"campaign", "adset", "ad"}
        adset = await db.scalar(
            select(MetaEntity).where(MetaEntity.external_id == ADSET_ID)
        )
        assert adset.parent_external_id == CAMPAIGN_ID

        stat = await db.scalar(
            select(MetaStatDaily).where(MetaStatDaily.account_id == account.id)
        )
        assert stat.spend == Decimal("180.5000")
        assert stat.impressions == 12000
        assert stat.pixel_leads == 22
        assert stat.campaign_external_id == CAMPAIGN_ID


async def test_second_sync_updates_the_same_rows(meta_connection) -> None:
    await _run_sync(meta_connection["connection"])
    await _run_sync(meta_connection["connection"])

    async with SessionLocal() as db:
        accounts = await db.scalar(
            select(func.count())
            .select_from(MetaAdAccount)
            .where(MetaAdAccount.connection_id == meta_connection["connection"])
        )
        stats = await db.scalar(
            select(func.count())
            .select_from(MetaStatDaily)
            .where(MetaStatDaily.connection_id == meta_connection["connection"])
        )
        assert accounts == 1
        assert stats == 1


async def test_overview_joins_keitaro_revenue_by_sub_id(meta_connection) -> None:
    await _run_sync(meta_connection["connection"])
    async with SessionLocal() as db:
        db.add(
            KeitaroStatDaily(
                workspace_id=meta_connection["workspace"],
                connection_id=meta_connection["tracker"],
                record_date=date(2026, 7, 1),
                dimension_key=f"meta-test-{uuid.uuid4().hex}",
                sub_values={f"sub{ATTRIBUTION_SUB}": CAMPAIGN_ID},
                leads=22,
                sales=9,
                revenue=Decimal("540.00"),
            )
        )
        await db.commit()

    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/overview",
            params={"date_from": "2026-07-01", "date_to": "2026-07-01"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["attribution"]["sub_id"] == ATTRIBUTION_SUB
    assert payload["attribution"]["matched_campaigns"] == 1

    campaign = next(
        row for row in payload["campaigns"] if row["external_id"] == CAMPAIGN_ID
    )
    assert campaign["spend"] == 180.5
    assert campaign["revenue"] == 540.0
    assert campaign["profit"] == 359.5
    # ROI = профит / расход, 359.5 / 180.5 ≈ 199.17 %
    assert campaign["roi"] == pytest.approx(199.17, abs=0.01)
    assert campaign["ctr"] == pytest.approx(3.33, abs=0.01)


async def test_overview_reports_no_revenue_without_attribution(meta_connection) -> None:
    await _run_sync(meta_connection["connection"])
    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, meta_connection["connection"])
        connection.attribution_sub_id = None
        await db.commit()

    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/overview",
            params={"date_from": "2026-07-01", "date_to": "2026-07-01"},
        )
    payload = response.json()
    campaign = next(
        row for row in payload["campaigns"] if row["external_id"] == CAMPAIGN_ID
    )
    # Расход есть, дохода нет — и он именно отсутствует, а не равен нулю.
    assert campaign["spend"] == 180.5
    assert campaign["revenue"] is None
    assert campaign["roi"] is None
    assert payload["attribution"]["sub_id"] is None


async def test_buyer_sees_only_the_accounts_assigned_to_them(meta_connection) -> None:
    await _run_sync(meta_connection["connection"])
    async with SessionLocal() as db:
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        buyer = User(
            workspace_id=meta_connection["workspace"],
            role_id=buyer_role.id,
            name="Meta scope buyer",
            login="metascopebuyer",
            password_hash=hash_password("scope-password"),
        )
        db.add(buyer)
        await db.commit()
        buyer_id = buyer.id

    try:
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/login",
                json={"login": "metascopebuyer", "password": "scope-password"},
            )
            assert login.status_code == 200
            response = client.get("/api/v1/meta/overview")
            assert response.status_code == 200
            assert response.json()["accounts"] == []

        async with SessionLocal() as db:
            account = await db.scalar(
                select(MetaAdAccount).where(
                    MetaAdAccount.connection_id == meta_connection["connection"]
                )
            )
            account.owner_id = buyer_id
            await db.commit()

        with TestClient(app) as client:
            client.post(
                "/api/v1/auth/login",
                json={"login": "metascopebuyer", "password": "scope-password"},
            )
            names = [row["name"] for row in client.get("/api/v1/meta/overview").json()["accounts"]]
            assert names == ["Celestial Main"]
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Session).where(Session.user_id == buyer_id))
            await db.execute(delete(UserParent).where(UserParent.user_id == buyer_id))
            await db.execute(delete(User).where(User.id == buyer_id))
            await db.commit()


async def test_period_longer_than_half_a_year_is_rejected(meta_connection) -> None:
    today = date.today()
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/overview",
            params={
                "date_from": (today - timedelta(days=400)).isoformat(),
                "date_to": today.isoformat(),
            },
        )
    assert response.status_code == 422


async def test_sync_stores_the_social_graph_and_links_the_account(meta_connection) -> None:
    """Соцаккаунт, БМ и фан-пейдж появляются, а кабинет привязывается к БМу."""
    await _run_sync(meta_connection["connection"])

    async with SessionLocal() as db:
        social = await db.scalar(
            select(MetaSocialAccount).where(
                MetaSocialAccount.connection_id == meta_connection["connection"]
            )
        )
        business = await db.scalar(
            select(MetaBusiness).where(
                MetaBusiness.connection_id == meta_connection["connection"]
            )
        )
        page = await db.scalar(
            select(MetaFanPage).where(
                MetaFanPage.connection_id == meta_connection["connection"]
            )
        )
        account = await db.scalar(
            select(MetaAdAccount).where(
                MetaAdAccount.connection_id == meta_connection["connection"]
            )
        )
        ad = await db.scalar(
            select(MetaEntity).where(
                MetaEntity.connection_id == meta_connection["connection"],
                MetaEntity.level == "ad",
            )
        )

    assert social is not None and social.name == "Celestial System User"
    assert business is not None and business.social_account_id == social.id
    # ФП пришёл и от аккаунта, и от БМа — побеждает БМ.
    assert page is not None and page.business_id == business.id
    assert page.social_account_id == social.id
    assert account.business_id == business.id
    assert account.social_account_id == social.id
    # Фан-пейдж объявления вытащен из префикса effective_object_story_id.
    assert ad.page_external_id == PAGE_ID


async def test_every_overview_level_answers(meta_connection) -> None:
    await _run_sync(meta_connection["connection"])
    async with SessionLocal() as db:
        account = await db.scalar(
            select(MetaAdAccount).where(
                MetaAdAccount.connection_id == meta_connection["connection"]
            )
        )
        account.owner_id = meta_connection["admin"]
        await db.commit()

    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        answers = {
            level: client.get(f"/api/v1/meta/overview/levels/{level}{window}")
            for level in (
                "users", "socials", "fanpages", "businesses",
                "accounts", "campaigns", "adsets", "ads",
            )
        }
        unknown = client.get(f"/api/v1/meta/overview/levels/nope{window}")

    assert unknown.status_code == 404
    for level, response in answers.items():
        assert response.status_code == 200, level
        payload = response.json()
        assert payload["level"] == level
        # На каждом уровне ровно одна строка: в фикстуре один объект каждого вида.
        assert len(payload["rows"]) == 1, level
        assert payload["rows"][0]["spend"] == 180.5, level
        # Клики по ссылке приходят отдельно от общих кликов.
        assert payload["rows"][0]["link_clicks"] == 310, level
        assert payload["rows"][0]["clicks"] == 400, level


async def test_revenue_stops_below_the_campaign(meta_connection) -> None:
    """Доход Keitaro привязан к ID кампании — у адсетов и объявлений его нет."""
    await _run_sync(meta_connection["connection"])
    async with SessionLocal() as db:
        db.add(
            KeitaroStatDaily(
                workspace_id=meta_connection["workspace"],
                connection_id=meta_connection["tracker"],
                record_date=date(2026, 7, 1),
                dimension_key="meta-levels-revenue",
                sub_values={f"sub{ATTRIBUTION_SUB}": CAMPAIGN_ID},
                leads=22,
                sales=5,
                revenue=Decimal("640"),
            )
        )
        await db.commit()

    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        campaigns = client.get(f"/api/v1/meta/overview/levels/campaigns{window}").json()
        ads = client.get(f"/api/v1/meta/overview/levels/ads{window}").json()

    assert campaigns["has_revenue"] is True
    assert campaigns["rows"][0]["revenue"] == 640.0
    assert campaigns["rows"][0]["profit"] == 459.5
    assert ads["has_revenue"] is False
    assert ads["rows"][0]["revenue"] is None


async def test_the_structure_columns_count_children(meta_connection) -> None:
    await _run_sync(meta_connection["connection"])
    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        socials = client.get(f"/api/v1/meta/overview/levels/socials{window}").json()
        businesses = client.get(f"/api/v1/meta/overview/levels/businesses{window}").json()
        accounts = client.get(f"/api/v1/meta/overview/levels/accounts{window}").json()
        pages = client.get(f"/api/v1/meta/overview/levels/fanpages{window}").json()

    assert socials["rows"][0]["businesses"] == 1
    assert socials["rows"][0]["fan_pages"] == 1
    assert socials["rows"][0]["ad_accounts"] == 1
    assert businesses["rows"][0]["ad_accounts"] == 1
    # Кабинет живёт на БМе, значит он не личный.
    assert accounts["rows"][0]["kind"] == "business"
    assert accounts["rows"][0]["business"] == "Celestial BM"
    assert pages["rows"][0]["business"] == "Celestial BM"


async def test_a_level_search_narrows_by_name(meta_connection) -> None:
    await _run_sync(meta_connection["connection"])
    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        hit = client.get(f"/api/v1/meta/overview/levels/campaigns{window}&search=nervio")
        miss = client.get(f"/api/v1/meta/overview/levels/campaigns{window}&search=zzz")

    assert len(hit.json()["rows"]) == 1
    assert miss.json()["rows"] == []


async def test_objects_without_spend_still_show_up(meta_connection) -> None:
    """Кабинет, ничего не открутивший за период, из обзора не исчезает."""
    await _run_sync(meta_connection["connection"])
    with _admin_client() as client:
        quiet = client.get(
            "/api/v1/meta/overview/levels/accounts?date_from=2026-01-01&date_to=2026-01-02"
        ).json()

    assert len(quiet["rows"]) == 1
    assert quiet["rows"][0]["spend"] == 0.0
    assert quiet["rows"][0]["name"] == "Celestial Main"


# --- фиксация расхода за отрезок дня (ТЗ 2.4.4) -------------------------------


@pytest.fixture
async def spend_window(meta_connection, monkeypatch):
    """Кампания с расходом за день плюс агент и оффер, на которые его относят."""
    from app.api.routers import meta as meta_router
    from app.models import (
        MediaRecord,
        MetaSpendCommit,
        Offer,
        ProviderType,
        SpendProvider,
    )

    day = date(2026, 8, 8)
    async with SessionLocal() as db:
        account = MetaAdAccount(
            workspace_id=meta_connection["workspace"],
            connection_id=meta_connection["connection"],
            external_id=ACCOUNT_ID,
            name="Кабинет для окна",
            currency="USD",
            timezone_name="Europe/Kyiv",
        )
        db.add(account)
        await db.flush()
        db.add(
            MetaEntity(
                workspace_id=meta_connection["workspace"],
                connection_id=meta_connection["connection"],
                account_id=account.id,
                level="campaign",
                external_id=CAMPAIGN_ID,
                name="ar_check2",
                effective_status="ACTIVE",
            )
        )
        # Вторая кампания без единого расхода за день: на ней проверяется, что
        # отмеченная строка не пропадает и что в Meta за ней не ходят.
        db.add(
            MetaEntity(
                workspace_id=meta_connection["workspace"],
                connection_id=meta_connection["connection"],
                account_id=account.id,
                level="campaign",
                external_id=CAMPAIGN_QUIET_ID,
                name="ar_check2 - Copy",
                effective_status="PAUSED",
            )
        )
        db.add(
            MetaStatDaily(
                workspace_id=meta_connection["workspace"],
                connection_id=meta_connection["connection"],
                account_id=account.id,
                record_date=day,
                campaign_external_id=CAMPAIGN_ID,
                dimension_key=f"{CAMPAIGN_ID}:{day.isoformat()}",
                spend=Decimal("24.00"),
            )
        )
        offer = Offer(workspace_id=meta_connection["workspace"], name="Оффер окна")
        provider = SpendProvider(
            workspace_id=meta_connection["workspace"],
            name="Агент 10%",
            provider_type=ProviderType.agent,
            commission_pct=Decimal("10"),
        )
        db.add_all([offer, provider])
        await db.commit()
        ids = {
            "day": day,
            "account": account.id,
            "offer": offer.id,
            "provider": provider.id,
            "buyer": meta_connection["admin"],
            "workspace": meta_connection["workspace"],
        }

    # Час стоит доллар — так проверяемо, что окно берёт именно свои часы.
    hour_calls: list[str] = []

    async def fake_load(client, external_id, start, end):
        hour_calls.append(external_id)
        return [
            {"hour": hour, "spend": 1.0, "impressions": 10, "clicks": 1, "link_clicks": 0}
            for hour in range(24)
        ]

    monkeypatch.setattr(meta_router.meta_hourly, "load", fake_load)
    ids["hour_calls"] = hour_calls

    yield ids

    async with SessionLocal() as db:
        await db.execute(
            delete(MetaSpendCommit).where(MetaSpendCommit.workspace_id == ids["workspace"])
        )
        await db.execute(
            delete(MediaRecord).where(MediaRecord.offer_id == ids["offer"])
        )
        await db.execute(delete(Offer).where(Offer.id == ids["offer"]))
        await db.execute(delete(SpendProvider).where(SpendProvider.id == ids["provider"]))
        await db.commit()


def _commit_payload(spend_window, **overrides) -> dict:
    payload = {
        "record_date": spend_window["day"].isoformat(),
        "hour_from": 12,
        "hour_to": 16,
        "campaign_ids": [CAMPAIGN_ID],
        "offer_id": str(spend_window["offer"]),
        "buyer_id": str(spend_window["buyer"]),
        "provider_id": str(spend_window["provider"]),
    }
    payload.update(overrides)
    return payload


async def test_the_window_shows_only_its_own_hours(spend_window) -> None:
    """С 12:00 по 16:00 — это четыре часа, а не пять и не сутки."""
    with _admin_client() as client:
        payload = client.get(
            f"/api/v1/meta/spend/window?day={spend_window['day']}&hour_from=12&hour_to=16"
        ).json()

    assert payload["total"] == 4.0
    row = payload["rows"][0]
    assert row["name"] == "ar_check2"
    # Дневная сумма остаётся видна рядом: по ней понятно, какую часть берём.
    assert row["day_spend"] == 24.0
    assert row["taken_hours"] == []
    assert payload["timezones"] == ["Europe/Kyiv"]


async def test_committed_spend_lands_in_the_mediaboard_with_the_agent_percent(
    spend_window,
) -> None:
    from app.models import MediaRecord

    with _admin_client() as client:
        result = client.post("/api/v1/meta/spend/commit", json=_commit_payload(spend_window))
    assert result.status_code == 201
    body = result.json()
    # Четыре часа по доллару плюс 10 % агента.
    assert body["base_amount"] == 4.0
    assert body["spend"] == 4.4

    async with SessionLocal() as db:
        record = await db.get(MediaRecord, uuid.UUID(body["media_record_id"]))
        assert record.spend_calculated == Decimal("4.4000")
        assert record.record_date == spend_window["day"]


async def test_two_windows_of_one_day_add_up_instead_of_overwriting(spend_window) -> None:
    with _admin_client() as client:
        first = client.post(
            "/api/v1/meta/spend/commit", json=_commit_payload(spend_window)
        ).json()
        second = client.post(
            "/api/v1/meta/spend/commit",
            json=_commit_payload(spend_window, hour_from=16, hour_to=20),
        ).json()

    assert first["media_record_id"] == second["media_record_id"]
    # 4 + 4 часа по доллару, и всё это с процентом агента.
    assert second["spend"] == 8.8


async def test_the_same_hours_cannot_be_committed_twice(spend_window) -> None:
    """Второй клик по той же кнопке иначе молча удваивал бы расход."""
    with _admin_client() as client:
        client.post("/api/v1/meta/spend/commit", json=_commit_payload(spend_window))
        again = client.post(
            "/api/v1/meta/spend/commit",
            json=_commit_payload(spend_window, hour_from=14, hour_to=18),
        )
    assert again.status_code == 409
    assert "уже отнесена" in again.json()["error"]["message"]


async def test_removing_a_commit_takes_its_spend_back(spend_window) -> None:
    from app.models import MediaRecord

    with _admin_client() as client:
        body = client.post(
            "/api/v1/meta/spend/commit", json=_commit_payload(spend_window)
        ).json()
        client.post(
            "/api/v1/meta/spend/commit",
            json=_commit_payload(spend_window, hour_from=16, hour_to=20),
        )
        items = client.get(f"/api/v1/meta/spend/commits?day={spend_window['day']}").json()["items"]
        assert [item["window"] for item in items] == ["12:00–16:00", "16:00–20:00"]
        client.delete(f"/api/v1/meta/spend/commits/{items[0]['id']}")

    async with SessionLocal() as db:
        record = await db.get(MediaRecord, uuid.UUID(body["media_record_id"]))
        assert record.spend_calculated == Decimal("4.4000")


async def test_taken_hours_are_visible_on_the_window(spend_window) -> None:
    with _admin_client() as client:
        client.post("/api/v1/meta/spend/commit", json=_commit_payload(spend_window))
        payload = client.get(
            f"/api/v1/meta/spend/window?day={spend_window['day']}&hour_from=8&hour_to=12"
        ).json()

    assert payload["rows"][0]["taken_hours"] == [12, 13, 14, 15]


async def test_only_the_marked_campaigns_are_asked_about(spend_window) -> None:
    """Форму открывают с отмеченными в таблице кампаниями — их и считаем."""
    spend_window["hour_calls"].clear()
    with _admin_client() as client:
        payload = client.get(
            f"/api/v1/meta/spend/window?day={spend_window['day']}"
            f"&hour_from=12&hour_to=16&campaign_ids={CAMPAIGN_QUIET_ID}"
        ).json()

    assert [row["campaign_id"] for row in payload["rows"]] == [CAMPAIGN_QUIET_ID]
    # Отмеченная кампания остаётся в списке даже с нулём: иначе выбранная
    # строка молча пропадала бы из формы.
    assert payload["rows"][0]["spend"] == 0.0
    assert payload["rows"][0]["day_spend"] == 0.0
    # А в Meta за её часами не ходим — за день ноль, в окне тоже будет ноль.
    assert spend_window["hour_calls"] == []


async def test_a_buyer_fixes_the_spend_on_their_own_day(spend_window) -> None:
    """Баер фиксирует со своего аккаунта: `media.manage` хватает, чужой день — нет.

    Права `meta.launch` (заливы, деньги в кабинете) у роли «Buyer» нет, и
    требовать его значило бы выдать баеру заодно и запуск рекламы.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import MetaAdAccount, Permission

    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as db:
        codes = list(
            (
                await db.execute(
                    select(Permission).where(
                        Permission.code.in_(["meta.view", "media.manage"])
                    )
                )
            ).scalars()
        )
        role = Role(
            workspace_id=spend_window["workspace"],
            name=f"Баер окна {suffix}",
            permissions=codes,
        )
        db.add(role)
        await db.flush()
        buyer = User(
            workspace_id=spend_window["workspace"],
            role_id=role.id,
            name="Баер окна",
            login=f"window-buyer-{suffix}",
            password_hash=hash_password("test-password"),
        )
        db.add(buyer)
        # Кабинет виден баеру только как своему ответственному.
        account = await db.get(MetaAdAccount, spend_window["account"])
        account.owner_id = buyer.id
        await db.commit()
        buyer_id = buyer.id
        role_id = role.id

    try:
        with TestClient(app) as client:
            assert client.post(
                "/api/v1/auth/login",
                json={"login": f"window-buyer-{suffix}", "password": "test-password"},
            ).status_code == 200
            stranger = client.post(
                "/api/v1/meta/spend/commit",
                json=_commit_payload(spend_window),
            )
            own = client.post(
                "/api/v1/meta/spend/commit",
                json=_commit_payload(spend_window, buyer_id=str(buyer_id)),
            )

        assert stranger.status_code == 403
        assert own.status_code == 201
        assert own.json()["base_amount"] == 4.0
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Session).where(Session.user_id == buyer_id))
            await db.execute(delete(User).where(User.id == buyer_id))
            await db.execute(delete(Role).where(Role.id == role_id))
            await db.commit()
