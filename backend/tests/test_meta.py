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
        kwargs.pop("transport", None)
        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    return factory


@pytest.fixture
async def meta_connection(database):
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            owner_id=admin.id,
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


async def test_video_upload_fetches_thumbnail_from_meta(meta_connection, monkeypatch) -> None:
    """/advideos возвращает только id — превью для объявления дозапрашиваем."""
    import app.api.routers.meta as meta_router

    await _run_sync(meta_connection["connection"])
    async with SessionLocal() as db:
        account = await db.scalar(
            select(MetaAdAccount).where(
                MetaAdAccount.connection_id == meta_connection["connection"]
            )
        )
        account_id = str(account.id)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/advideos"):
            return httpx.Response(200, json={"id": "video-1"})
        if request.url.path.endswith("/video-1"):
            return httpx.Response(200, json={"picture": "https://cdn.example/prev.jpg"})
        return httpx.Response(200, json={"data": []})

    def factory(access_token: str, **kwargs) -> MetaClient:
        kwargs.pop("transport", None)
        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(meta_router, "MetaClient", factory)

    with _admin_client() as client:
        response = client.post(
            f"/api/v1/meta/creatives?account_id={account_id}&name=Видео",
            files={"file": ("video.mp4", b"fake-mp4-bytes", "video/mp4")},
        )
    assert response.status_code == 201
    assert any(c.startswith("POST") and c.endswith("/advideos") for c in calls)
    assert any(c.startswith("GET") and c.endswith("/video-1") for c in calls)
    assert response.json()["thumbnail_url"] == "https://cdn.example/prev.jpg"


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


async def test_account_assets_falls_back_to_visible_fan_pages(
    meta_connection, monkeypatch
) -> None:
    """The page selector remains usable when ``promote_pages`` is empty."""
    await _run_sync(meta_connection["connection"])

    class AssetClient:
        async def account_pages(self, _account_external_id: str) -> list[dict]:
            return []

        async def pages(self, _business_id: str | None = None) -> list[dict]:
            return [{"id": PAGE_ID, "name": "Nervio Official"}]

        async def pixels(self, _account_external_id: str) -> list[dict]:
            return [{"id": "pixel-1", "name": "Nervio Pixel"}]

    async def fake_client_for(_connection, _db):
        return AssetClient()

    monkeypatch.setattr("app.api.routers.meta.client_for", fake_client_for)
    async with SessionLocal() as db:
        account = await db.scalar(
            select(MetaAdAccount).where(
                MetaAdAccount.connection_id == meta_connection["connection"]
            )
        )
        account_id = str(account.id)

    with _admin_client() as client:
        response = client.get(f"/api/v1/meta/accounts/{account_id}/assets")

    assert response.status_code == 200, response.text
    assert response.json()["pages"] == [{"id": PAGE_ID, "name": "Nervio Official"}]
    assert response.json()["pixels"] == [{"id": "pixel-1", "name": "Nervio Pixel"}]


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


async def test_meta_connections_follow_user_hierarchy_and_allow_multiple_own_connections(
    database, monkeypatch
) -> None:
    """Баер владеет своими подключениями, тимлид их видит, админ видит все."""
    from fastapi.testclient import TestClient

    from app.api.routers import meta as meta_router
    from app.main import app

    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        lead_role = await db.scalar(select(Role).where(Role.name == "Team Lead"))
        lead = User(
            workspace_id=admin.workspace_id,
            role_id=lead_role.id,
            name=f"Meta lead {suffix}",
            login=f"meta-lead-{suffix}",
            password_hash=hash_password("test-password"),
        )
        first_buyer = User(
            workspace_id=admin.workspace_id,
            role_id=buyer_role.id,
            name=f"Meta buyer one {suffix}",
            login=f"meta-buyer-one-{suffix}",
            password_hash=hash_password("test-password"),
        )
        second_buyer = User(
            workspace_id=admin.workspace_id,
            role_id=buyer_role.id,
            name=f"Meta buyer two {suffix}",
            login=f"meta-buyer-two-{suffix}",
            password_hash=hash_password("test-password"),
        )
        db.add_all([lead, first_buyer, second_buyer])
        await db.flush()
        db.add(UserParent(user_id=first_buyer.id, parent_id=lead.id))
        await db.commit()
        user_ids = {lead.id, first_buyer.id, second_buyer.id}

    async def fake_verify(*_args, **_kwargs):
        return [{
            "id": f"act_{uuid.uuid4().int % 10**12}",
            "name": "Личный кабинет",
            "account_status": 1,
            "currency": "USD",
        }]

    monkeypatch.setattr(meta_router, "_verify_token", fake_verify)

    def create_as(login: str, name: str):
        with TestClient(app) as client:
            assert client.post(
                "/api/v1/auth/login",
                json={"login": login, "password": "test-password"},
            ).status_code == 200
            return client.post(
                "/api/v1/meta/connections",
                json={"name": name, "access_token": "personal-meta-token-long-enough"},
            )

    created_ids: set[uuid.UUID] = set()
    try:
        first = create_as(f"meta-buyer-one-{suffix}", f"Buyer one A {suffix}")
        second = create_as(f"meta-buyer-one-{suffix}", f"Buyer one B {suffix}")
        stranger = create_as(f"meta-buyer-two-{suffix}", f"Buyer two A {suffix}")
        assert first.status_code == second.status_code == stranger.status_code == 201
        created_ids = {
            uuid.UUID(first.json()["id"]),
            uuid.UUID(second.json()["id"]),
            uuid.UUID(stranger.json()["id"]),
        }
        assert first.json()["owner_name"] == f"Meta buyer one {suffix}"
        assert first.json()["can_edit"] is True

        with TestClient(app) as client:
            client.post(
                "/api/v1/auth/login",
                json={"login": f"meta-buyer-one-{suffix}", "password": "test-password"},
            )
            rows = client.get("/api/v1/meta/connections").json()["items"]
            visible = {row["id"]: row for row in rows}
            assert first.json()["id"] in visible and second.json()["id"] in visible
            assert stranger.json()["id"] not in visible
            assert visible[first.json()["id"]]["can_edit"] is True

        with TestClient(app) as client:
            client.post(
                "/api/v1/auth/login",
                json={"login": f"meta-lead-{suffix}", "password": "test-password"},
            )
            rows = client.get("/api/v1/meta/connections").json()["items"]
            visible = {row["id"]: row for row in rows}
            assert first.json()["id"] in visible and second.json()["id"] in visible
            assert stranger.json()["id"] not in visible
            assert visible[first.json()["id"]]["can_edit"] is False
            denied = client.patch(
                f"/api/v1/meta/connections/{first.json()['id']}",
                json={"name": f"Lead edit {suffix}"},
            )
            assert denied.status_code == 403

        with _admin_client() as client:
            rows = client.get("/api/v1/meta/connections").json()["items"]
            visible = {row["id"]: row for row in rows}
            assert {str(value) for value in created_ids}.issubset(visible)
            assert all(visible[str(value)]["can_edit"] for value in created_ids)
    finally:
        async with SessionLocal() as db:
            if created_ids:
                await db.execute(
                    delete(MetaAdAccount).where(MetaAdAccount.connection_id.in_(created_ids))
                )
                await db.execute(
                    delete(IntegrationConnection).where(IntegrationConnection.id.in_(created_ids))
                )
            await db.execute(delete(Session).where(Session.user_id.in_(user_ids)))
            await db.execute(delete(UserParent).where(UserParent.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
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


async def test_accounts_are_searchable_by_meta_id(meta_connection) -> None:
    """Кабинет ищется по `act_...`, а не только по названию.

    В комментариях и в чужих ссылках имени кабинета часто нет, а ID есть, — по
    названию такой кабинет было не найти.
    """
    await _run_sync(meta_connection["connection"])
    async with SessionLocal() as db:
        account = await db.scalar(
            select(MetaAdAccount).where(
                MetaAdAccount.connection_id == meta_connection["connection"]
            )
        )
        account.name = "Совершенно другое имя"
        await db.commit()

    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        by_id = client.get(
            f"/api/v1/meta/overview/levels/accounts{window}&search={ACCOUNT_ID}"
        ).json()
        by_digits = client.get(
            f"/api/v1/meta/overview/levels/accounts{window}&search=555000"
        ).json()
        by_name = client.get(
            f"/api/v1/meta/overview/levels/accounts{window}&search=другое"
        ).json()
        miss = client.get(
            f"/api/v1/meta/overview/levels/accounts{window}&search=act_000000"
        ).json()

    assert len(by_id["rows"]) == 1
    # Ищем и по куску идентификатора: целиком его редко копируют.
    assert len(by_digits["rows"]) == 1
    # Поиск по названию продолжает работать.
    assert len(by_name["rows"]) == 1
    assert miss["rows"] == []
    # ID виден в самой строке — его копируют отсюда же.
    assert by_id["rows"][0]["external_id"] == ACCOUNT_ID


async def test_campaigns_are_searchable_by_their_meta_id(meta_connection) -> None:
    """Кампании, адсеты и объявления ищутся по числовому id из Ads Manager."""
    await _run_sync(meta_connection["connection"])
    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        found = client.get(
            f"/api/v1/meta/overview/levels/campaigns{window}&search={CAMPAIGN_ID}"
        ).json()
        missing = client.get(
            f"/api/v1/meta/overview/levels/campaigns{window}&search=99999999"
        ).json()
    assert len(found["rows"]) == 1
    assert missing["rows"] == []


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


async def test_structure_filters_by_connection_owner_then_social_account(
    meta_connection,
) -> None:
    """Чекбоксы идут каскадом: владелец подключения → Meta-аккаунт → низы."""
    await _run_sync(meta_connection["connection"])
    suffix = uuid.uuid4().hex[:8]
    created_user_id = None
    created_connection_id = None
    async with SessionLocal() as db:
        admin = await db.get(User, meta_connection["admin"])
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        buyer = User(
            workspace_id=admin.workspace_id,
            role_id=buyer_role.id,
            name=f"Filter buyer {suffix}",
            login=f"filter-buyer-{suffix}",
            password_hash=hash_password("test-password"),
        )
        db.add(buyer)
        await db.flush()
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            owner_id=buyer.id,
            name=f"Filter connection {suffix}",
            kind="meta",
            base_url="https://graph.facebook.com/v23.0",
            api_key_encrypted=encrypt_secret("filter-meta-token-long-enough"),
        )
        db.add(connection)
        await db.flush()
        social = MetaSocialAccount(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id=f"social-{suffix}",
            name=f"Selected social {suffix}",
        )
        db.add(social)
        await db.flush()
        business = MetaBusiness(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            social_account_id=social.id,
            external_id=f"business-{suffix}",
            name=f"Selected BM {suffix}",
        )
        db.add(business)
        await db.flush()
        page = MetaFanPage(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            social_account_id=social.id,
            business_id=business.id,
            external_id=f"page-{suffix}",
            name=f"Selected page {suffix}",
        )
        account = MetaAdAccount(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            social_account_id=social.id,
            business_id=business.id,
            external_id=f"act-{suffix}",
            name=f"Selected cabinet {suffix}",
            owner_id=admin.id,
            currency="USD",
        )
        db.add_all([page, account])
        await db.flush()
        campaign_id = f"campaign-{suffix}"
        db.add(
            MetaEntity(
                workspace_id=admin.workspace_id,
                connection_id=connection.id,
                account_id=account.id,
                level="campaign",
                external_id=campaign_id,
                name=f"Selected campaign {suffix}",
            )
        )
        db.add(
            MetaStatDaily(
                workspace_id=admin.workspace_id,
                connection_id=connection.id,
                account_id=account.id,
                record_date=date(2026, 7, 1),
                campaign_external_id=campaign_id,
                dimension_key=f"cascade-{suffix}",
                spend=Decimal("12.50"),
            )
        )
        await db.commit()
        created_user_id = buyer.id
        created_connection_id = connection.id
        social_id = social.id

    window = {"date_from": "2026-07-01", "date_to": "2026-07-01"}
    try:
        with _admin_client() as client:
            users = client.get("/api/v1/meta/overview/levels/users", params=window).json()
            socials = client.get(
                "/api/v1/meta/overview/levels/socials",
                params={**window, "connection_owner_id": str(created_user_id)},
            ).json()
            common = {
                **window,
                "connection_owner_id": str(created_user_id),
                "social_id": str(social_id),
            }
            pages = client.get(
                "/api/v1/meta/overview/levels/fanpages", params=common
            ).json()
            accounts = client.get(
                "/api/v1/meta/overview/levels/accounts", params=common
            ).json()
            campaigns = client.get(
                "/api/v1/meta/overview/levels/campaigns", params=common
            ).json()

        user_rows = {row["id"]: row for row in users["rows"]}
        assert str(created_user_id) in user_rows
        # Ответственным кабинета специально оставлен админ: строка всё равно
        # принадлежит баеру, потому что источник — владелец подключения.
        assert user_rows[str(created_user_id)]["spend"] == 12.5
        assert [row["name"] for row in socials["rows"]] == [f"Selected social {suffix}"]
        assert [row["name"] for row in pages["rows"]] == [f"Selected page {suffix}"]
        assert [row["name"] for row in accounts["rows"]] == [f"Selected cabinet {suffix}"]
        assert [row["name"] for row in campaigns["rows"]] == [
            f"Selected campaign {suffix}"
        ]
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(MetaStatDaily).where(
                    MetaStatDaily.connection_id == created_connection_id
                )
            )
            await db.execute(
                delete(MetaEntity).where(MetaEntity.connection_id == created_connection_id)
            )
            await db.execute(
                delete(MetaAdAccount).where(
                    MetaAdAccount.connection_id == created_connection_id
                )
            )
            await db.execute(
                delete(MetaFanPage).where(MetaFanPage.connection_id == created_connection_id)
            )
            await db.execute(
                delete(MetaBusiness).where(MetaBusiness.connection_id == created_connection_id)
            )
            await db.execute(
                delete(MetaSocialAccount).where(
                    MetaSocialAccount.connection_id == created_connection_id
                )
            )
            await db.execute(
                delete(IntegrationConnection).where(
                    IntegrationConnection.id == created_connection_id
                )
            )
            await db.execute(delete(User).where(User.id == created_user_id))
            await db.commit()


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
            "connection": meta_connection["connection"],
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
    """Однодневное окно по умолчанию: с 12:00 по 16:00 того же дня."""
    payload = {
        "date_from": spend_window["day"].isoformat(),
        "hour_from": 12,
        "date_to": spend_window["day"].isoformat(),
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
    assert row["taken_windows"] == []
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
        record = await db.get(MediaRecord, uuid.UUID(body["media_record_ids"][0]))
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

    assert first["media_record_ids"] == second["media_record_ids"]
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
        record = await db.get(MediaRecord, uuid.UUID(body["media_record_ids"][0]))
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


# --- окно через полночь ---------------------------------------------------------


async def _add_spend_day(spend_window, day: date) -> None:
    async with SessionLocal() as db:
        db.add(
            MetaStatDaily(
                workspace_id=spend_window["workspace"],
                connection_id=spend_window["connection"],
                account_id=spend_window["account"],
                record_date=day,
                campaign_external_id=CAMPAIGN_ID,
                dimension_key=f"{CAMPAIGN_ID}:{day.isoformat()}",
                spend=Decimal("24.00"),
            )
        )
        await db.commit()


async def test_a_window_can_cross_midnight(spend_window) -> None:
    """Окно с 22:00 до 06:00 следующего дня ложится в записи обоих дней.

    Медиаборд живёт записями «день + баер + оффер», поэтому длинное окно
    раскладывается на посуточные части — каждая со своей суммой и своим
    процентом агента.
    """
    from app.models import MediaRecord

    next_day = spend_window["day"] + timedelta(days=1)
    await _add_spend_day(spend_window, next_day)
    with _admin_client() as client:
        result = client.post(
            "/api/v1/meta/spend/commit",
            json=_commit_payload(
                spend_window, hour_from=22, date_to=next_day.isoformat(), hour_to=6
            ),
        )
        items = client.get(
            f"/api/v1/meta/spend/commits?from={spend_window['day']}&to={next_day}"
        ).json()["items"]

    assert result.status_code == 201
    body = result.json()
    # Часть первого дня (22–24) плюс часть второго (00–06): шесть часов по
    # доллару и два часа по доллару, всё с 10 % агента.
    assert body["base_amount"] == 8.0
    assert body["spend"] == 8.8
    assert len(body["media_record_ids"]) == 2

    async with SessionLocal() as db:
        records = [
            await db.get(MediaRecord, uuid.UUID(record_id))
            for record_id in body["media_record_ids"]
        ]
        assert sorted(record.record_date for record in records) == [
            spend_window["day"],
            next_day,
        ]

    # В списке видно обе части, каждая со своей датой.
    assert [item["window"] for item in items] == [
        f"{spend_window['day'].isoformat()} · 22:00–24:00",
        f"{next_day.isoformat()} · 00:00–06:00",
    ]


async def test_taken_windows_are_visible_for_a_cross_day_window(spend_window) -> None:
    """Занятые отрезки видны на экране и в окне, которое переходит через полночь."""
    next_day = spend_window["day"] + timedelta(days=1)
    await _add_spend_day(spend_window, next_day)
    with _admin_client() as client:
        client.post(
            "/api/v1/meta/spend/commit",
            json=_commit_payload(
                spend_window, hour_from=22, date_to=next_day.isoformat(), hour_to=6
            ),
        )
        payload = client.get(
            f"/api/v1/meta/spend/window?from={spend_window['day']}&to={next_day}"
            "&hour_from=4&hour_to=12"
        ).json()

    assert payload["rows"][0]["taken_windows"] == [
        {"date": spend_window["day"].isoformat(), "from": 22, "to": 24},
        {"date": next_day.isoformat(), "from": 0, "to": 6},
    ]
    # Окно 04:00–12:00 второго дня задевает занятые часы 04:00–06:00.
    row = payload["rows"][0]
    assert any(
        taken["date"] == next_day.isoformat() and taken["from"] < 12
        for taken in row["taken_windows"]
    )


async def test_a_conflict_is_caught_across_days(spend_window) -> None:
    """Час кампании нельзя отнести дважды, даже если окна лежат в разных днях."""
    next_day = spend_window["day"] + timedelta(days=1)
    await _add_spend_day(spend_window, next_day)
    with _admin_client() as client:
        client.post(
            "/api/v1/meta/spend/commit",
            json=_commit_payload(
                spend_window, hour_from=22, date_to=next_day.isoformat(), hour_to=6
            ),
        )
        again = client.post(
            "/api/v1/meta/spend/commit",
            json=_commit_payload(
                spend_window,
                date_from=next_day.isoformat(),
                hour_from=4,
                date_to=next_day.isoformat(),
                hour_to=10,
            ),
        )
    assert again.status_code == 409
    message = again.json()["error"]["message"]
    assert "уже отнесена" in message
    assert next_day.isoformat() in message


async def test_an_inverted_or_overlong_window_is_rejected(spend_window) -> None:
    """Конец раньше начала и окна длиной в квартал до Meta не доезжают."""
    long_after = spend_window["day"] + timedelta(days=31)
    with _admin_client() as client:
        inverted = client.get(
            f"/api/v1/meta/spend/window?from={long_after}&to={spend_window['day']}"
        )
        overlong = client.get(
            f"/api/v1/meta/spend/window?from={spend_window['day']}&to={long_after}"
        )
        commit_inverted = client.post(
            "/api/v1/meta/spend/commit",
            json=_commit_payload(
                spend_window,
                date_from=(spend_window["day"] + timedelta(days=1)).isoformat(),
                hour_from=10,
                hour_to=12,
            ),
        )
    assert inverted.status_code == 422
    assert overlong.status_code == 422
    assert commit_inverted.status_code == 422


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


async def test_a_pick_narrows_every_level_below_it(meta_connection) -> None:
    """Отметка строки сужает то, что ниже неё, и не трогает свой же уровень.

    Цепочка обзора идёт пользователь → аккаунт → БМ → фан-пейдж → кабинет, и
    каждый шаг связан по-своему: БМ знает свой аккаунт колонкой, а вот кабинет
    к странице привязан только через объявления — «кабинеты этой страницы» это
    те, где крутилась реклама от её лица.
    """
    await _run_sync(meta_connection["connection"])
    window = "?date_from=2026-07-01&date_to=2026-07-01"
    stranger = str(uuid.uuid4())
    with _admin_client() as client:
        business = client.get(f"/api/v1/meta/overview/levels/businesses{window}").json()
        page = client.get(f"/api/v1/meta/overview/levels/fanpages{window}").json()
        account = client.get(f"/api/v1/meta/overview/levels/accounts{window}").json()
        business_id = business["rows"][0]["id"]
        page_id = page["rows"][0]["id"]
        account_id = account["rows"][0]["id"]

        pages_of_business = client.get(
            f"/api/v1/meta/overview/levels/fanpages{window}&business_id={business_id}"
        ).json()
        pages_of_stranger = client.get(
            f"/api/v1/meta/overview/levels/fanpages{window}&business_id={stranger}"
        ).json()
        accounts_of_page = client.get(
            f"/api/v1/meta/overview/levels/accounts{window}&page_id={page_id}"
        ).json()
        accounts_of_nothing = client.get(
            f"/api/v1/meta/overview/levels/accounts{window}&page_id=404404404"
        ).json()
        campaigns_of_account = client.get(
            f"/api/v1/meta/overview/levels/campaigns{window}&ad_account_id={account_id}"
        ).json()
        campaigns_of_stranger = client.get(
            f"/api/v1/meta/overview/levels/campaigns{window}&ad_account_id={stranger}"
        ).json()
        # Свой уровень фильтром не режется — иначе снять отметку было бы нечем.
        own_level = client.get(
            f"/api/v1/meta/overview/levels/businesses{window}&business_id={stranger}"
        ).json()

    assert len(pages_of_business["rows"]) == 1
    assert pages_of_business["rows"][0]["id"] == page_id
    assert pages_of_stranger["rows"] == []
    assert len(accounts_of_page["rows"]) == 1
    assert accounts_of_page["rows"][0]["id"] == account_id
    assert accounts_of_nothing["rows"] == []
    assert len(campaigns_of_account["rows"]) == 1
    assert campaigns_of_stranger["rows"] == []
    assert len(own_level["rows"]) == 1


async def test_every_level_shows_its_meta_id(meta_connection) -> None:
    """ID объекта в Meta приходит на всех уровнях, кроме пользователя.

    Ключ строки для этого не годится: у аккаунтов, БМов и кабинетов это
    внутренний id CRM, а ищут и копируют везде метовский.
    """
    await _run_sync(meta_connection["connection"])
    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        answers = {
            level: client.get(f"/api/v1/meta/overview/levels/{level}{window}").json()
            for level in (
                "users", "socials", "businesses", "fanpages",
                "accounts", "campaigns", "adsets", "ads",
            )
        }
        by_id = client.get(
            f"/api/v1/meta/overview/levels/businesses{window}"
            f"&search={answers['businesses']['rows'][0]['external_id']}"
        ).json()

    assert answers["users"]["rows"][0]["external_id"] is None
    for level, payload in answers.items():
        if level == "users":
            continue
        assert payload["rows"][0]["external_id"], level
    assert answers["accounts"]["rows"][0]["external_id"].startswith("act_")
    # Фан-пейдж и объекты рекламы ключуются самим метовским ID.
    assert answers["fanpages"]["rows"][0]["external_id"] == answers["fanpages"]["rows"][0]["id"]
    # Раз ID виден, по нему должно и искаться — у БМа ключ строки чужой.
    assert len(by_id["rows"]) == 1


async def test_the_accounts_level_carries_its_connection(meta_connection) -> None:
    """Подключения живут в уровне «Аккаунты»: строка знает своё подключение.

    По этому id открываются сохранённые настройки прямо из таблицы, поэтому
    без него подключение стало бы недоступным для правки.
    """
    await _run_sync(meta_connection["connection"])
    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        socials = client.get(f"/api/v1/meta/overview/levels/socials{window}").json()

    row = socials["rows"][0]
    assert row["connection_id"] == str(meta_connection["connection"])
    assert row["connection"]


async def test_a_connection_without_an_account_still_shows_up(meta_connection) -> None:
    """Подключение, у которого аккаунт ещё не подтянулся, тоже видно.

    Первая синхронизация могла не пройти или токену не хватило прав — без строки
    в таблице такое подключение стало бы нечинимым: его настройки некуда открыть.
    """
    async with SessionLocal() as db:
        await db.execute(
            delete(MetaSocialAccount).where(
                MetaSocialAccount.connection_id == meta_connection["connection"]
            )
        )
        await db.commit()

    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        socials = client.get(f"/api/v1/meta/overview/levels/socials{window}").json()

    rows = {row["id"]: row for row in socials["rows"]}
    placeholder = rows.get(str(meta_connection["connection"]))
    assert placeholder is not None
    assert placeholder["connection_id"] == str(meta_connection["connection"])
    assert placeholder["ad_accounts"] == 0


async def test_error_diagnostics_redact_token_and_keep_subcode():
    token = "test-secret-token-value"
    def handler(request):
        return httpx.Response(400, json={"error": {
            "code": 10, "error_subcode": 123, "fbtrace_id": "trace-1",
            "message": f"Denied {token} access_token=another-secret",
            "access_token": token,
        }})
    client = MetaClient(token, transport=httpx.MockTransport(handler))
    with pytest.raises(MetaError) as raised:
        await client.check()
    assert raised.value.details["error_subcode"] == 123
    assert raised.value.details["fbtrace_id"] == "trace-1"
    assert token not in str(raised.value.details)
    assert "another-secret" not in str(raised.value.details)
    assert token not in str(raised.value)


async def test_a_campaign_without_spend_still_shows_in_the_overview(meta_connection) -> None:
    """Свежая кампания видна в разделе до первой открутки.

    Строки уровня собирались только из статистики, поэтому объект, у которого
    показов ещё не было — только что созданный или стоящий на модерации, — в
    списке не появлялся: в Ads Manager есть, в CRM нет, и правило на него не
    поставить. Синхронизация при этом отработала и запись завела.
    """
    await _run_sync(meta_connection["connection"])
    async with SessionLocal() as db:
        account = await db.scalar(
            select(MetaAdAccount).where(
                MetaAdAccount.connection_id == meta_connection["connection"]
            )
        )
        db.add(
            MetaEntity(
                workspace_id=meta_connection["workspace"],
                connection_id=meta_connection["connection"],
                account_id=account.id,
                level="campaign",
                external_id="120249472705360199",
                name="Свежая кампания",
                effective_status="IN_PROCESS",
                objective="OUTCOME_TRAFFIC",
            )
        )
        await db.commit()

    window = "?date_from=2026-07-01&date_to=2026-07-01"
    with _admin_client() as client:
        payload = client.get(f"/api/v1/meta/overview/levels/campaigns{window}").json()
        found = client.get(
            f"/api/v1/meta/overview/levels/campaigns{window}&search=120249472705360199"
        ).json()

    rows = {row["id"]: row for row in payload["rows"]}
    assert "120249472705360199" in rows
    fresh = rows["120249472705360199"]
    assert fresh["name"] == "Свежая кампания"
    assert fresh["spend"] == 0
    # Статус приезжает из справочника: по нему и видно, что объект на модерации.
    assert fresh["status"] == "IN_PROCESS"
    # Поиск по метовскому ID тоже её находит.
    assert [row["id"] for row in found["rows"]] == ["120249472705360199"]
