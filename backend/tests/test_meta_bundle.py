"""Связка: цель, макросы, spintax, бюджет и время залива (ТЗ 3.3–3.5)."""

import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models import (
    LaunchStatus,
    MetaAdAccount,
    MetaCreative,
    MetaLaunch,
    MetaOperation,
    MetaTemplate,
)
from app.services.meta import build_targeting
from app.services.meta_bundle import render_macros, spin
from app.services.meta_launch import (
    LaunchValidationError,
    MetaLaunchPublisher,
    load_launch_context,
    validate_launch,
)
from tests.test_media_finance import _admin_client
from tests.test_meta_launch import (
    _client_factory,
    _write_handler,
    launch_setup,  # noqa: F401 — фикстура переиспользуется целиком
)


def test_macros_fill_the_name_and_unknown_ones_disappear() -> None:
    rendered = render_macros(
        "{{bundle.name}} | {{geo}} | adset #{{adset.number}} | {{nope}}",
        {"bundle.name": "Nervio", "geo": "DE", "adset.number": 3},
    )
    # Неизвестный макрос выбрасывается: «{{nope}}» в кабинете выглядел бы
    # опечаткой баера, а чинить её пришлось бы уже в Meta.
    assert rendered == "Nervio | DE | adset #3 |"


def test_spintax_picks_a_variant_per_call() -> None:
    text = "{Купи|Закажи} {сейчас|сегодня}"
    first = spin(text, random.Random(1))
    second = spin(text, random.Random(2))
    for value in (first, second):
        assert value.split()[0] in {"Купи", "Закажи"}
        assert value.split()[1] in {"сейчас", "сегодня"}
    # Незакрытая скобка не должна крутить цикл вечно — текст возвращается как есть.
    assert spin("{a|{b", random.Random(1)).endswith("b")


def test_targeting_carries_devices_and_exclusions() -> None:
    template = MetaTemplate(
        workspace_id=uuid.uuid4(),
        name="t",
        geo=["DE"],
        age_min=25,
        age_max=45,
        genders=[],
        languages=[],
        placements={"publisher_platforms": ["facebook"]},
        interests=[{"id": "6003", "name": "Спорт"}],
        settings={
            "adset": {
                "location_type": "recent",
                "excluded_geo": ["AT"],
                "excluded_interests": [{"id": "6004", "name": "Дети"}],
                "auto_placements": True,
                "os": "android",
                "android_smartphone": True,
                "android_tablet": False,
                "android_min": "10.0",
                "wifi_only": True,
            }
        },
    )
    targeting = build_targeting(template)
    assert "location_types" not in targeting["geo_locations"]
    assert targeting["excluded_geo_locations"] == {"countries": ["AT"]}
    assert targeting["exclusions"]["interests"][0]["id"] == "6004"
    assert targeting["user_os"] == ["Android_ver_10.0_and_above"]
    assert targeting["user_device"] == ["Android_Smartphone"]
    assert targeting["wireless_carrier"] == ["Wifi"]
    # Авто-плейсменты сильнее выбранных вручную: иначе связка обещала бы Meta
    # автоматику и тут же её отменяла.
    assert "publisher_platforms" not in targeting


async def test_the_goal_decides_what_meta_gets(launch_setup) -> None:
    with _admin_client() as client:
        created = client.post(
            "/api/v1/meta/templates",
            json={
                "name": f"Связка {uuid.uuid4().hex[:6]}",
                "geo": ["DE"],
                "daily_budget": "80",
                "settings": {
                    "campaign": {"goal": "traffic", "budget_kind": "lifetime"},
                    "ad": {"headline": "{Купи|Закажи}"},
                },
            },
        )

    assert created.status_code == 201, created.text
    row = created.json()
    # Цель в интерфейсе одна, а в Meta это пара «objective + оптимизация»:
    # сервер раскладывает её сам, чтобы они не разъехались при правке.
    assert row["objective"] == "OUTCOME_TRAFFIC"
    assert row["optimization_goal"] == "LINK_CLICKS"
    # Тип бюджета выбирает поле, а не заводит вторую сумму: оба поля разом Meta
    # не принимает.
    assert row["daily_budget"] is None
    assert row["lifetime_budget"] == 80.0
    assert row["settings"]["ad"]["headline"] == "{Купи|Закажи}"

    async with SessionLocal() as db:
        await db.execute(delete(MetaTemplate).where(MetaTemplate.id == uuid.UUID(row["id"])))
        await db.commit()


async def test_the_bundle_names_the_objects_and_holds_the_budget(launch_setup) -> None:
    """Названия в кабинете берутся из связки, бюджет — с выбранного уровня."""
    async with SessionLocal() as db:
        template = await db.get(MetaTemplate, launch_setup["template"])
        template.settings = {
            "campaign": {
                "goal": "leads",
                "campaign_name": "{{bundle.name}} | {{geo}}",
                "budget_level": "adset",
                "budget_kind": "daily",
            },
            "adset": {"adset_name": "adset #{{adset.number}}"},
            "ad": {"ad_name": "ad #{{ad.number}} {{creative.name}}"},
        }
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.activate_on_publish = True
        launch.pause_ads = True
        await db.commit()

    sent: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = dict(httpx.QueryParams(request.content.decode()))
            sent.append((request.url.path, body))
        return _write_handler(request)

    publisher = MetaLaunchPublisher(SessionLocal, _client_factory(handler))
    result = await publisher.publish(str(launch_setup["launch"]), str(launch_setup["admin"]))

    campaign = [body for path, body in sent if path.endswith("/campaigns")][0]
    adset = [body for path, body in sent if path.endswith("/adsets")][0]
    ad = [body for path, body in sent if path.endswith("/ads")][0]
    assert campaign["name"] == "DE broad | DE"
    assert adset["name"] == "adset #1"
    assert ad["name"] == "ad #1 banner-01"
    # Бюджет живёт там, где его поставила связка, и ровно в одном месте.
    assert "daily_budget" not in campaign
    assert adset["daily_budget"] == "5000"
    assert result["status"] == "active"

    async with SessionLocal() as db:
        kinds = list(
            (
                await db.execute(
                    select(MetaOperation.kind).where(
                        MetaOperation.launch_id == launch_setup["launch"]
                    )
                )
            ).scalars()
        )
    # Объявления оставили на паузе — значит, снимали только адсет и кампанию.
    assert "ad_activate" not in kinds
    assert kinds.count("adset_activate") == 1
    assert kinds.count("campaign_activate") == 1


async def test_a_scheduled_batch_waits_for_the_scheduler(launch_setup, monkeypatch) -> None:
    queued: list[str] = []
    monkeypatch.setattr(
        "app.api.routers.meta.publish_meta_launch",
        type("Task", (), {"delay": staticmethod(lambda *args: queued.append(args[0]))}),
    )
    moment = datetime.now(UTC) + timedelta(hours=3)
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/launches/batch",
            json={
                "name": "Ночной залив",
                "template_id": str(launch_setup["template"]),
                "account_ids": [str(launch_setup["account"])],
                "creatives_by_account": {
                    str(launch_setup["account"]): [str(launch_setup["creative"])]
                },
                "daily_budget": "50",
                "link_url": "https://track.example/click",
                "publish_at": moment.isoformat(),
                "publish": True,
            },
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scheduled"] == 1 and body["queued"] == 0
    # Задача не уходит в брокер: с eta на три часа она не пережила бы
    # перезапуск воркера, и залив просто не состоялся бы — молча.
    assert queued == []

    from app.workers.tasks import _publish_due_launches

    async with SessionLocal() as db:
        launch = await db.scalar(
            select(MetaLaunch).where(MetaLaunch.name == "Ночной залив")
        )
        assert launch.publish_at is not None
        assert launch.status == LaunchStatus.draft
        # Время пришло — планировщик обязан отдать залив в очередь.
        launch.publish_at = datetime.now(UTC) - timedelta(minutes=1)
        launch_id = launch.id
        await db.commit()

    published: list[tuple] = []
    monkeypatch.setattr(
        "app.workers.tasks.publish_meta_launch",
        type("Task", (), {"delay": staticmethod(lambda *args: published.append(args))}),
    )
    assert (await _publish_due_launches())["queued"] == 1
    assert published and published[0][0] == str(launch_id)

    async with SessionLocal() as db:
        stored = await db.get(MetaLaunch, launch_id)
        # Время снято, иначе следующий тик поставил бы залив второй раз.
        assert stored.publish_at is None
        await db.execute(delete(MetaLaunch).where(MetaLaunch.id == launch_id))
        await db.commit()


async def test_the_pause_between_accounts_spreads_the_batch(launch_setup, monkeypatch) -> None:
    """Первый кабинет уходит сразу, следующий — через заданную паузу."""
    queued: list[str] = []
    monkeypatch.setattr(
        "app.api.routers.meta.publish_meta_launch",
        type("Task", (), {"delay": staticmethod(lambda *args: queued.append(args[0]))}),
    )
    async with SessionLocal() as db:
        first = await db.get(MetaAdAccount, launch_setup["account"])
        second = MetaAdAccount(
            workspace_id=first.workspace_id,
            connection_id=first.connection_id,
            external_id="act_777000333",
            name="Launch account 2",
            currency="USD",
            owner_id=first.owner_id,
        )
        db.add(second)
        await db.flush()
        copy = MetaCreative(
            workspace_id=first.workspace_id,
            account_id=second.id,
            kind="image",
            name="banner-02",
            external_hash="abc123hash",
        )
        db.add(copy)
        await db.commit()
        second_id, copy_id = str(second.id), str(copy.id)

    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/launches/batch",
            json={
                "name": "Пачка с паузой",
                "template_id": str(launch_setup["template"]),
                "account_ids": [str(launch_setup["account"]), second_id],
                "creatives_by_account": {
                    str(launch_setup["account"]): [str(launch_setup["creative"])],
                    second_id: [copy_id],
                },
                "daily_budget": "50",
                "link_url": "https://track.example/click",
                "account_delay_seconds": 120,
                "publish": True,
            },
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] == 2
    # Первый уходит сразу, второй ждёт паузу: двадцать кабинетов, стартующих в
    # одну секунду, — это ровно тот след, из-за которого прилетает бан.
    assert body["queued"] == 1 and body["scheduled"] == 1
    assert len(queued) == 1

    async with SessionLocal() as db:
        await db.execute(delete(MetaLaunch).where(MetaLaunch.name == "Пачка с паузой"))
        await db.execute(delete(MetaCreative).where(MetaCreative.id == uuid.UUID(copy_id)))
        await db.execute(delete(MetaAdAccount).where(MetaAdAccount.id == uuid.UUID(second_id)))
        await db.commit()


async def test_the_targeting_lookup_goes_to_meta_and_needs_two_letters(
    launch_setup, monkeypatch
) -> None:
    """Интересы и языки приходят из Meta: своего справочника у них быть не может."""
    seen: list[tuple[str, str]] = []

    async def fake_search(self, kind: str, query: str, limit: int = 25) -> list[dict]:
        seen.append((kind, query))
        return [
            {"id": "6003107902433", "name": "Спорт", "path": ["Интересы", "Спорт"]},
        ]

    monkeypatch.setattr(
        "app.services.meta.MetaClient.targeting_search", fake_search, raising=True
    )
    with _admin_client() as client:
        short = client.get("/api/v1/meta/targeting", params={"kind": "interest", "q": "с"})
        found = client.get("/api/v1/meta/targeting", params={"kind": "interest", "q": "спорт"})

    # На один символ в Meta не ходим: она отдаёт случайную выборку, и показывать
    # её как подсказку — значит предлагать наугад.
    assert short.status_code == 200 and short.json()["items"] == []
    assert seen == [("interest", "спорт")]
    row = found.json()["items"][0]
    assert row["id"] == "6003107902433"
    assert row["path"] == "Интересы / Спорт"


def test_the_country_list_carries_russian_names() -> None:
    from app.services.geo import country_options

    rows = {row["code"]: row for row in country_options()}
    # Страну в форме ищут по-русски, а аббревиатуры не должны превращаться
    # в «Сша».
    assert rows["DE"]["ru"] == "Германия"
    assert rows["US"]["ru"] == "США"
    assert rows["DE"]["name"] == "Germany"


def test_macros_are_split_by_level_and_random_has_a_length() -> None:
    from app.services.meta_bundle import MACROS

    campaign = {macro["code"] for macro in MACROS["campaign"]}
    ad = {macro["code"] for macro in MACROS["ad"]}
    # ID кампании можно подставить в название объявления — кампания к тому
    # моменту создана; в название самой кампании нельзя, ID ещё нет.
    assert "{{campaign.id}}" not in campaign
    assert "{{campaign.id}}" in ad
    assert "{{ad.number}}" in ad and "{{ad.number}}" not in campaign

    value = render_macros("{{random.digits.4}}", {}, random.Random(1))
    assert len(value) == 4 and value.isdigit()
    assert len(render_macros("{{random.letters.en.3}}", {}, random.Random(1))) == 3


def test_targeting_reads_advantage_audience_over_manual_expansion() -> None:
    template = MetaTemplate(
        workspace_id=uuid.uuid4(),
        name="t",
        geo=["DE"],
        genders=[],
        languages=[],
        placements={},
        interests=[],
        settings={"adset": {"advantage_audience": True, "targeting_expansion": True}},
    )
    targeting = build_targeting(template)
    assert targeting["targeting_automation"] == {"advantage_audience": 1}
    # Meta принимает что-то одно: Advantage+ уже означает «ищи шире».
    assert "targeting_relaxation_types" not in targeting


def test_zero_decimal_currencies_do_not_get_multiplied() -> None:
    from app.services.meta import money_to_minor

    # У иены «центов» нет: умножение на сто дало бы кабинету стократный бюджет.
    assert money_to_minor(Decimal("50"), "USD") == "5000"
    assert money_to_minor(Decimal("5000"), "JPY") == "5000"
    assert money_to_minor(Decimal("50")) == "5000"


def test_the_promoted_object_follows_the_goal() -> None:
    from app.services.meta_launch import _promoted_object

    launch = MetaLaunch(
        workspace_id=uuid.uuid4(), account_id=uuid.uuid4(), name="l", page_id="900100"
    )
    template = MetaTemplate(
        workspace_id=launch.workspace_id,
        name="t",
        optimization_goal="PAGE_LIKES",
        custom_event_type="LEAD",
    )
    # Лайкам страницы нужна страница, конверсиям — пиксель с событием.
    assert _promoted_object(launch, template) == {"page_id": "900100"}
    template.optimization_goal = "OFFSITE_CONVERSIONS"
    template.pixel_id = "998877"
    assert _promoted_object(launch, template) == {
        "pixel_id": "998877",
        "custom_event_type": "LEAD",
    }
    template.optimization_goal = "LINK_CLICKS"
    assert _promoted_object(launch, template) is None


async def test_validation_catches_what_meta_would_reject(launch_setup) -> None:
    """Ошибки, которые Meta вернула бы кодом 100, ловим до обращения к ней."""
    async with SessionLocal() as db:
        template = await db.get(MetaTemplate, launch_setup["template"])
        template.optimization_goal = "APP_INSTALLS"
        template.genders = [1]
        template.settings = {
            "campaign": {"budget_kind": "lifetime", "special_ad_categories": ["CREDIT"]},
        }
        await db.commit()
        context = await load_launch_context(db, launch_setup["launch"])

    with pytest.raises(LaunchValidationError) as failure:
        validate_launch(context)
    message = str(failure.value)
    assert "Установки приложения" in message
    assert "даты окончания" in message
    assert "по полу" in message

    async with SessionLocal() as db:
        template = await db.get(MetaTemplate, launch_setup["template"])
        template.optimization_goal = "LINK_CLICKS"
        template.genders = []
        template.settings = {}
        await db.commit()
        context = await load_launch_context(db, launch_setup["launch"])
    # Всё исправили — залив проходит.
    validate_launch(context)


async def test_randomization_is_stable_for_the_same_launch(launch_setup) -> None:
    """Разброс бюджета и возраста повторяем: иначе вторая попытка после сбоя
    ушла бы в кабинет с другими числами."""
    async with SessionLocal() as db:
        template = await db.get(MetaTemplate, launch_setup["template"])
        template.settings = {
            "campaign": {"budget_level": "adset", "budget_randomize": True},
            "adset": {"age_randomize": True},
        }
        await db.commit()

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/adsets"):
            sent.append(dict(httpx.QueryParams(request.content.decode())))
        return _write_handler(request)

    publisher = MetaLaunchPublisher(SessionLocal, _client_factory(handler))
    await publisher.publish(str(launch_setup["launch"]), str(launch_setup["admin"]))

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.adset_external_id = None
        launch.campaign_external_id = None
        # Публикация идемпотентна по записанным ID: чтобы повторить создание,
        # их нужно снять целиком.
        launch.external_payload = {}
        launch.status = LaunchStatus.draft
        await db.commit()
    await publisher.publish(str(launch_setup["launch"]), str(launch_setup["admin"]))

    assert len(sent) == 2
    assert sent[0]["daily_budget"] == sent[1]["daily_budget"]
    # ±10 % от 50 долларов, в центах.
    assert 4500 <= int(sent[0]["daily_budget"]) <= 5500
    targeting = json.loads(sent[0]["targeting"])
    assert 13 <= targeting["age_min"] <= targeting["age_max"] <= 65
    assert json.loads(sent[1]["targeting"])["age_min"] == targeting["age_min"]


async def test_languages_make_one_ad_and_adsets_are_copied(launch_setup) -> None:
    """Языки — один креатив с набором текстов, адсеты — копии одного таргета."""
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.adset_count = 2
        launch.ads = [
            {
                "creative_ids": [str(launch_setup["creative"])],
                "texts": [
                    {
                        "language": "en_US",
                        "language_name": "English (US)",
                        "headline": "Buy",
                        "primary_text": "Text EN",
                        "link_url": "https://track.example/en",
                    },
                    {
                        "language": "de_DE",
                        "language_name": "Deutsch",
                        "headline": "Kaufen",
                        "primary_text": "Text DE",
                        "link_url": "https://track.example/de",
                    },
                ],
            }
        ]
        await db.commit()

    sent: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            sent.append((request.url.path, dict(httpx.QueryParams(request.content.decode()))))
        return _write_handler(request)

    publisher = MetaLaunchPublisher(SessionLocal, _client_factory(handler))
    result = await publisher.publish(str(launch_setup["launch"]), str(launch_setup["admin"]))

    adsets = [body for path, body in sent if path.endswith("/adsets")]
    creatives = [body for path, body in sent if path.endswith("/adcreatives")]
    ads = [body for path, body in sent if path.endswith("/ads")]
    # Две копии адсета, по объявлению в каждой, а креатив — один на обе.
    assert len(adsets) == 2 and len(ads) == 2 and len(creatives) == 1
    assert len(result["adset_ids"]) == 2

    feed = json.loads(creatives[0]["asset_feed_spec"])
    assert [title["text"] for title in feed["titles"]] == ["Buy", "Kaufen"]
    assert [link["website_url"] for link in feed["link_urls"]] == [
        "https://track.example/en?sub_id_5={{campaign.id}}",
        "https://track.example/de?sub_id_5={{campaign.id}}",
    ]
    # Правило соответствия — то, ради чего всё затевалось: текст выбирается
    # по языку зрителя, а не крутится вперемешку. Meta принимает только
    # числовые ID локалей (adlocale) + одно правило должно быть по умолчанию.
    rules = feed["asset_customization_rules"]
    assert [rule["customization_spec"]["locales"] for rule in rules] == [[6], [3]]
    assert [rule["is_default"] for rule in rules] == [True, False]
    # Метки правил — коды языков, Meta числовые строки не принимает.
    assert [rule["title_label"]["name"] for rule in rules] == ["en_US", "de_DE"]
    # Общий креатив: ни одного image_label (картинка без метки на всех).
    assert all("image_label" not in rule for rule in rules)

    # Повтор ничего не задваивает: ID уже записаны.
    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.status = LaunchStatus.draft
        await db.commit()
    sent.clear()
    await publisher.publish(str(launch_setup["launch"]), str(launch_setup["admin"]))
    assert [path for path, _ in sent if not path.endswith("/insights")] == []


async def test_own_creative_per_language_goes_to_the_rules(launch_setup) -> None:
    """«На каждый язык свой креатив»: у ассетов adlabels, в правилах —
    image_label, каждый язык ссылается на свою картинку."""
    from sqlalchemy import select as _select

    from app.models import MetaCreative, MetaLaunchCreative

    async with SessionLocal() as db:
        db.add(
            MetaCreative(
                workspace_id=launch_setup["workspace"],
                account_id=launch_setup["account"],
                kind="image",
                name="banner-de",
                external_hash="def456hash",
            )
        )
        await db.flush()
        second = await db.scalar(
            _select(MetaCreative).where(MetaCreative.external_hash == "def456hash")
        )
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.ads = [
            {
                "creative_ids": [],
                "texts": [
                    {
                        "language": "en_US",
                        "language_name": "English (US)",
                        "headline": "Buy",
                        "primary_text": "Text EN",
                        "link_url": "https://track.example/en",
                        "creative_ids": [str(launch_setup["creative"])],
                    },
                    {
                        "language": "de_DE",
                        "language_name": "Deutsch",
                        "headline": "Kaufen",
                        "primary_text": "Text DE",
                        "link_url": "https://track.example/de",
                        "creative_ids": [str(second.id)],
                    },
                ],
            }
        ]
        db.add(MetaLaunchCreative(launch_id=launch.id, creative_id=second.id, position=1))
        await db.commit()

    sent: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            sent.append((request.url.path, dict(httpx.QueryParams(request.content.decode()))))
        return _write_handler(request)

    await MetaLaunchPublisher(
        SessionLocal, _client_factory(handler)
    ).publish(str(launch_setup["launch"]), str(launch_setup["admin"]))

    creatives = [body for path, body in sent if path.endswith("/adcreatives")]
    assert len(creatives) == 1
    feed = json.loads(creatives[0]["asset_feed_spec"])
    images = feed["images"]
    assert all("adlabels" in image for image in images)
    # Метки языков — коды (не числовые строки: их Meta читает как id).
    labels = {image["adlabels"][0]["name"] for image in images}
    assert labels == {"en_US", "de_DE"}
    rules = {rule["image_label"]["name"] for rule in feed["asset_customization_rules"]}
    assert rules == {"en_US", "de_DE"}
    assert [rule["customization_spec"]["locales"] for rule in feed["asset_customization_rules"]] == [
        [6],
        [3],
    ]


async def test_advanced_mode_creates_several_campaigns_with_overrides(
    launch_setup,
) -> None:
    """Расширенный режим: 2 кампании, своя цель, бюджет на адсетах на весь
    срок, лимит адсета, стратегия ставок — всё уходит в Meta."""
    from sqlalchemy import delete as _delete

    from app.models import MetaRule, User

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        rule = MetaRule(
            workspace_id=launch_setup["workspace"],
            account_id=launch_setup["account"],
            name="Advanced rule",
            level="campaign",
            conditions=[{"metric": "roi", "operator": "lt", "value": "0"}],
            min_spend=Decimal("10"),
            created_by_id=admin.id,
        )
        db.add(rule)
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        launch.campaign_count = 2
        launch.objective = "OUTCOME_TRAFFIC"
        launch.custom_event_type = "LEAD"
        launch.attribution = "1d_click"
        launch.engaged_view = "7d"
        launch.budget_level = "adset"
        launch.budget_kind = "lifetime"
        launch.budget_randomize = False
        launch.budget_limit_min = Decimal("1")
        launch.budget_limit_max = Decimal("15")
        launch.bid_strategy = "LOWEST_COST_WITH_BID_CAP"
        launch.daily_budget = Decimal("30")
        launch.rule_ids = [str(rule.id)]
        launch.rule_group = "Группа №1"
        launch.tags = {"level": "campaign", "names": ["Новая волна"], "mode": "add"}
        await db.commit()
        rule_id = rule.id

    sent: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            sent.append((request.url.path, dict(httpx.QueryParams(request.content.decode()))))
            if request.url.path.endswith("/adlabels"):
                return httpx.Response(200, json={"id": "label-42"})
        return _write_handler(request)

    from app.services.meta_launch import attach_rules_to_launch

    async with SessionLocal() as db:
        launch = await db.get(MetaLaunch, launch_setup["launch"])
        await attach_rules_to_launch(db, launch, launch.rule_ids)
        await db.commit()

    await MetaLaunchPublisher(
        SessionLocal, _client_factory(handler)
    ).publish(str(launch_setup["launch"]), str(launch_setup["admin"]))

    campaigns = [body for path, body in sent if path.endswith("/campaigns")]
    adsets = [body for path, body in sent if path.endswith("/adsets")]
    labels = [
        body for path, body in sent
        if path.endswith("/adlabels") and "name" in body
    ]
    attaches = [
        body for path, body in sent
        if path.endswith("/adlabels") and "adlabel_id" in body
    ]
    assert len(campaigns) == 2
    assert all(campaigns[0]["objective"] == "OUTCOME_TRAFFIC" for campaign in campaigns)
    assert all("daily_budget" not in campaign for campaign in campaigns)
    assert len(adsets) == 2
    assert all("lifetime_budget" in adset for adset in adsets)
    assert all(adset["daily_budget_min"] == "100" for adset in adsets)  # 1 USD
    assert all(adset["daily_budget_max"] == "1500" for adset in adsets)  # 15 USD
    assert all(adset["bid_strategy"] == "LOWEST_COST_WITH_BID_CAP" for adset in adsets)
    assert len(labels) == 1 and labels[0]["name"] == "Новая волна"
    assert len(attaches) == 2

    async with SessionLocal() as db:
        bound = await db.scalar(
            select(MetaRule).where(MetaRule.id == rule_id)
        )
        assert bound.launch_id == launch_setup["launch"]
        stored = await db.get(MetaLaunch, launch_setup["launch"])
        payload = stored.external_payload or {}
        assert len(payload.get("campaign_ids") or []) == 2
        await db.execute(_delete(MetaRule).where(MetaRule.id == rule_id))
        await db.commit()


def test_uniquify_changes_the_hash_without_breaking_the_file() -> None:
    from app.services.meta import uniquify

    jpeg = b"\xff\xd8" + b"\x00" * 32 + b"\xff\xd9"
    first = uniquify(jpeg, "image/jpeg")
    second = uniquify(jpeg, "image/jpeg")
    # Метка кладётся в служебный сегмент: файл остаётся JPEG, а хэш меняется.
    assert first[:2] == b"\xff\xd8" and first.endswith(b"\xff\xd9")
    assert first != jpeg and first != second
    # Формат, который так не умеет, возвращается как есть: портить файл ради
    # уникальности хуже, чем оставить его прежним.
    assert uniquify(b"GIF89a...", "image/gif") == b"GIF89a..."


def test_age_randomization_respects_the_given_years_and_meta_limits() -> None:
    from app.services.meta_launch import _randomized_age

    template = MetaTemplate(workspace_id=uuid.uuid4(), name="t", age_min=25, age_max=45)
    spread = _randomized_age(template, "seed", years=3)
    assert 22 <= spread["age_min"] <= 28
    assert 42 <= spread["age_max"] <= 48

    # За 18 и 65 разброс не уходит: ниже Meta таргет не пускает, выше у неё
    # просто нет — там «65 и старше».
    edges = MetaTemplate(workspace_id=uuid.uuid4(), name="t", age_min=18, age_max=65)
    for seed in ("a", "b", "c", "d", "e"):
        result = _randomized_age(edges, seed, years=3)
        assert 18 <= result["age_min"] <= 21
        assert 62 <= result["age_max"] <= 65


def test_the_client_routes_through_the_connection_proxy() -> None:
    from app.services.meta import MetaClient

    client = MetaClient("token", proxy="socks5://user:pass@1.2.3.4:1080", user_agent="UA/1.0")
    assert client.proxy == "socks5://user:pass@1.2.3.4:1080"
    # User-Agent уходит заголовком рядом с токеном, а не подменяет его.
    assert client.headers["User-Agent"] == "UA/1.0"
    assert client.headers["Authorization"] == "Bearer token"

    bare = MetaClient("token")
    assert bare.proxy is None and "User-Agent" not in bare.headers


def test_proxy_without_scheme_is_rejected_at_the_form() -> None:
    from app.schemas import validate_proxy_url

    assert validate_proxy_url("http://user:pass@1.2.3.4:8080") == "http://user:pass@1.2.3.4:8080"
    assert validate_proxy_url("  ") is None
    # «1.2.3.4:8080» httpx не примет, и падало бы это в синхронизации — далеко
    # от места, где адрес вводили.
    with pytest.raises(ValueError):
        validate_proxy_url("1.2.3.4:8080")


def test_gentle_window_for_non_system_tokens() -> None:
    from app.services.meta_sync import BACKFILL_DAYS, GENTLE_DAYS, MetaSyncEngine

    system = MetaSyncEngine._date_window("backfill", {"auth_method": "system_user"})
    session = MetaSyncEngine._date_window("backfill", {"auth_method": "session"})
    # Токену системного пользователя полная выкачка нормальна, токену из сессии
    # такой всплеск сразу после выпуска Meta засчитывает за угон.
    assert (system[1] - system[0]).days == BACKFILL_DAYS
    assert (session[1] - session[0]).days == GENTLE_DAYS
