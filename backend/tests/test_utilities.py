"""Утилиты: Alert и CAP — ТЗ 9."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret, hash_password
from app.models import (
    AlertChannel,
    AlertEvent,
    AlertRule,
    CapRule,
    IntegrationConnection,
    MediaRecord,
    Offer,
    Role,
    Session,
    Status,
    TelegramBot,
    User,
    UserParent,
)
from app.services import telegram
from app.services.alert_fields import window_range
from app.services.alerts import (
    AlertEngine,
    cap_period_range,
    deposit_matches,
    metrics_for,
    normalize_conditions,
    normalize_thresholds,
    reached_threshold,
    render_deposit,
    scheduled_now,
)
from tests.test_media_finance import _admin_client


def _team_today() -> date:
    """День команды, чтобы тест не расходился с отчётом около полуночи UTC."""
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


@pytest.fixture
async def utilities(database):
    """Бот, канал и один баер с цифрами Медиаборда за сегодня."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        buyer = User(
            workspace_id=admin.workspace_id,
            role_id=buyer_role.id,
            name="Баер Алекс",
            login="alertbuyer",
            password_hash=hash_password("alert-password"),
        )
        db.add(buyer)
        await db.flush()

        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == admin.workspace_id
            )
        )
        if connection is None:
            connection = IntegrationConnection(
                workspace_id=admin.workspace_id,
                name="Alerts fixture",
                base_url="https://tracker.example",
                api_key_encrypted=encrypt_secret("test-key"),
            )
            db.add(connection)
            await db.flush()
        # Свой оффер и свой баер: показатели считаются по всему воркспейсу, и
        # строки Медиаборда из соседних тестов иначе попадали бы в агрегат.
        offer = Offer(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id=f"alert-offer-{uuid.uuid4()}",
            name="Оффер для алертов",
        )
        db.add(offer)
        await db.flush()

        db.add(
            MediaRecord(
                workspace_id=admin.workspace_id,
                record_date=_team_today(),
                buyer_id=buyer.id,
                offer_id=offer.id,
                revenue=Decimal("900"),
                spend_calculated=Decimal("600"),
                installs=100,
                registrations=40,
                ftd=8,
            )
        )
        channel = AlertChannel(
            workspace_id=admin.workspace_id,
            name="Команда",
            chat_id="-1001234567890",
        )
        db.add(channel)
        db.add(
            TelegramBot(
                workspace_id=admin.workspace_id,
                username="celestial_bot",
                token_encrypted=encrypt_secret("123456:TESTTOKENTESTTOKEN"),
            )
        )
        await db.commit()
        ids = {
            "workspace": admin.workspace_id,
            "buyer": buyer.id,
            "offer": offer.id,
            "channel": channel.id,
        }

    yield ids

    async with SessionLocal() as db:
        await db.execute(
            delete(AlertEvent).where(AlertEvent.workspace_id == ids["workspace"])
        )
        await db.execute(
            delete(AlertRule).where(AlertRule.workspace_id == ids["workspace"])
        )
        await db.execute(delete(CapRule).where(CapRule.workspace_id == ids["workspace"]))
        await db.execute(
            delete(AlertChannel).where(AlertChannel.workspace_id == ids["workspace"])
        )
        await db.execute(
            delete(TelegramBot).where(TelegramBot.workspace_id == ids["workspace"])
        )
        await db.execute(delete(MediaRecord).where(MediaRecord.buyer_id == ids["buyer"]))
        await db.execute(delete(Offer).where(Offer.id == ids["offer"]))
        await db.execute(delete(Session).where(Session.user_id == ids["buyer"]))
        await db.execute(delete(UserParent).where(UserParent.user_id == ids["buyer"]))
        await db.execute(delete(User).where(User.id == ids["buyer"]))
        await db.commit()


def _alert(utilities, **overrides) -> dict:
    payload = {
        "name": "Депозиты команды",
        "kind": "deposit",
        "channel_id": str(utilities["channel"]),
        "message_template": "Депозит {offer} · {campaign} · {revenue} · {click_at}",
    }
    payload.update(overrides)
    return payload


def _report(utilities, **overrides) -> dict:
    payload = {
        "name": "Утренний отчёт",
        "kind": "report",
        "channel_id": str(utilities["channel"]),
        "window": "today",
        "schedule": "daily_09",
        "timezone": "Europe/Moscow",
    }
    payload.update(overrides)
    return payload

def _cap(utilities, **overrides) -> dict:
    payload = {
        "name": "CAP на оффер",
        "channel_id": str(utilities["channel"]),
        "offer_ids": [str(utilities["offer"])],
        "metric": "sales",
        "limit_value": 10,
        "period": "day",
        "timezone": "Europe/Moscow",
        "notify_at": [80, 100],
    }
    payload.update(overrides)
    return payload


# --- справочник и правила ----------------------------------------------------


async def test_report_can_use_a_custom_daily_time(utilities) -> None:
    with _admin_client() as client:
        reference = client.get("/api/v1/utilities/reference").json()
        created = client.post(
            "/api/v1/utilities/alerts",
            json=_report(utilities, schedule="daily_1435"),
        )
        invalid = client.post(
            "/api/v1/utilities/alerts",
            json=_report(utilities, schedule="daily_2460"),
        )

    schedule_codes = {item["code"] for item in reference["schedules"]}
    assert "twice" not in schedule_codes
    assert "workdays_10" not in schedule_codes
    assert created.status_code == 201
    assert created.json()["schedule_label"] == "Ежедневно в 14:35"
    assert invalid.status_code == 422


async def test_a_cap_needs_an_offer_or_a_person(utilities) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/utilities/caps", json=_cap(utilities, offer_ids=[], user_id=None)
        )
    assert response.status_code == 422


async def test_a_cap_reports_its_progress(utilities) -> None:
    """8 депозитов из лимита 10 — это 80 %."""
    with _admin_client() as client:
        rule = client.post("/api/v1/utilities/caps", json=_cap(utilities)).json()
        progress = client.get(f"/api/v1/utilities/caps/{rule['id']}/progress").json()
    assert Decimal(str(progress["value"])) == Decimal("8")
    assert progress["percent"] == 80


async def test_editing_a_cap_resets_the_reached_threshold(utilities) -> None:
    """Новый порог ниже уже пройденного иначе никогда бы не сработал."""
    with _admin_client() as client:
        rule = client.post("/api/v1/utilities/caps", json=_cap(utilities)).json()
        async with SessionLocal() as db:
            stored = await db.get(CapRule, uuid.UUID(rule["id"]))
            stored.notified_percent = 100
            await db.commit()
        client.patch(
            f"/api/v1/utilities/caps/{rule['id']}", json=_cap(utilities, notify_at=[50])
        )

    async with SessionLocal() as db:
        stored = await db.get(CapRule, uuid.UUID(rule["id"]))
        assert stored.notified_percent == 0


# --- движок ------------------------------------------------------------------


async def test_metrics_come_from_the_mediaboard(utilities) -> None:
    today = _team_today()
    async with SessionLocal() as db:
        values = await metrics_for(
            db, utilities["workspace"], today, today, user_id=utilities["buyer"]
        )
    assert values["spend"] == Decimal("600.0000")
    assert values["profit"] == Decimal("300.0000")
    assert values["roi"] == Decimal("50.0000")
    assert values["sales"] == Decimal("8")
    assert values["cpl"] == Decimal("15.0000")


async def test_a_trigger_sends_a_message_and_logs_the_event(utilities, monkeypatch) -> None:
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append({"chat_id": chat_id, "text": text})

    monkeypatch.setattr(telegram, "send_message", fake_send)

    with _admin_client() as client:
        client.post(
            "/api/v1/utilities/alerts",
            json=_report(utilities, schedule="every_15"),
        )

    result = await AlertEngine(SessionLocal).run()
    assert result["alerts"] == 1
    assert sent and sent[0]["chat_id"] == "-1001234567890"
    assert "Отчёт за" in sent[0]["text"]

    with _admin_client() as client:
        events = client.get("/api/v1/utilities/events").json()["items"]
    assert events[0]["delivered"] is True


async def test_a_failed_delivery_is_written_down_with_its_reason(
    utilities, monkeypatch
) -> None:
    """Иначе на вопрос «почему не пришло» нечем ответить — в чате-то пусто."""

    async def fake_send(token, chat_id, text, thread_id=None):
        raise telegram.TelegramError("Бот исключён из чата")

    monkeypatch.setattr(telegram, "send_message", fake_send)

    with _admin_client() as client:
        client.post(
            "/api/v1/utilities/alerts",
            json=_report(utilities, schedule="every_15"),
        )
    await AlertEngine(SessionLocal).run()

    with _admin_client() as client:
        events = client.get("/api/v1/utilities/events").json()["items"]
    assert events[0]["delivered"] is False
    assert "исключён" in events[0]["error"]


async def test_a_switched_off_rule_is_skipped(utilities, monkeypatch) -> None:
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)

    with _admin_client() as client:
        client.post("/api/v1/utilities/alerts", json=_alert(utilities, status="inactive"))
    result = await AlertEngine(SessionLocal).run()

    assert result["alerts"] == 0
    assert sent == []


async def test_a_cap_warns_once_per_threshold(utilities, monkeypatch) -> None:
    """8 из 10 — это 80 %: приходит порог 80 и не повторяется."""
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)

    with _admin_client() as client:
        client.post("/api/v1/utilities/caps", json=_cap(utilities))
    first = await AlertEngine(SessionLocal).run()
    second = await AlertEngine(SessionLocal).run()

    assert first["caps"] == 1
    assert second["caps"] == 0
    assert len(sent) == 1
    assert "80 %" in sent[0]


async def test_a_new_period_lets_the_cap_warn_again(utilities, monkeypatch) -> None:
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)

    with _admin_client() as client:
        rule = client.post("/api/v1/utilities/caps", json=_cap(utilities)).json()
    await AlertEngine(SessionLocal).run()

    async with SessionLocal() as db:
        stored = await db.get(CapRule, uuid.UUID(rule["id"]))
        # Как будто предупреждение ушло вчера: счётчик порогов относится к
        # прошлому периоду и должен обнулиться.
        stored.notified_period = "2000-01-01/d"
        await db.commit()

    await AlertEngine(SessionLocal).run()
    assert len(sent) == 2


async def test_without_a_bot_nothing_is_sent_but_the_reason_is_recorded(
    utilities,
) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(TelegramBot).where(TelegramBot.workspace_id == utilities["workspace"])
        )
        await db.commit()

    with _admin_client() as client:
        client.post(
            "/api/v1/utilities/alerts",
            json=_report(utilities, schedule="every_15"),
        )
    await AlertEngine(SessionLocal).run()

    with _admin_client() as client:
        events = client.get("/api/v1/utilities/events").json()["items"]
    assert events[0]["delivered"] is False
    assert "не подключён" in events[0]["error"]


async def test_the_bot_token_is_never_returned(utilities) -> None:
    with _admin_client() as client:
        payload = client.get("/api/v1/utilities/bot").json()
    assert payload["connected"] is True
    assert payload["username"] == "celestial_bot"
    assert "token" not in str(payload).lower() or "TESTTOKEN" not in str(payload)


async def test_a_bad_token_is_not_saved(utilities, monkeypatch) -> None:
    """Токен с опечаткой сохранился бы молча и подвёл в самый нужный момент."""

    async def fake_check(token):
        raise telegram.TelegramError("Telegram не принял токен бота")

    monkeypatch.setattr(telegram, "check_token", fake_check)

    with _admin_client() as client:
        response = client.put("/api/v1/utilities/bot", json={"token": "0" * 30})
    assert response.status_code == 422

    async with SessionLocal() as db:
        bot = await db.scalar(
            select(TelegramBot).where(
                TelegramBot.workspace_id == utilities["workspace"]
            )
        )
        # Прежний токен остался нетронутым.
        assert bot.username == "celestial_bot"


# --- вспомогательные функции --------------------------------------------------


def test_windows_cover_the_expected_days() -> None:
    today = date(2026, 8, 20)  # четверг
    assert window_range("today", today) == (today, today)
    assert window_range("yesterday", today) == (date(2026, 8, 19), date(2026, 8, 19))
    assert window_range("last_7d", today) == (date(2026, 8, 14), today)
    assert window_range("last_30d", today) == (date(2026, 7, 22), today)
    assert window_range("this_week", today) == (date(2026, 8, 17), today)
    assert window_range("last_week", today) == (date(2026, 8, 10), date(2026, 8, 16))
    assert window_range("month", today) == (date(2026, 8, 1), today)
    assert window_range("last_month", today) == (date(2026, 7, 1), date(2026, 7, 31))


def test_cap_periods_change_their_key_between_periods() -> None:
    monday = date(2026, 8, 17)
    sunday = date(2026, 8, 23)
    assert cap_period_range("week", monday)[2] == cap_period_range("week", sunday)[2]
    assert cap_period_range("day", monday)[2] != cap_period_range("day", sunday)[2]


def test_thresholds_are_sorted_and_deduplicated() -> None:
    assert normalize_thresholds([100, 50, 50, "80", None]) == [50, 80, 100]
    assert normalize_thresholds("мусор") == [100]


def test_only_the_highest_passed_threshold_counts() -> None:
    assert reached_threshold([50, 80, 100], 85) == 80
    assert reached_threshold([50, 80, 100], 30) == 0
    assert reached_threshold([50, 80, 100], 120) == 100


# --- права --------------------------------------------------------------------


async def test_a_buyer_can_read_but_not_change(utilities) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"login": "alertbuyer", "password": "alert-password"},
        )
        read = client.get("/api/v1/utilities/alerts")
        write = client.post(
            "/api/v1/utilities/alerts",
            json=_report(utilities, schedule="every_15"),
        )
    assert read.status_code == 200
    assert write.status_code == 403


async def test_an_inactive_channel_stops_delivery(utilities, monkeypatch) -> None:
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)

    with _admin_client() as client:
        client.post(
            "/api/v1/utilities/alerts",
            json=_report(utilities, schedule="every_15"),
        )
    async with SessionLocal() as db:
        channel = await db.get(AlertChannel, utilities["channel"])
        channel.status = Status.inactive
        await db.commit()

    await AlertEngine(SessionLocal).run()
    assert sent == []

    with _admin_client() as client:
        events = client.get("/api/v1/utilities/events").json()["items"]
    assert events[0]["delivered"] is False
    assert "выключен" in events[0]["error"]


async def test_the_bot_chats_endpoint_lists_where_the_bot_was_added(
    utilities, monkeypatch
) -> None:
    """Иначе chat_id приходится искать вручную — и человек застревает."""

    async def fake_list(token):
        return [
            {"chat_id": "-1001234567890", "title": "Команда", "type": "supergroup"},
            {"chat_id": "-1005555555555", "title": "Новая группа", "type": "group"},
        ]

    monkeypatch.setattr(telegram, "list_chats", fake_list)

    with _admin_client() as client:
        result = client.get("/api/v1/utilities/bot/chats").json()

    chats = {chat["chat_id"]: chat for chat in result["chats"]}
    # Уже заведённый канал помечен, чтобы его не добавили второй раз.
    assert chats["-1001234567890"]["known"] is True
    assert chats["-1005555555555"]["known"] is False


async def test_bot_chats_without_a_bot_explains_what_to_do(utilities) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(TelegramBot).where(TelegramBot.workspace_id == utilities["workspace"])
        )
        await db.commit()

    with _admin_client() as client:
        response = client.get("/api/v1/utilities/bot/chats")
    assert response.status_code == 422
    assert "подключите бота" in response.json()["error"]["message"]


async def test_several_offers_add_up_into_one_cap(utilities) -> None:
    """Партнёрка даёт общий лимит на связку — показатели офферов складываются."""
    async with SessionLocal() as db:
        second = Offer(
            workspace_id=utilities["workspace"],
            name="Второй оффер связки",
        )
        db.add(second)
        await db.flush()
        db.add(
            MediaRecord(
                workspace_id=utilities["workspace"],
                record_date=_team_today(),
                buyer_id=utilities["buyer"],
                offer_id=second.id,
                ftd=5,
            )
        )
        await db.commit()
        second_id = second.id

    try:
        with _admin_client() as client:
            single = client.post(
                "/api/v1/utilities/caps", json=_cap(utilities, name="CAP один")
            ).json()
            both = client.post(
                "/api/v1/utilities/caps",
                json=_cap(
                    utilities,
                    name="CAP связка",
                    offer_ids=[str(utilities["offer"]), str(second_id)],
                ),
            ).json()
            listing = client.get("/api/v1/utilities/caps").json()

        rows = {row["name"]: row for row in listing["items"]}
        # 8 депозитов у первого оффера, 5 у второго.
        assert Decimal(str(rows["CAP один"]["progress"]["value"])) == Decimal("8")
        assert Decimal(str(rows["CAP связка"]["progress"]["value"])) == Decimal("13")
        assert rows["CAP связка"]["progress"]["percent"] == 130
        assert rows["CAP связка"]["offer_ids"] == [
            str(utilities["offer"]), str(second_id)
        ]
        assert len(rows["CAP связка"]["offer_names"]) == 2
        # Прогресс приезжает сразу со списком, без запроса на каждую строку.
        assert single["id"] and both["id"]
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(MediaRecord).where(MediaRecord.offer_id == second_id))
            await db.execute(delete(CapRule).where(CapRule.name.in_(["CAP один", "CAP связка"])))
            await db.execute(delete(Offer).where(Offer.id == second_id))
            await db.commit()


async def test_the_cap_list_counts_total_active_and_reached(utilities) -> None:
    with _admin_client() as client:
        client.post("/api/v1/utilities/caps", json=_cap(utilities, name="CAP выбран"))
        client.post(
            "/api/v1/utilities/caps",
            json=_cap(utilities, name="CAP далеко", limit_value=1000, status="inactive"),
        )
        listing = client.get("/api/v1/utilities/caps").json()
        only_active = client.get("/api/v1/utilities/caps?status=active").json()
        by_metric = client.get("/api/v1/utilities/caps?metric=spend").json()

    summary = listing["summary"]
    assert summary["total"] == 2
    assert summary["active"] == 1
    # «Достигнуто» — про лимит, а не про статус: 8 из 10 это ещё не 100 %.
    assert summary["reached"] == 0
    assert {row["name"] for row in only_active["items"]} == {"CAP выбран"}
    assert by_metric["items"] == []


async def test_the_cap_counter_resets_by_its_own_timezone() -> None:
    """Полночь сервера и полночь команды — разные моменты."""
    from app.services.alerts import cap_today

    assert cap_today("UTC") is not None
    # Неизвестная зона не роняет расчёт, а откатывается к UTC.
    assert cap_today("Nowhere/Nothing") == cap_today("UTC")


async def test_a_cap_names_its_offers_in_the_message(utilities) -> None:
    from app.services.alerts import AlertEngine

    async with SessionLocal() as db:
        rule = CapRule(
            workspace_id=utilities["workspace"],
            name="CAP подпись",
            channel_id=utilities["channel"],
            offer_ids=[str(utilities["offer"])],
            metric="sales",
            limit_value=Decimal("10"),
        )
        db.add(rule)
        await db.commit()
        subject = await AlertEngine(SessionLocal)._cap_subject(db, rule)
        await db.execute(delete(CapRule).where(CapRule.id == rule.id))
        await db.commit()

    assert subject and "Оффер для алертов" in subject


async def test_a_total_cap_never_resets(utilities, monkeypatch) -> None:
    """«Общий лимит» — это лимит на всё время, а не на день с другим названием."""
    from app.services.alerts import CAP_EPOCH, cap_period_range

    first, last, key = cap_period_range("total", date(2026, 8, 20))
    assert first == CAP_EPOCH
    assert last == date(2026, 8, 20)
    # Метка периода постоянная — счётчик пройденных порогов не обнуляется.
    assert key == cap_period_range("total", date(2027, 1, 1))[2]

    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)

    with _admin_client() as client:
        rule = client.post(
            "/api/v1/utilities/caps", json=_cap(utilities, period="total")
        )
        assert rule.status_code == 201
        assert rule.json()["period"] == "total"
        progress = client.get(
            f"/api/v1/utilities/caps/{rule.json()['id']}/progress"
        ).json()
    # Восемь депозитов из фикстуры попадают в общий период целиком.
    assert Decimal(str(progress["value"])) == Decimal("8")
    assert progress["period_from"] == CAP_EPOCH.isoformat()


# --- Alert: депозиты и отчёты (ТЗ 9.1) ---------------------------------------


@pytest.fixture
async def conversion(utilities):
    """Одна продажа в журнале конверсий — то, из чего собирается сообщение."""
    from app.models import KeitaroConversion

    async with SessionLocal() as db:
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == utilities["workspace"]
            )
        )
        row = KeitaroConversion(
            workspace_id=utilities["workspace"],
            connection_id=connection.id,
            external_id=f"conv-{uuid.uuid4()}",
            status="sale",
            conversion_at=datetime(2026, 8, 8, 15, 2, tzinfo=UTC),
            click_at=datetime(2026, 8, 8, 11, 23, tzinfo=UTC),
            campaign_external_id="77",
            campaign_name="FB | RU | Vulkan",
            campaign_group_id="5",
            offer_external_id="120",
            offer_name="Vulkan DE 250$",
            country_code="DE",
            revenue=Decimal("250"),
            sub_values={f"sub_id_{index}": f"s{index}" for index in range(1, 11)},
        )
        db.add(row)
        await db.commit()
        ids = {"id": row.id, "seen_at": row.seen_at}

    yield ids

    async with SessionLocal() as db:
        await db.execute(delete(KeitaroConversion).where(KeitaroConversion.id == ids["id"]))
        await db.commit()


async def _deposit_rule(utilities, **overrides) -> str:
    with _admin_client() as client:
        return client.post(
            "/api/v1/utilities/alerts", json=_alert(utilities, **overrides)
        ).json()["id"]


async def _open_cursor(rule_id: str) -> None:
    """Сдвинуть курсор в прошлое — как будто правило уже работало вчера."""
    async with SessionLocal() as db:
        rule = await db.get(AlertRule, uuid.UUID(rule_id))
        rule.cursor_at = datetime(2020, 1, 1, tzinfo=UTC)
        await db.commit()


async def test_a_new_deposit_rule_does_not_replay_history(utilities, conversion) -> None:
    """Иначе включённое сегодня правило высыпало бы в чат все депозиты за месяц."""
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    rule_id = await _deposit_rule(utilities)
    import app.services.telegram as telegram_module

    original = telegram_module.send_message
    telegram_module.send_message = fake_send
    try:
        first = await AlertEngine(SessionLocal).run()
    finally:
        telegram_module.send_message = original

    assert first["alerts"] == 0
    assert sent == []
    async with SessionLocal() as db:
        rule = await db.get(AlertRule, uuid.UUID(rule_id))
        # Первый прогон только ставит курсор — с него и пойдут новые депозиты.
        assert rule.cursor_at is not None


async def test_a_deposit_message_fills_its_macros(utilities, conversion, monkeypatch) -> None:
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)
    rule_id = await _deposit_rule(
        utilities,
        message_template="{offer} · {campaign} · {revenue} · {click_at} · {sub_id_3}",
        timezone="UTC",
    )
    await _open_cursor(rule_id)

    result = await AlertEngine(SessionLocal).run()

    assert result["alerts"] == 1
    assert sent == ["Vulkan DE 250$ · FB | RU | Vulkan · 250.00 · 08.08.2026 11:23 · s3"]


async def test_a_deposit_is_not_sent_twice(utilities, conversion, monkeypatch) -> None:
    """Курсор двигается по просмотренным — второй прогон молчит."""
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)
    rule_id = await _deposit_rule(utilities)
    await _open_cursor(rule_id)

    await AlertEngine(SessionLocal).run()
    await AlertEngine(SessionLocal).run()

    assert len(sent) == 1


def _cond(field, operator, **rest):
    return {"field": field, "operator": operator, **rest}


def _tree(*items, op="and"):
    return {"op": op, "items": list(items)}


async def test_conditions_narrow_deposits_to_their_group_and_offer(
    utilities, conversion
) -> None:
    async with SessionLocal() as db:
        from app.models import KeitaroConversion

        row = await db.get(KeitaroConversion, conversion["id"])

    both = AlertRule(conditions=_tree(
        _cond("campaign_group", "in", values=["5"]),
        _cond("offer", "in", values=["120"]),
    ))
    other = AlertRule(conditions=_tree(_cond("campaign_group", "in", values=["9"])))
    empty = AlertRule(conditions=_tree())

    assert deposit_matches(both, row) is True
    assert deposit_matches(other, row) is False
    # Пустое дерево — это «любые депозиты», а не «никакие».
    assert deposit_matches(empty, row) is True


async def test_a_condition_can_exclude(utilities, conversion) -> None:
    """«Всё, кроме этой группы» галочками не выразить — ради этого операторы и есть."""
    async with SessionLocal() as db:
        from app.models import KeitaroConversion

        row = await db.get(KeitaroConversion, conversion["id"])

    excluded = AlertRule(conditions=_tree(
        _cond("campaign_group", "not_in", values=["5"])
    ))
    kept = AlertRule(conditions=_tree(_cond("campaign_group", "not_in", values=["9"])))
    assert deposit_matches(excluded, row) is False
    assert deposit_matches(kept, row) is True


async def test_contains_looks_at_the_name_not_the_id(utilities, conversion) -> None:
    """Человек пишет «Vulkan», а не 120 — сравнивать надо название."""
    async with SessionLocal() as db:
        from app.models import KeitaroConversion

        row = await db.get(KeitaroConversion, conversion["id"])

    assert deposit_matches(
        AlertRule(conditions=_tree(_cond("offer", "contains", text="vulkan"))), row
    ) is True
    assert deposit_matches(
        AlertRule(conditions=_tree(_cond("offer", "not_contains", text="vulkan"))), row
    ) is False
    assert deposit_matches(
        AlertRule(conditions=_tree(_cond("offer", "contains", text="jozz"))), row
    ) is False


async def test_an_or_group_inside_and(utilities, conversion) -> None:
    async with SessionLocal() as db:
        from app.models import KeitaroConversion

        row = await db.get(KeitaroConversion, conversion["id"])

    rule = AlertRule(conditions=_tree(
        _cond("campaign_group", "in", values=["5"]),
        _tree(
            _cond("offer", "in", values=["999"]),
            _cond("offer", "contains", text="Vulkan"),
            op="or",
        ),
    ))
    # Группа не подошла по id, но подошла по названию — ИЛИ внутри И.
    assert deposit_matches(rule, row) is True


def test_a_broken_condition_tree_does_not_break_the_engine() -> None:
    """Дерево лежит в JSON-колонке — однажды туда попадёт что-то неожиданное."""
    tree = normalize_conditions({
        "op": "мусор",
        "items": [
            {"field": "выдумка", "operator": "in", "values": ["1"]},
            {"field": "offer", "operator": "как-то", "values": ["1"]},
            {"field": "offer", "operator": "in", "values": []},
            {"field": "offer", "operator": "contains", "text": "  "},
            "строка",
            {"op": "or", "items": []},
            {"field": "offer", "operator": "in", "values": ["120"]},
        ],
    })
    assert tree == {
        "op": "and",
        "items": [{"field": "offer", "operator": "in", "values": ["120"]}],
    }


async def test_the_click_time_is_shown_in_the_rule_timezone(utilities, conversion) -> None:
    async with SessionLocal() as db:
        from app.models import KeitaroConversion

        row = await db.get(KeitaroConversion, conversion["id"])

    moscow = AlertRule(timezone="Europe/Moscow", message_template="{click_at}")
    utc = AlertRule(timezone="UTC", message_template="{click_at}")
    # 11:23 UTC — это 14:23 в Москве; без пересчёта сообщение спорило бы с трекером.
    assert render_deposit(moscow, row) == "08.08.2026 14:23"
    assert render_deposit(utc, row) == "08.08.2026 11:23"


async def test_a_report_counts_the_period_and_sends_it(utilities, monkeypatch) -> None:
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)
    with _admin_client() as client:
        client.post(
            "/api/v1/utilities/alerts",
            json=_report(
                utilities,
                schedule="every_15",
                message_template="{leads}/{sales} {revenue} {spend} {profit} {roi}",
            ),
        )

    result = await AlertEngine(SessionLocal).run()

    # Отчёт считает всю команду, поэтому ждём ровно то же, что даёт формула
    # Медиаборда за этот день: соседние тесты кладут свои строки в тот же день.
    today = _team_today()
    async with SessionLocal() as db:
        metrics = await metrics_for(db, utilities["workspace"], today, today)
    expected = (
        f"{int(metrics['leads'])}/{int(metrics['sales'])} "
        f"{metrics['revenue']:.2f} {metrics['spend']:.2f} "
        f"{metrics['profit']:.2f} {metrics['roi']:.2f}"
    )

    assert result["alerts"] == 1
    assert sent == [expected]
    # Значения действительно подставлены, а не остались макросами.
    assert "{leads}" not in sent[0]


def test_the_schedule_waits_for_its_slot() -> None:
    now = datetime(2026, 8, 8, 6, 0, tzinfo=UTC)
    created = datetime(2026, 8, 1, tzinfo=UTC)
    # 06:00 UTC — это 09:00 в Москве, слот наступил.
    due = AlertRule(schedule="daily_09", timezone="Europe/Moscow", created_at=created)
    assert scheduled_now(due, now) is True

    early = AlertRule(schedule="daily_18", timezone="Europe/Moscow", created_at=created)
    assert scheduled_now(early, now) is False

    fired = AlertRule(
        schedule="daily_09", timezone="Europe/Moscow", created_at=created,
        last_fired_at=datetime(2026, 8, 8, 6, 5, tzinfo=UTC),
    )
    # В этот слот уже отправляли — второй раз не идём.
    assert scheduled_now(fired, now) is False


def test_a_custom_schedule_respects_hours_and_minutes() -> None:
    created = datetime(2026, 8, 1, tzinfo=UTC)
    rule = AlertRule(
        schedule="daily_1435", timezone="Europe/Moscow", created_at=created
    )

    assert scheduled_now(rule, datetime(2026, 8, 8, 11, 34, tzinfo=UTC)) is False
    assert scheduled_now(rule, datetime(2026, 8, 8, 11, 35, tzinfo=UTC)) is True

    rule.last_fired_at = datetime(2026, 8, 8, 11, 35, tzinfo=UTC)
    assert scheduled_now(rule, datetime(2026, 8, 8, 11, 40, tzinfo=UTC)) is False


def test_a_fresh_rule_does_not_fire_at_a_slot_that_already_passed() -> None:
    """Иначе правило выстреливает сразу после сохранения — за прошедшие сутки."""
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    late = AlertRule(
        schedule="daily_09",
        timezone="UTC",
        created_at=datetime(2026, 8, 8, 11, 0, tzinfo=UTC),
    )
    assert scheduled_now(late, now) is False


def test_html_tags_survive_but_values_are_escaped() -> None:
    """Теги шаблона должны сработать, а угловая скобка в данных — остаться текстом."""
    from app.services import alert_macros

    text = alert_macros.render(
        "<blockquote>{offer}</blockquote>",
        {"offer": "Vulkan <DE> 250$"},
        escape_values=True,
    )

    assert text.startswith("<blockquote>")
    assert text.endswith("</blockquote>")
    # Скобки из названия оффера не должны стать разметкой Telegram.
    assert "&lt;DE&gt;" in text
