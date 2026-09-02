"""Записывающая часть Meta Ads: заливы, публикация, автоправила (ТЗ 3.3–3.6, 3.8)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret, hash_password
from app.main import app
from app.models import (
    IntegrationConnection,
    KeitaroStatDaily,
    LaunchStatus,
    MetaAdAccount,
    MetaCreative,
    MetaEntity,
    MetaLaunch,
    MetaLaunchCreative,
    MetaOperation,
    MetaRule,
    MetaRuleEvent,
    MetaStatDaily,
    MetaTemplate,
    Role,
    Status,
    User,
)
from app.services.meta import (
    MetaClient,
    MetaError,
    build_targeting,
    campaign_id_macro_url,
    money_to_minor,
)
from app.services.meta_launch import (
    LaunchValidationError,
    MetaLaunchPublisher,
    load_launch_context,
    validate_launch,
)
from app.services.meta_rules import MetaRuleEngine, matches
from tests.test_media_finance import _admin_client

ACCOUNT_ID = "act_777000222"
CAMPAIGN_ID = "23855000001"
ADSET_ID = "23855000002"
AD_ID = "23855000003"
CREATIVE_ID = "23855000004"
SUB_ID = 5


def _write_handler(request: httpx.Request) -> httpx.Response:
    """Graph API, отвечающий на запись. Каждый уровень — свой ID."""
    path = request.url.path
    if request.method == "POST":
        if path.endswith("/campaigns"):
            return httpx.Response(200, json={"id": CAMPAIGN_ID})
        if path.endswith("/adsets"):
            return httpx.Response(200, json={"id": ADSET_ID})
        if path.endswith("/adcreatives"):
            return httpx.Response(200, json={"id": CREATIVE_ID})
        if path.endswith("/ads"):
            return httpx.Response(200, json={"id": AD_ID})
        if path.endswith("/adimages"):
            return httpx.Response(
                200,
                json={"images": {"source": {"hash": "abc123hash", "url": "https://img"}}},
            )
        if path.endswith("/advideos"):
            return httpx.Response(200, json={"id": "video-1"})
        return httpx.Response(200, json={"success": True})
    if path.endswith("/search") and request.url.params.get("type") == "adlocale":
        # Словарь локалей для правил мультиязычности: числа — ключи adlocale.
        query = request.url.params.get("q", "").lower()
        locales = {
            "en": [{"key": 6, "name": "English (US)"}, {"key": 24, "name": "English (UK)"}],
            "deutsch": [{"key": 3, "name": "Deutsch"}],
            "français": [{"key": 32, "name": "Français"}],
        }
        rows = [
            row for key, entries in locales.items() if key in query for row in entries
        ]
        return httpx.Response(200, json={"data": rows})
    return httpx.Response(200, json={"data": []})


def _client_factory(handler=_write_handler):
    def factory(access_token: str, **kwargs) -> MetaClient:
        kwargs.pop("transport", None)
        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    return factory


@pytest.fixture
async def launch_setup(database):
    """Подключение, кабинет, шаблон, креатив и залив-черновик."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Meta launch BM",
            kind="meta",
            base_url="https://graph.facebook.com/v23.0",
            api_key_encrypted=encrypt_secret("meta-launch-token-long-enough-value"),
            attribution_sub_id=SUB_ID,
        )
        tracker = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Meta launch tracker",
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("tracker-key"),
        )
        db.add_all([connection, tracker])
        await db.flush()

        account = MetaAdAccount(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id=ACCOUNT_ID,
            name="Launch account",
            currency="USD",
            owner_id=admin.id,
        )
        db.add(account)
        await db.flush()

        template = MetaTemplate(
            workspace_id=admin.workspace_id,
            name="DE broad",
            objective="OUTCOME_LEADS",
            optimization_goal="LINK_CLICKS",
            billing_event="IMPRESSIONS",
            bid_strategy="LOWEST_COST_WITHOUT_CAP",
            geo=["DE"],
            genders=[],
            languages=[],
            placements={"publisher_platforms": ["facebook", "instagram"]},
            interests=[],
            page_id="900100",
        )
        creative = MetaCreative(
            workspace_id=admin.workspace_id,
            account_id=account.id,
            kind="image",
            name="banner-01",
            external_hash="abc123hash",
        )
        db.add_all([template, creative])
        await db.flush()

        launch = MetaLaunch(
            workspace_id=admin.workspace_id,
            account_id=account.id,
            template_id=template.id,
            owner_id=admin.id,
            name="DE Nervio broad",
            geo="DE",
            daily_budget=Decimal("50.00"),
            link_url="https://track.example/click?x=1",
            primary_text="Текст",
            headline="Заголовок",
        )
        db.add(launch)
        await db.flush()
        db.add(MetaLaunchCreative(launch_id=launch.id, creative_id=creative.id, position=0))
        await db.commit()

        ids = {
            "workspace": admin.workspace_id,
            "admin": admin.id,
            "connection": connection.id,
            "tracker": tracker.id,
            "account": account.id,
            "template": template.id,
            "creative": creative.id,
            "launch": launch.id,
        }

    yield ids

    async with SessionLocal() as db:
        await db.execute(delete(MetaRuleEvent).where(MetaRuleEvent.workspace_id == ids["workspace"]))
        await db.execute(delete(MetaRule).where(MetaRule.workspace_id == ids["workspace"]))
        await db.execute(delete(MetaOperation).where(MetaOperation.workspace_id == ids["workspace"]))
        await db.execute(delete(MetaLaunchCreative).where(MetaLaunchCreative.launch_id == ids["launch"]))
        await db.execute(delete(MetaLaunch).where(MetaLaunch.workspace_id == ids["workspace"]))
        await db.execute(delete(MetaCreative).where(MetaCreative.workspace_id == ids["workspace"]))
        await db.execute(delete(MetaTemplate).where(MetaTemplate.workspace_id == ids["workspace"]))
        for table in (MetaStatDaily, MetaEntity, MetaAdAccount):
            await db.execute(delete(table).where(table.connection_id == ids["connection"]))
        await db.execute(
            delete(KeitaroStatDaily).where(KeitaroStatDaily.connection_id == ids["tracker"])
        )
        await db.execute(
            delete(IntegrationConnection).where(
                IntegrationConnection.id.in_([ids["connection"], ids["tracker"]])
            )
        )
        await db.commit()


def test_budgets_go_to_meta_in_minor_units() -> None:
    assert money_to_minor(Decimal("50.00")) == "5000"
    assert money_to_minor(Decimal("12.345")) == "1235"
    assert money_to_minor(0) == "0"


async def test_instagram_is_dropped_when_page_has_no_ig_link(launch_setup) -> None:
    """Страница без связи с IG → Instagram из платформ убирается, а не падает
    залив с «Выберите IG-аккаунт или Page»."""
    from app.services.meta_launch import MetaLaunchPublisher

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.params.get("fields") and "instagram" in request.url.params["fields"]:
            return httpx.Response(
                200, json={"id": "900100", "name": "Page", "instagram_business_account": None}
            )
        return _write_handler(request)

    def factory(token, **kwargs):
        kwargs.pop("transport", None)
        return MetaClient(
            token, transport=httpx.MockTransport(handler), **kwargs
        )

    publisher = MetaLaunchPublisher(SessionLocal, factory)
    # Планы залива: обычные (без языков) — цепочка без изменения.
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.adset_count = 1
        await db.commit()
    await publisher.publish(str(launch_setup["launch"]), str(launch_setup["admin"]))

    async with SessionLocal() as db:
        requests = list(
            (
                await db.execute(
                    select(MetaOperation.kind, MetaOperation.request).where(
                        MetaOperation.launch_id == launch_setup["launch"]
                    )
                )
            ).all()
        )
    adset_request = {
        kind: request for kind, request in requests
    }["adset_create"]
    targeting = adset_request["targeting"]
    platforms = targeting.get("publisher_platforms") or []
    lowered = [str(item).lower() for item in platforms]
    assert "instagram" not in lowered
    assert "facebook" in lowered


def test_multi_language_rule_requires_a_numeric_locale() -> None:
    """Правило без числового ID локали не должно уйти в Meta: это тот самый
    код 100, из-за которого падал залив «языками»."""
    from app.services.meta import build_asset_feed_spec

    texts = [
        {"language": "en_US", "headline": "Buy", "primary_text": "Text",
         "link_url": "https://track.example/en"},
    ]
    with pytest.raises(ValueError) as raised:
        build_asset_feed_spec(texts, [], cta="LEARN_MORE")
    assert "числовой ID языка" in str(raised.value)


async def test_scheduled_budget_increase_applies_once(launch_setup) -> None:
    """Период увеличения бюджета: применяется один раз, к последнему значению."""
    from datetime import datetime, timedelta

    from app.services.budget_increase import apply_due_budget_increases
    from app.services.meta_launch import MetaLaunchPublisher

    await MetaLaunchPublisher(SessionLocal, client_factory=_client_factory()).publish(
        str(launch_setup["launch"]), str(launch_setup["admin"])
    )
    now = datetime.utcnow()
    budget_sets = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "daily_budget" in request.content.decode():
            budget_sets.append(dict(httpx.QueryParams(request.content.decode())))
        return _write_handler(request)

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.budget_increases = [
            {
                "start_at": (now - timedelta(minutes=5)).isoformat() + "Z",
                "end_at": (now + timedelta(hours=1)).isoformat() + "Z",
                "kind": "sum",
                "amount": 10,
                "applied": False,
            }
        ]
        await db.commit()

    from app.services.meta import MetaClient

    def factory(token, **kwargs):
        kwargs.pop("transport", None)
        return MetaClient(
            token, transport=httpx.MockTransport(handler), **kwargs
        )

    report = await apply_due_budget_increases(SessionLocal, factory)
    assert report["increased"] == 1
    # Базовый бюджет 50 + 10 = 60 долларов = 6000 центов.
    assert budget_sets and budget_sets[0]["daily_budget"] == "6000"

    report_again = await apply_due_budget_increases(SessionLocal, factory)
    assert report_again["increased"] == 0
    assert len(budget_sets) == 1

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        payload = launch.external_payload or {}
        assert payload.get("current_budget") == "60.00"
        assert launch.budget_increases[0]["applied"] is True


def test_multi_language_rule_uses_locales_and_default_flag() -> None:
    from app.services.meta import build_asset_feed_spec

    texts = [
        {"language": "en_US", "headline": "Buy", "primary_text": "Text",
         "link_url": "https://track.example/en"},
        {"language": "de_DE", "headline": "Kaufen", "primary_text": "Text DE",
         "link_url": "https://track.example/de"},
    ]
    spec = build_asset_feed_spec(texts, [], cta="LEARN_MORE", locale_ids={"en_US": 6, "de_DE": 3})
    rules = spec["asset_customization_rules"]
    assert rules[0]["customization_spec"] == {"locales": [6]}
    assert rules[0]["is_default"] is True
    assert rules[1]["customization_spec"] == {"locales": [3]}
    assert rules[1]["is_default"] is False


def test_targeting_omits_empty_placements() -> None:
    template = MetaTemplate(
        workspace_id=uuid.uuid4(),
        name="t",
        geo=["de", "at"],
        age_min=25,
        age_max=45,
        genders=[1],
        languages=[],
        placements={},
        interests=[],
    )
    targeting = build_targeting(template)
    # location_types Meta удалила (ошибка 100/1870194) — в таргетинг не уходит.
    assert targeting["geo_locations"] == {
        "countries": ["DE", "AT"],
    }
    assert targeting["genders"] == [1]
    # Пустых плейсментов быть не должно: для Meta пустой список — ошибка,
    # а отсутствие поля — автоматические плейсменты.
    assert "publisher_platforms" not in targeting


def test_tracking_link_gets_the_campaign_macro_once() -> None:
    link = campaign_id_macro_url("https://track.example/click?x=1", SUB_ID)
    assert link == "https://track.example/click?x=1&sub_id_5={{campaign.id}}"
    assert campaign_id_macro_url(link, SUB_ID) == link
    assert campaign_id_macro_url("https://track.example/c", SUB_ID).endswith(
        "?sub_id_5={{campaign.id}}"
    )


async def test_write_is_not_retried_after_a_lost_response() -> None:
    """Оборванный POST не повторяется: объект мог быть уже создан."""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ReadTimeout("connection lost", request=request)

    client = MetaClient("token", transport=httpx.MockTransport(handler))
    with pytest.raises(MetaError) as raised:
        await client.create_campaign(ACCOUNT_ID, name="x", objective="OUTCOME_LEADS")
    assert attempts["count"] == 1
    assert "Повторять его автоматически нельзя" in str(raised.value)


async def test_write_is_retried_when_meta_reports_a_rate_limit() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(
                400,
                json={"error": {"message": "limit reached", "code": 17}},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(200, json={"id": CAMPAIGN_ID})

    client = MetaClient("token", transport=httpx.MockTransport(handler))
    response = await client.create_campaign(ACCOUNT_ID, name="x", objective="OUTCOME_LEADS")
    assert response["id"] == CAMPAIGN_ID
    assert attempts["count"] == 2


async def test_image_upload_reads_the_hash_out_of_the_envelope() -> None:
    client = MetaClient("token", transport=httpx.MockTransport(_write_handler))
    uploaded = await client.upload_image(
        ACCOUNT_ID, file_name="b.jpg", content=b"x", mime_type="image/jpeg"
    )
    assert uploaded["hash"] == "abc123hash"


async def test_validation_names_every_problem_before_touching_meta(launch_setup) -> None:
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.link_url = None
        launch.daily_budget = Decimal("0")
        await db.commit()

        context = await load_launch_context(db, launch_setup["launch"])
        with pytest.raises(LaunchValidationError) as raised:
            validate_launch(context)
    message = str(raised.value)
    assert "ссылка" in message
    assert "бюджет" in message


async def test_publish_creates_the_full_chain_and_stays_paused(launch_setup) -> None:
    publisher = MetaLaunchPublisher(SessionLocal, client_factory=_client_factory())
    result = await publisher.publish(str(launch_setup["launch"]), str(launch_setup["admin"]))

    assert result["campaign_id"] == CAMPAIGN_ID
    assert result["adset_id"] == ADSET_ID
    assert result["ads"] == [AD_ID]
    # activate_on_publish не выставлен, значит деньги ещё не тратятся.
    assert result["status"] == "paused"

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        assert launch.status == LaunchStatus.paused
        assert launch.campaign_external_id == CAMPAIGN_ID
        assert launch.published_at is not None
        assert launch.last_error is None

        kinds = list(
            (
                await db.execute(
                    select(MetaOperation.kind).where(
                        MetaOperation.launch_id == launch_setup["launch"]
                    )
                )
            ).scalars()
        )
        # Порядок гарантирован кодом, а не временем создания: в SQLite у всех
        # четырёх записей совпадает секунда, поэтому сверяем состав.
        assert set(kinds) == {"campaign_create", "adset_create", "creative_create", "ad_create"}
        failed = await db.scalar(
            select(func.count())
            .select_from(MetaOperation)
            .where(
                MetaOperation.launch_id == launch_setup["launch"],
                MetaOperation.status != "success",
            )
        )
        assert failed == 0


async def test_republishing_resumes_instead_of_duplicating(launch_setup) -> None:
    """Кампания уже создана — второй раз её создавать нельзя."""
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.campaign_external_id = CAMPAIGN_ID
        launch.adset_external_id = ADSET_ID
        await db.commit()

    created = {"campaigns": 0, "adsets": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/campaigns"):
            created["campaigns"] += 1
        if request.method == "POST" and request.url.path.endswith("/adsets"):
            created["adsets"] += 1
        return _write_handler(request)

    publisher = MetaLaunchPublisher(SessionLocal, client_factory=_client_factory(handler))
    result = await publisher.publish(str(launch_setup["launch"]))

    assert created == {"campaigns": 0, "adsets": 0}
    assert result["ads"] == [AD_ID]


async def test_activation_is_a_separate_step(launch_setup) -> None:
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.activate_on_publish = True
        await db.commit()

    publisher = MetaLaunchPublisher(SessionLocal, client_factory=_client_factory())
    result = await publisher.publish(str(launch_setup["launch"]))
    assert result["status"] == "active"

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        assert launch.status == LaunchStatus.active
        activation = await db.scalar(
            select(MetaOperation).where(
                MetaOperation.launch_id == launch_setup["launch"],
                MetaOperation.kind == "campaign_activate",
            )
        )
        assert activation.status == "success"
        assert activation.target_external_id == CAMPAIGN_ID


async def test_a_failed_stage_leaves_the_launch_failed_with_a_reason(launch_setup) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/adsets"):
            return httpx.Response(
                400,
                json={"error": {"message": "Invalid targeting", "code": 100}},
            )
        return _write_handler(request)

    publisher = MetaLaunchPublisher(SessionLocal, client_factory=_client_factory(handler))
    with pytest.raises(MetaError):
        await publisher.publish(str(launch_setup["launch"]))

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        assert launch.status == LaunchStatus.failed
        assert launch.last_error
        # Кампания уже создана — её ID сохранён, иначе повтор создал бы вторую.
        assert launch.campaign_external_id == CAMPAIGN_ID
        assert launch.adset_external_id is None
        failed = await db.scalar(
            select(MetaOperation).where(
                MetaOperation.launch_id == launch_setup["launch"],
                MetaOperation.kind == "adset_create",
            )
        )
        assert failed.status == "failed"


def test_a_rule_stays_silent_when_the_metric_is_unknown() -> None:
    rule = MetaRule(
        workspace_id=uuid.uuid4(),
        name="ROI",
        conditions=[{"metric": "roi", "operator": "lt", "value": "0"}],
    )
    # Без атрибуции ROI не посчитан. None — это «не знаем», а не «ниже нуля».
    assert matches(rule, {"roi": None}) is False
    assert matches(rule, {"roi": -20.0}) is True
    assert matches(rule, {"roi": 15.0}) is False


def test_all_conditions_must_hold() -> None:
    rule = MetaRule(
        workspace_id=uuid.uuid4(),
        name="ROI и расход",
        conditions=[
            {"metric": "roi", "operator": "lt", "value": "0"},
            {"metric": "spend", "operator": "gte", "value": "100"},
        ],
    )
    assert matches(rule, {"roi": -20.0, "spend": 150.0}) is True
    # Второе условие не выполнено — правило молчит.
    assert matches(rule, {"roi": -20.0, "spend": 40.0}) is False
    # И неизвестное значение не спасает даже при выполненном втором.
    assert matches(rule, {"roi": None, "spend": 150.0}) is False


def test_a_rule_without_conditions_matches_everything() -> None:
    """«Остановить все активные объявления» — правило без условий."""
    rule = MetaRule(workspace_id=uuid.uuid4(), name="Стоп всем", conditions=[])
    assert matches(rule, {"roi": None, "spend": 0.0}) is True


async def _seed_stats(ids: dict, spend: Decimal, revenue: Decimal | None) -> None:
    today = datetime.now(UTC).date()
    async with SessionLocal() as db:
        db.add(
            MetaEntity(
                workspace_id=ids["workspace"],
                connection_id=ids["connection"],
                account_id=ids["account"],
                level="campaign",
                external_id=CAMPAIGN_ID,
                name="DE Nervio broad",
                effective_status="ACTIVE",
            )
        )
        db.add(
            MetaEntity(
                workspace_id=ids["workspace"],
                connection_id=ids["connection"],
                account_id=ids["account"],
                level="adset",
                external_id=ADSET_ID,
                parent_external_id=CAMPAIGN_ID,
                name="DE 25-45",
                daily_budget=Decimal("50.00"),
            )
        )
        db.add(
            MetaStatDaily(
                workspace_id=ids["workspace"],
                connection_id=ids["connection"],
                account_id=ids["account"],
                record_date=today,
                campaign_external_id=CAMPAIGN_ID,
                dimension_key=f"rule-{uuid.uuid4().hex}",
                impressions=10000,
                clicks=300,
                spend=spend,
            )
        )
        if revenue is not None:
            db.add(
                KeitaroStatDaily(
                    workspace_id=ids["workspace"],
                    connection_id=ids["tracker"],
                    record_date=today,
                    dimension_key=f"rule-k-{uuid.uuid4().hex}",
                    sub_values={f"sub{SUB_ID}": CAMPAIGN_ID},
                    leads=10,
                    sales=2,
                    revenue=revenue,
                )
            )
        await db.commit()


async def test_a_losing_campaign_is_paused_and_logged(launch_setup) -> None:
    await _seed_stats(launch_setup, Decimal("100.00"), Decimal("40.00"))
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.campaign_external_id = CAMPAIGN_ID
        launch.status = LaunchStatus.active
        db.add(
            MetaRule(
                workspace_id=launch_setup["workspace"],
                name="Стоп при минусе",
                account_id=launch_setup["account"],
                conditions=[{"metric": "roi", "operator": "lt", "value": "-30"}],
                window="today",
                entity_status="any",
                min_spend=Decimal("50"),
                action="pause",
                cooldown_minutes=180,
            )
        )
        await db.commit()

    paused = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith(f"/{CAMPAIGN_ID}"):
            paused["count"] += 1
        return _write_handler(request)

    engine = MetaRuleEngine(SessionLocal, client_factory=_client_factory(handler))
    # ROI = (40 - 100) / 100 = -60 %, порог -30 % — правило обязано сработать.
    first = await engine.run()
    assert first["triggered"] == 1
    assert first["applied"] == 1
    assert paused["count"] == 1

    async with SessionLocal() as db:
        event = await db.scalar(
            select(MetaRuleEvent).where(MetaRuleEvent.workspace_id == launch_setup["workspace"])
        )
        assert event.applied is True
        assert event.action == "pause"
        assert event.metric_value == Decimal("-60.00")
        assert event.campaign_name == "DE Nervio broad"

    # Второй прогон внутри окна молчит: иначе правило било бы по одной и той же
    # кампании каждые полчаса.
    second = await engine.run()
    assert second["triggered"] == 0
    assert paused["count"] == 1


async def test_low_spend_keeps_the_rule_quiet(launch_setup) -> None:
    await _seed_stats(launch_setup, Decimal("3.00"), Decimal("0.00"))
    async with SessionLocal() as db:
        db.add(
            MetaRule(
                workspace_id=launch_setup["workspace"],
                name="Порог расхода",
                account_id=launch_setup["account"],
                conditions=[{"metric": "roi", "operator": "lt", "value": "0"}],
                entity_status="any",
                min_spend=Decimal("50"),
                action="notify",
            )
        )
        await db.commit()

    engine = MetaRuleEngine(SessionLocal, client_factory=_client_factory())
    assert (await engine.run())["triggered"] == 0


async def test_budget_rule_raises_the_adset_and_never_below_the_floor(launch_setup) -> None:
    await _seed_stats(launch_setup, Decimal("100.00"), Decimal("400.00"))
    async with SessionLocal() as db:
        db.add(
            MetaRule(
                workspace_id=launch_setup["workspace"],
                name="Скейл",
                account_id=launch_setup["account"],
                conditions=[{"metric": "roi", "operator": "gt", "value": "100"}],
                entity_status="any",
                min_spend=Decimal("50"),
                action="increase_budget",
                action_value=Decimal("20"),
            )
        )
        await db.commit()

    sent: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith(f"/{ADSET_ID}"):
            body = request.content.decode()
            sent.append((request.url.path, body))
        return _write_handler(request)

    engine = MetaRuleEngine(SessionLocal, client_factory=_client_factory(handler))
    result = await engine.run()
    assert result["applied"] == 1
    # 50.00 + 20 % = 60.00, в Meta уходит 6000 центов.
    assert "daily_budget=6000" in sent[0][1]


async def test_rule_preview_shows_what_would_happen(launch_setup) -> None:
    await _seed_stats(launch_setup, Decimal("100.00"), Decimal("40.00"))
    async with SessionLocal() as db:
        rule = MetaRule(
            workspace_id=launch_setup["workspace"],
            name="Предпросмотр",
            account_id=launch_setup["account"],
            conditions=[{"metric": "roi", "operator": "lt", "value": "0"}],
            entity_status="any",
            min_spend=Decimal("10"),
            action="pause",
            is_enabled=False,
        )
        db.add(rule)
        await db.commit()
        rule_id = rule.id

    with _admin_client() as client:
        response = client.post(f"/api/v1/meta/rules/{rule_id}/preview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] == 1
    assert payload["level"] == "campaign"
    assert payload["objects"][0]["values"]["roi"] == -60.0

    # Предпросмотр ничего не выполняет и не пишет в журнал.
    async with SessionLocal() as db:
        events = await db.scalar(
            select(func.count())
            .select_from(MetaRuleEvent)
            .where(MetaRuleEvent.rule_id == rule_id)
        )
        assert events == 0


async def test_launch_api_rejects_a_creative_from_another_account(launch_setup) -> None:
    async with SessionLocal() as db:
        other = MetaAdAccount(
            workspace_id=launch_setup["workspace"],
            connection_id=launch_setup["connection"],
            external_id="act_999",
            name="Other account",
        )
        db.add(other)
        await db.flush()
        stray = MetaCreative(
            workspace_id=launch_setup["workspace"],
            account_id=other.id,
            kind="image",
            name="stray",
            external_hash="zzz",
        )
        db.add(stray)
        await db.commit()
        stray_id = str(stray.id)

    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/launches",
            json={
                "name": "Чужой креатив",
                "account_id": str(launch_setup["account"]),
                "daily_budget": "40.00",
                "link_url": "https://track.example/click",
                "creative_ids": [stray_id],
            },
        )
    assert response.status_code == 422
    assert "другой кабинет" in response.json()["error"]["message"]


async def test_published_launch_cannot_be_deleted_or_relinked(launch_setup) -> None:
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.campaign_external_id = CAMPAIGN_ID
        launch.status = LaunchStatus.active
        await db.commit()

    launch_id = str(launch_setup["launch"])
    with _admin_client() as client:
        deleted = client.delete(f"/api/v1/meta/launches/{launch_id}")
        relinked = client.patch(
            f"/api/v1/meta/launches/{launch_id}", json={"link_url": "https://other.example"}
        )
        renamed = client.patch(f"/api/v1/meta/launches/{launch_id}", json={"name": "Новое имя"})

    assert deleted.status_code == 422
    assert relinked.status_code == 422
    # Имя менять можно: оно живёт в CRM и на кампанию в Meta не влияет.
    assert renamed.status_code == 200


async def test_connection_preview_lists_accounts_without_saving(launch_setup, monkeypatch) -> None:
    """Шаг «Проверка» в мастере не создаёт ни подключения, ни кабинетов."""
    import app.api.routers.meta as meta_router

    def factory(access_token: str, **kwargs) -> MetaClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/me"):
                return httpx.Response(200, json={"id": "1", "name": "SU"})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "act_1",
                            "name": "Preview one",
                            "account_status": 1,
                            "currency": "EUR",
                            "timezone_name": "Europe/Berlin",
                            "amount_spent": "25000",
                            "spend_cap": "0",
                        },
                        {"id": "act_2", "name": "Preview two", "account_status": 2},
                    ]
                },
            )

        kwargs.pop("transport", None)
        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(meta_router, "MetaClient", factory)

    before = await _connection_count()
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/connections/preview",
            json={"access_token": "a-token-long-enough-for-schema"},
        )
    assert response.status_code == 200
    accounts = response.json()["accounts"]
    assert [row["external_id"] for row in accounts] == ["act_1", "act_2"]
    assert accounts[0]["currency"] == "EUR"
    assert accounts[0]["amount_spent"] == 250.0
    assert accounts[1]["account_status"] == "DISABLED"
    assert await _connection_count() == before

    # Сводка шага «Проверка»: столько кабинетов, БМов, страниц и кампаний
    # реально видно этим токеном.
    summary = response.json()["summary"]
    assert summary["ad_accounts"] == 2
    assert summary["active_ad_accounts"] == 1
    assert summary["currencies"] == ["EUR", "USD"]
    assert summary["campaigns_partial"] is False
    assert summary["campaigns"] == 4


async def test_the_preview_survives_a_token_without_business_rights(
    launch_setup, monkeypatch
) -> None:
    """Без business_management БМы и страницы не отдаются вовсе.

    Это не сломанное подключение: кабинеты видны, работать можно. Поэтому шаг
    «Проверка» должен показать прочерк в этих плитках, а не упасть целиком.
    """
    import app.api.routers.meta as meta_router

    def factory(access_token: str, **kwargs) -> MetaClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/me"):
                return httpx.Response(200, json={"id": "1", "name": "SU"})
            if request.url.path.endswith(("/businesses", "/accounts")):
                return httpx.Response(
                    403,
                    json={"error": {"message": "(#200) business_management", "code": 200}},
                )
            if request.url.path.endswith("/campaigns"):
                return httpx.Response(200, json={"data": [{"id": "c1", "name": "К"}]})
            return httpx.Response(
                200,
                json={"data": [{"id": "act_1", "name": "Один", "account_status": 1,
                                "currency": "USD"}]},
            )

        kwargs.pop("transport", None)
        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(meta_router, "MetaClient", factory)

    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/connections/preview",
            json={"access_token": "a-token-long-enough-for-schema"},
        )

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["ad_accounts"] == 1
    assert summary["businesses"] is None
    assert summary["pages"] is None
    assert summary["campaigns"] == 1


async def test_the_import_step_disables_the_accounts_nobody_picked(
    launch_setup, monkeypatch
) -> None:
    import app.api.routers.meta as meta_router

    def factory(access_token: str, **kwargs) -> MetaClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/me"):
                return httpx.Response(200, json={"id": "1", "name": "SU"})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "act_keep", "name": "Нужный", "account_status": 1,
                         "currency": "USD"},
                        {"id": "act_skip", "name": "Лишний", "account_status": 1,
                         "currency": "USD"},
                    ]
                },
            )

        kwargs.pop("transport", None)
        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(meta_router, "MetaClient", factory)

    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/connections",
            json={
                "name": "Мастер подключения",
                "access_token": "a-token-long-enough-for-schema",
                "attribution_sub_id": 3,
                "import_accounts": ["act_keep"],
            },
        )
    assert response.status_code == 201
    connection_id = uuid.UUID(response.json()["id"])

    try:
        async with SessionLocal() as db:
            rows = {
                account.external_id: account.status
                for account in (
                    await db.execute(
                        select(MetaAdAccount).where(
                            MetaAdAccount.connection_id == connection_id
                        )
                    )
                ).scalars()
            }
        # Невыбранный кабинет не исчезает, а заводится выключенным: так видно,
        # что он существует, но синхронизация его не трогает.
        assert rows == {"act_keep": Status.active, "act_skip": Status.inactive}
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(MetaAdAccount).where(MetaAdAccount.connection_id == connection_id)
            )
            await db.execute(
                delete(IntegrationConnection).where(IntegrationConnection.id == connection_id)
            )
            await db.commit()


async def test_template_geo_must_be_country_codes() -> None:
    with _admin_client() as client:
        bad = client.post(
            "/api/v1/meta/templates",
            json={"name": "Плохое гео", "geo": ["Германия"]},
        )
        good = client.post(
            "/api/v1/meta/templates",
            json={"name": "Хорошее гео", "geo": ["de", "at"]},
        )
    assert bad.status_code == 422
    assert good.status_code == 201
    assert good.json()["geo"] == ["DE", "AT"]

    async with SessionLocal() as db:
        await db.execute(delete(MetaTemplate).where(MetaTemplate.name == "Хорошее гео"))
        await db.commit()


async def test_budget_rules_require_a_percent() -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/rules",
            json={
                "name": "Без процента",
                "metric": "roi",
                "operator": "gt",
                "threshold": "100",
                "action": "increase_budget",
            },
        )
    assert response.status_code == 422


async def test_rule_conditions_accept_the_whole_form_vocabulary() -> None:
    """Форма правил предлагает настройки объекта и «содержит» — API их принимает.

    Раньше схема условия знала четырнадцать метрик и пять операторов, а форма
    показывала все, что умеет движок: половина выбранного отдавала 422 уже на
    сохранении.
    """
    with _admin_client() as client:
        good = client.post(
            "/api/v1/meta/rules",
            json={
                "name": "Стоп по названию кампании",
                "level": "campaign",
                "action": "pause",
                "conditions": [
                    {"metric": "campaign_name", "operator": "in", "value": "test"},
                    {"metric": "result_cr", "operator": "lt", "value": "1.5"},
                ],
            },
        )
        text_with_number_operator = client.post(
            "/api/v1/meta/rules",
            json={
                "name": "Текст с числовым оператором",
                "action": "pause",
                "conditions": [
                    {"metric": "campaign_name", "operator": "lt", "value": "test"}
                ],
            },
        )
        empty_threshold = client.post(
            "/api/v1/meta/rules",
            json={
                "name": "Пустой порог",
                "action": "pause",
                "conditions": [{"metric": "roi", "operator": "lt", "value": ""}],
            },
        )
    assert good.status_code == 201
    assert text_with_number_operator.status_code == 422
    assert empty_threshold.status_code == 422

    async with SessionLocal() as db:
        await db.execute(
            delete(MetaRule).where(MetaRule.name == "Стоп по названию кампании")
        )
        await db.commit()


async def test_rules_reject_actions_the_level_cannot_perform() -> None:
    """Бюджет и ставка живут не на каждом уровне — форма и API согласны в этом."""
    with _admin_client() as client:
        budget_on_ad = client.post(
            "/api/v1/meta/rules",
            json={
                "name": "Бюджет объявления",
                "level": "ad",
                "action": "change_budget",
                "action_value": "20",
            },
        )
        bid_on_campaign = client.post(
            "/api/v1/meta/rules",
            json={
                "name": "Ставка кампании",
                "level": "campaign",
                "action": "change_bid",
                "action_value": "5",
            },
        )
        campaign_without_id = client.post(
            "/api/v1/meta/rules",
            json={
                "name": "Кампания не выбрана",
                "scope_kind": "campaign",
                "level": "adset",
                "action": "pause",
            },
        )
    assert budget_on_ad.status_code == 422
    assert bid_on_campaign.status_code == 422
    assert campaign_without_id.status_code == 422


async def test_sync_pulls_the_campaign_status_back_into_the_launch(launch_setup) -> None:
    """Кампанию остановили в Ads Manager — залив обязан это показать."""
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.campaign_external_id = CAMPAIGN_ID
        launch.status = LaunchStatus.active
        await db.commit()

    from app.services.meta_sync import MetaSyncEngine

    engine = MetaSyncEngine(SessionLocal)
    await engine._sync_launch_statuses(
        {"workspace_id": launch_setup["workspace"]},
        [{"id": CAMPAIGN_ID, "effective_status": "PAUSED"}],
    )

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        assert launch.status == LaunchStatus.paused


async def test_a_draft_launch_is_not_overwritten_by_sync(launch_setup) -> None:
    from app.services.meta_sync import MetaSyncEngine

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.campaign_external_id = CAMPAIGN_ID
        await db.commit()

    engine = MetaSyncEngine(SessionLocal)
    await engine._sync_launch_statuses(
        {"workspace_id": launch_setup["workspace"]},
        [{"id": CAMPAIGN_ID, "effective_status": "ACTIVE"}],
    )

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        assert launch.status == LaunchStatus.draft


async def _connection_count() -> int:
    async with SessionLocal() as db:
        return await db.scalar(
            select(func.count())
            .select_from(IntegrationConnection)
            .where(IntegrationConnection.kind == "meta")
        )


async def _second_account(ids: dict, name: str = "Launch account 2") -> uuid.UUID:
    """Второй кабинет того же подключения — чтобы заливать пачкой."""
    async with SessionLocal() as db:
        account = MetaAdAccount(
            workspace_id=ids["workspace"],
            connection_id=ids["connection"],
            external_id="act_777000333",
            name=name,
            currency="USD",
            owner_id=ids["admin"],
        )
        db.add(account)
        await db.commit()
        return account.id


async def test_a_batch_creates_one_launch_per_account(launch_setup) -> None:
    second = await _second_account(launch_setup)
    async with SessionLocal() as db:
        creative = MetaCreative(
            workspace_id=launch_setup["workspace"],
            account_id=second,
            kind="image",
            name="banner-02",
            external_hash="def456hash",
        )
        db.add(creative)
        await db.commit()
        second_creative = creative.id

    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/launches/batch",
            json={
                "name": "DE Nervio batch",
                "account_ids": [str(launch_setup["account"]), str(second)],
                "creatives_by_account": {
                    str(launch_setup["account"]): [str(launch_setup["creative"])],
                    str(second): [str(second_creative)],
                },
                "template_id": str(launch_setup["template"]),
                "geo": "DE",
                "daily_budget": "40",
                "link_url": "https://track.example/click?x=2",
                "primary_text": "Текст",
                "headline": "Заголовок",
                "publish": False,
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["created"] == 2
    assert payload["queued"] == 0
    assert all(row["ok"] for row in payload["results"])

    async with SessionLocal() as db:
        launches = list(
            (
                await db.execute(
                    select(MetaLaunch).where(MetaLaunch.name == "DE Nervio batch")
                )
            ).scalars()
        )
        assert {launch.account_id for launch in launches} == {
            launch_setup["account"], second
        }
        # Каждому заливу достался креатив своего кабинета, а не общий список.
        links = dict(
            (
                await db.execute(
                    select(MetaLaunchCreative.launch_id, MetaLaunchCreative.creative_id).where(
                        MetaLaunchCreative.launch_id.in_([row.id for row in launches])
                    )
                )
            ).all()
        )
        by_account = {launch.account_id: links[launch.id] for launch in launches}
        assert by_account[launch_setup["account"]] == launch_setup["creative"]
        assert by_account[second] == second_creative


async def test_a_creative_from_another_account_is_rejected(launch_setup) -> None:
    second = await _second_account(launch_setup, "Launch account 3")
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/launches/batch",
            json={
                "name": "DE Nervio wrong creative",
                "account_ids": [str(second)],
                # Креатив загружен в первый кабинет — во втором у него другой хэш.
                "creatives_by_account": {str(second): [str(launch_setup["creative"])]},
                "link_url": "https://track.example/click?x=3",
            },
        )
    assert response.status_code == 422
    assert "другой кабинет" in response.json()["error"]["message"]


async def test_a_failing_account_does_not_cancel_the_rest(
    launch_setup, monkeypatch
) -> None:
    """Кабинет без креативов не проходит проверку, остальные уходят в очередь."""
    from app.api.routers import meta as meta_router

    queued: list[str] = []
    monkeypatch.setattr(
        meta_router.publish_meta_launch,
        "delay",
        lambda launch_id, user_id=None: queued.append(launch_id),
    )
    second = await _second_account(launch_setup, "Launch account 4")
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/launches/batch",
            json={
                "name": "DE Nervio mixed",
                "account_ids": [str(launch_setup["account"]), str(second)],
                "creatives_by_account": {
                    str(launch_setup["account"]): [str(launch_setup["creative"])]
                },
                "template_id": str(launch_setup["template"]),
                "daily_budget": "40",
                "link_url": "https://track.example/click?x=4",
                "primary_text": "Текст",
                "headline": "Заголовок",
                "publish": True,
            },
        )

    payload = response.json()
    assert payload["created"] == 2
    assert payload["queued"] == 1
    by_account = {row["account_name"]: row for row in payload["results"]}
    assert by_account["Launch account"]["ok"] is True
    assert by_account["Launch account 4"]["ok"] is False
    assert "креатив" in by_account["Launch account 4"]["error"].lower()

    # В воркер ушёл ровно один залив — тот, что прошёл проверку.
    assert len(queued) == 1
    # Заготовка кабинета, не прошедшего проверку, всё равно сохранена.
    async with SessionLocal() as db:
        stored = list(
            (
                await db.execute(
                    select(MetaLaunch).where(MetaLaunch.name == "DE Nervio mixed")
                )
            ).scalars()
        )
        assert len(stored) == 2


async def test_batch_string_budget_override_is_coerced(launch_setup, monkeypatch) -> None:
    """Бюджет кабинета приходит строкой из формы — валидация не должна падать."""
    from app.api.routers import meta as meta_router

    queued: list[str] = []
    monkeypatch.setattr(
        meta_router.publish_meta_launch,
        "delay",
        lambda launch_id, user_id=None: queued.append(launch_id),
    )
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/launches/batch",
            json={
                "name": "Override budget",
                "account_ids": [str(launch_setup["account"])],
                "creatives_by_account": {
                    str(launch_setup["account"]): [str(launch_setup["creative"])]
                },
                "overrides": {
                    str(launch_setup["account"]): {"daily_budget": "25.5"},
                },
                "template_id": str(launch_setup["template"]),
                "geo": "DE",
                "daily_budget": "40",
                "link_url": "https://track.example/click",
                "primary_text": "Текст",
                "headline": "Заголовок",
                "publish": True,
            },
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["queued"] == 1
    assert all(row["ok"] for row in payload["results"])


async def test_batch_overrides_persist_naming_and_dsa_fields(
    launch_setup, monkeypatch
) -> None:
    """Дополнительные поля мастера («Кастомный нейминг», «Параметры URL»,
    «Отображаемый URL», «Бенефициар») доезжают до залива каждого кабинета."""
    from app.api.routers import meta as meta_router

    queued: list[str] = []
    monkeypatch.setattr(
        meta_router.publish_meta_launch,
        "delay",
        lambda launch_id, user_id=None: queued.append(launch_id),
    )
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/launches/batch",
            json={
                "name": "DE Nervio extras",
                "account_ids": [str(launch_setup["account"])],
                "creatives_by_account": {
                    str(launch_setup["account"]): [str(launch_setup["creative"])]
                },
                "overrides": {
                    str(launch_setup["account"]): {
                        "campaign_name": "{{cab.name}} — test 1",
                        "url_tags": "utm_source=fb&utm_campaign={{campaign.id}}",
                        "display_link": "example.com",
                        "beneficiary": "Falasca Ryleigh Brannock",
                    },
                },
                "template_id": str(launch_setup["template"]),
                "geo": "DE",
                "daily_budget": "40",
                "link_url": "https://track.example/click",
                "primary_text": "Текст",
                "headline": "Заголовок",
                "publish": True,
            },
        )
    assert response.status_code == 201
    assert all(row["ok"] for row in response.json()["results"])

    async with SessionLocal() as db:
        stored = await db.get(MetaLaunch, uuid.UUID(queued[0]))
        assert stored.campaign_name == "{{cab.name}} — test 1"
        assert stored.url_tags == "utm_source=fb&utm_campaign={{campaign.id}}"
        assert stored.display_link == "example.com"
        assert stored.beneficiary == "Falasca Ryleigh Brannock"


async def test_publish_sends_dsa_beneficiary_and_custom_campaign_name(
    launch_setup,
) -> None:
    """Бенефициар уходит в оба DSA-поля адсета, а кастомный нейминг сильнее
    шаблона связки: макросы в нём разворачиваются."""
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.campaign_name = "{{cab.name}} — test 1"
        launch.beneficiary = "Falasca Ryleigh Brannock"
        await db.commit()

    await MetaLaunchPublisher(SessionLocal, client_factory=_client_factory()).publish(
        str(launch_setup["launch"]), str(launch_setup["admin"])
    )

    async with SessionLocal() as db:
        requests = list(
            (
                await db.execute(
                    select(MetaOperation.kind, MetaOperation.request).where(
                        MetaOperation.launch_id == launch_setup["launch"]
                    )
                )
            ).all()
        )
    by_kind = {kind: request for kind, request in requests}
    # Одно значение из мастера — оба поля DSA-прозрачности на адсете.
    assert by_kind["adset_create"]["dsa_beneficiary"] == "Falasca Ryleigh Brannock"
    assert by_kind["adset_create"]["dsa_payor"] == "Falasca Ryleigh Brannock"
    # Кастомный нейминг перекрыл шаблон связки, макрос кабинета развёрнут.
    assert by_kind["campaign_create"]["name"] == "Launch account — test 1"


async def test_dsa_recommendations_come_from_the_ad_account(
    launch_setup, monkeypatch
) -> None:
    """Селект «Бенефициар / Плательщик» питается подсказками самого кабинета."""
    from app.api.routers import meta as meta_router

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v23.0/{ACCOUNT_ID}"
        assert "dsa_recommendations" in request.url.params["fields"]
        return httpx.Response(
            200,
            json={
                "dsa_recommendations": {
                    "recommendations": ["Falasca Ryleigh Brannock", "ACME GmbH"]
                }
            },
        )

    monkeypatch.setattr(meta_router, "MetaClient", _client_factory(handler))
    with _admin_client() as client:
        response = client.get(
            f"/api/v1/meta/accounts/{launch_setup['account']}/dsa-recommendations"
        )
    assert response.status_code == 200
    assert response.json() == {
        "recommendations": ["Falasca Ryleigh Brannock", "ACME GmbH"]
    }


async def test_the_queue_lists_operations_with_their_launch(launch_setup) -> None:
    await MetaLaunchPublisher(SessionLocal, client_factory=_client_factory()).publish(
        str(launch_setup["launch"]), str(launch_setup["admin"])
    )
    with _admin_client() as client:
        queue = client.get("/api/v1/meta/operations")
        only_success = client.get("/api/v1/meta/operations?status=success")

    assert queue.status_code == 200
    rows = queue.json()["items"]
    assert rows, "публикация должна оставить след в очереди"
    assert {row["launch_name"] for row in rows} == {"DE Nervio broad"}
    assert {row["account_name"] for row in rows} == {"Launch account"}
    assert {row["kind"] for row in rows} >= {"campaign_create", "adset_create"}
    assert all(row["status"] == "success" for row in only_success.json()["items"])


async def test_a_rule_can_work_at_the_ad_level(launch_setup) -> None:
    """Уровень «объявление»: метрики и действие берутся по объявлению."""
    await _seed_stats(launch_setup, Decimal("100.00"), Decimal("40.00"))
    async with SessionLocal() as db:
        db.add(
            MetaEntity(
                workspace_id=launch_setup["workspace"],
                connection_id=launch_setup["connection"],
                account_id=launch_setup["account"],
                level="ad",
                external_id=AD_ID,
                parent_external_id=ADSET_ID,
                name="creative-01",
                effective_status="ACTIVE",
            )
        )
        # Строка статистики с ID объявления: на уровне «ad» группировка идёт
        # по нему, а общая фикстура пишет только кампанию.
        db.add(
            MetaStatDaily(
                workspace_id=launch_setup["workspace"],
                connection_id=launch_setup["connection"],
                account_id=launch_setup["account"],
                record_date=datetime.now(UTC).date(),
                campaign_external_id=CAMPAIGN_ID,
                adset_external_id=ADSET_ID,
                ad_external_id=AD_ID,
                dimension_key=f"rule-ad-{uuid.uuid4().hex}",
                impressions=10000,
                clicks=300,
                spend=Decimal("100.00"),
            )
        )
        rule = MetaRule(
            workspace_id=launch_setup["workspace"],
            name="Стоп объявлению",
            account_id=launch_setup["account"],
            level="ad",
            conditions=[{"metric": "spend", "operator": "gte", "value": "50"}],
            min_spend=Decimal("10"),
            action="pause",
            is_enabled=False,
        )
        db.add(rule)
        await db.commit()
        rule_id = rule.id

    with _admin_client() as client:
        preview = client.post(f"/api/v1/meta/rules/{rule_id}/preview").json()
        # Доход из трекера привязан к ID кампании, поэтому ниже кампании ROI
        # неизвестен — правило по нему обязано промолчать, а не выключить всё.
        client.patch(
            f"/api/v1/meta/rules/{rule_id}",
            json={"conditions": [{"metric": "roi", "operator": "lt", "value": "0"}]},
        )
        by_roi = client.post(f"/api/v1/meta/rules/{rule_id}/preview").json()

    assert preview["level"] == "ad"
    assert preview["matched"] == 1
    assert preview["objects"][0]["external_id"] == AD_ID
    assert preview["objects"][0]["name"] == "creative-01"
    assert by_roi["scanned"] == 1
    assert by_roi["matched"] == 0


async def test_the_status_filter_skips_paused_objects(launch_setup) -> None:
    await _seed_stats(launch_setup, Decimal("100.00"), Decimal("40.00"))
    async with SessionLocal() as db:
        campaign = await db.scalar(
            select(MetaEntity).where(
                MetaEntity.external_id == CAMPAIGN_ID, MetaEntity.level == "campaign"
            )
        )
        campaign.effective_status = "PAUSED"
        rule = MetaRule(
            workspace_id=launch_setup["workspace"],
            name="Только активные",
            account_id=launch_setup["account"],
            entity_status="active",
            conditions=[{"metric": "roi", "operator": "lt", "value": "0"}],
            min_spend=Decimal("10"),
            action="notify",
            is_enabled=False,
        )
        db.add(rule)
        await db.commit()
        rule_id = rule.id

    with _admin_client() as client:
        active_only = client.post(f"/api/v1/meta/rules/{rule_id}/preview").json()
        client.patch(f"/api/v1/meta/rules/{rule_id}", json={"entity_status": "paused"})
        paused_only = client.post(f"/api/v1/meta/rules/{rule_id}/preview").json()
        client.patch(f"/api/v1/meta/rules/{rule_id}", json={"entity_status": "any"})
        anything = client.post(f"/api/v1/meta/rules/{rule_id}/preview").json()

    assert active_only["scanned"] == 0
    assert paused_only["matched"] == 1
    assert anything["matched"] == 1


async def test_a_budget_rule_is_rejected_at_the_ad_level() -> None:
    """У объявления нет своего бюджета — правило не должно сохраниться."""
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/rules",
            json={
                "name": "Скейл объявления",
                "level": "ad",
                "action": "increase_budget",
                "action_value": "20",
                "conditions": [{"metric": "roi", "operator": "gt", "value": "100"}],
            },
        )
    assert response.status_code == 422
    fields = response.json()["error"]["details"]["fields"]
    assert any("бюджет" in field["message"].lower() for field in fields)


def test_the_window_preset_turns_into_a_date_range() -> None:
    from datetime import date as _date

    from app.services.meta_rules import window_range

    today = _date(2026, 8, 7)
    assert window_range("today", today) == (today, today)
    # «Вчера» — именно вчерашний день, а не два дня подряд.
    assert window_range("yesterday", today) == (_date(2026, 8, 6), _date(2026, 8, 6))
    assert window_range("last_3d", today) == (_date(2026, 8, 5), today)
    assert window_range("last_30d", today) == (_date(2026, 7, 9), today)


async def test_the_rule_reference_lists_levels_windows_and_operators() -> None:
    with _admin_client() as client:
        reference = client.get("/api/v1/meta/reference").json()

    assert set(reference["rule_levels"]) == {"campaign", "adset", "ad"}
    assert set(reference["rule_statuses"]) == {"active", "paused", "any"}
    assert "last_7d" in reference["rule_windows"]
    assert set(reference["rule_operators"]) == {
        "lt", "lte", "gt", "gte", "eq", "ne", "in", "nin"
    }
    assert "60" in reference["rule_frequencies"]


def test_hourly_rows_fold_into_twenty_four_buckets() -> None:
    from app.services.meta import HOURLY_BREAKDOWN
    from app.services.meta_hourly import fold, summarize

    rows = [
        {HOURLY_BREAKDOWN: "13:00:00 - 13:59:59", "spend": "12.50",
         "impressions": "1000", "clicks": "40", "inline_link_clicks": "30"},
        # Второй день того же периода складывается в тот же час.
        {HOURLY_BREAKDOWN: "13:00:00 - 13:59:59", "spend": "7.50",
         "impressions": "500", "clicks": "10", "inline_link_clicks": "8"},
        {HOURLY_BREAKDOWN: "02:00:00 - 02:59:59", "spend": "1.00",
         "impressions": "100", "clicks": "2", "inline_link_clicks": "1"},
        # Строку без часа отбрасываем, а не приписываем к полуночи.
        {"spend": "999.00", "impressions": "1", "clicks": "1"},
    ]
    buckets = fold(rows)
    assert len(buckets) == 24
    assert [bucket["hour"] for bucket in buckets] == list(range(24))
    assert buckets[13]["spend"] == 20.0
    assert buckets[13]["impressions"] == 1500
    assert buckets[13]["link_clicks"] == 38
    assert buckets[2]["spend"] == 1.0
    assert buckets[0]["spend"] == 0.0

    totals = summarize(buckets)
    assert totals["spend"] == 21.0
    assert totals["peak_hour"] == 13
    assert totals["peak_spend"] == 20.0


def test_an_empty_day_reports_no_peak() -> None:
    """Пик в 00:00 на нуле читался бы как факт, хотя это просто первая корзина."""
    from app.services.meta_hourly import empty_hours, summarize

    totals = summarize(empty_hours())
    assert totals["spend"] == 0
    assert totals["peak_hour"] is None
    assert totals["peak_spend"] is None


def test_the_hour_is_parsed_from_the_meta_range() -> None:
    from app.services.meta import HOURLY_BREAKDOWN, hour_of

    assert hour_of({HOURLY_BREAKDOWN: "00:00:00 - 00:59:59"}) == 0
    assert hour_of({HOURLY_BREAKDOWN: "23:00:00 - 23:59:59"}) == 23
    assert hour_of({HOURLY_BREAKDOWN: ""}) is None
    assert hour_of({}) is None
    # Мусор не превращается в нулевой час.
    assert hour_of({HOURLY_BREAKDOWN: "утро"}) is None
    assert hour_of({HOURLY_BREAKDOWN: "77:00:00 - 77:59:59"}) is None


async def test_hourly_spend_is_returned_for_a_visible_ad(launch_setup, monkeypatch) -> None:
    from app.api.routers import meta as meta_router
    from app.services.meta import HOURLY_BREAKDOWN

    async with SessionLocal() as db:
        account = await db.get(MetaAdAccount, launch_setup["account"])
        account.timezone_name = "Europe/Kiev"
        db.add(
            MetaEntity(
                workspace_id=launch_setup["workspace"],
                connection_id=launch_setup["connection"],
                account_id=launch_setup["account"],
                level="ad",
                external_id=AD_ID,
                parent_external_id=ADSET_ID,
                name="creative-01",
                effective_status="ACTIVE",
            )
        )
        await db.commit()

    asked: list[str] = []

    class FakeClient:
        def __init__(self, token: str, **kwargs) -> None:
            self.token = token

        async def hourly_insights(self, external_id, start, end):
            asked.append(external_id)
            return [
                {HOURLY_BREAKDOWN: "19:00:00 - 19:59:59", "spend": "40.00",
                 "impressions": "2000", "clicks": "80", "inline_link_clicks": "60"}
            ]

    monkeypatch.setattr(meta_router, "MetaClient", FakeClient)

    with _admin_client() as client:
        response = client.get(
            f"/api/v1/meta/insights/hourly?external_id={AD_ID}&level=ads"
            "&date_from=2026-07-01&date_to=2026-07-01&refresh=true"
        )
        unknown = client.get(
            "/api/v1/meta/insights/hourly?external_id=nope&level=ads&refresh=true"
        )
        wrong_level = client.get(
            f"/api/v1/meta/insights/hourly?external_id={AD_ID}&level=bananas&refresh=true"
        )

    assert response.status_code == 200
    payload = response.json()
    assert asked == [AD_ID]
    assert payload["name"] == "creative-01"
    assert payload["account_name"] == "Launch account"
    # Таймзона кабинета едет рядом с числами: часы Meta считает по ней.
    assert payload["timezone"] == "Europe/Kiev"
    assert len(payload["hours"]) == 24
    assert payload["hours"][19]["spend"] == 40.0
    assert payload["totals"]["peak_hour"] == 19

    # Чужой или несуществующий объект не отдаёт чужой расход.
    assert unknown.status_code == 404
    assert wrong_level.status_code == 422


async def test_a_buyer_cannot_read_hours_of_someone_elses_account(launch_setup) -> None:
    """Кабинет не закреплён за баером — по ID объявления часы не достать."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        role = await db.scalar(
            select(Role).where(Role.workspace_id == admin.workspace_id, Role.name == "Buyer")
        )
        db.add(
            MetaEntity(
                workspace_id=launch_setup["workspace"],
                connection_id=launch_setup["connection"],
                account_id=launch_setup["account"],
                level="ad",
                external_id=AD_ID,
                parent_external_id=ADSET_ID,
                name="creative-01",
            )
        )
        db.add(
            User(
                workspace_id=admin.workspace_id,
                role_id=role.id,
                name="Hourly Buyer",
                login="meta-hourly-buyer",
                password_hash=hash_password("test-password"),
            )
        )
        await db.commit()

    try:
        client = TestClient(app)
        assert client.post(
            "/api/v1/auth/login",
            json={"login": "meta-hourly-buyer", "password": "test-password"},
        ).status_code == 200
        with client:
            denied = client.get(
                f"/api/v1/meta/insights/hourly?external_id={AD_ID}&level=ads&refresh=true"
            )
        assert denied.status_code == 404
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.login == "meta-hourly-buyer"))
            await db.commit()


async def test_the_connection_remembers_how_the_token_was_issued(launch_setup) -> None:
    """Способ выпуска токена хранится у подключения и виден в проверке.

    На запросы к Graph API он не влияет — влияет на то, что сказать человеку,
    когда токен умрёт: системный живёт бессрочно, а токен сессии — до первого
    выхода из устройств.
    """
    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, launch_setup["connection"])
        # Подключения, заведённые до появления поля, считаются системными.
        assert connection.auth_method == "system_user"
        connection.auth_method = "session"
        await db.commit()

    with _admin_client() as client:
        listed = client.get("/api/v1/meta/connections").json()["items"]
        row = [item for item in listed if item["id"] == str(launch_setup["connection"])][0]
        checked = client.post(
            f"/api/v1/meta/connections/{launch_setup['connection']}/check"
        )

    assert row["auth_method"] == "session"
    assert checked.status_code in {200, 422, 502}
    if checked.status_code == 200:
        body = checked.json()
        assert body["auth_method"] == "session"
        assert "сессии" in body["auth_method_hint"]
