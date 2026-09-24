"""Утилиты: Alert и CAP — ТЗ 9."""

import uuid
from datetime import UTC, date, datetime, timedelta
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
    KeitaroCampaign,
    KeitaroGroup,
    MediaRecord,
    Offer,
    Role,
    Session,
    Status,
    TelegramBot,
    User,
    UserParent,
)
from app.services import alerts, telegram
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


async def test_reference_contains_only_all_active_keitaro_offers(utilities) -> None:
    """CAP не смешивает актуальный каталог с ручными и архивными офферами."""
    marker = uuid.uuid4().hex
    async with SessionLocal() as db:
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == utilities["workspace"]
            )
        )
        offers = [
            Offer(
                workspace_id=utilities["workspace"],
                connection_id=connection.id,
                external_id=f"cap-live-{marker}-{index}",
                name=f"CAP live {marker} {index:03d}",
                group_name="LIVE",
                keitaro_state=Status.active,
            )
            for index in range(505)
        ]
        offers.extend(
            [
                Offer(
                    workspace_id=utilities["workspace"],
                    external_id=f"cap-manual-{marker}",
                    name=f"CAP manual {marker}",
                    keitaro_state=Status.active,
                ),
                Offer(
                    workspace_id=utilities["workspace"],
                    connection_id=connection.id,
                    external_id=f"cap-inactive-{marker}",
                    name=f"CAP inactive {marker}",
                    group_name="LIVE",
                    keitaro_state=Status.inactive,
                ),
                Offer(
                    workspace_id=utilities["workspace"],
                    connection_id=connection.id,
                    external_id=f"cap-offers-{marker}",
                    name=f"CAP OFFERS group {marker}",
                    group_name="OFFERS",
                    keitaro_state=Status.active,
                ),
            ]
        )
        db.add_all(offers)
        await db.commit()
        created_ids = [offer.id for offer in offers]

    with _admin_client() as client:
        response = client.get("/api/v1/utilities/reference")

    async with SessionLocal() as db:
        await db.execute(delete(Offer).where(Offer.id.in_(created_ids)))
        await db.commit()

    assert response.status_code == 200
    names = {item["name"] for item in response.json()["offers"]}
    assert f"CAP live {marker} 504" in names
    assert f"CAP manual {marker}" not in names
    assert f"CAP inactive {marker}" not in names
    assert f"CAP OFFERS group {marker}" not in names


async def test_reference_uses_current_keitaro_groups_not_historical_campaigns(
    utilities,
) -> None:
    """Удалённая группа остаётся в истории кампаний, но не возвращается в форму."""
    marker = uuid.uuid4().hex
    async with SessionLocal() as db:
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == utilities["workspace"]
            )
        )
        current = KeitaroGroup(
            workspace_id=utilities["workspace"],
            connection_id=connection.id,
            resource_type="campaigns",
            external_id=f"current-{marker}",
            name=f"Current {marker}",
        )
        stale = KeitaroCampaign(
            workspace_id=utilities["workspace"],
            connection_id=connection.id,
            external_id=f"campaign-{marker}",
            name=f"Historical campaign {marker}",
            group_external_id=f"stale-{marker}",
            group_name=f"Stale {marker}",
            status=Status.inactive,
        )
        db.add_all([current, stale])
        await db.commit()
        current_id = current.id
        stale_id = stale.id

    with _admin_client() as client:
        response = client.get("/api/v1/utilities/reference")

    async with SessionLocal() as db:
        await db.execute(delete(KeitaroCampaign).where(KeitaroCampaign.id == stale_id))
        await db.execute(delete(KeitaroGroup).where(KeitaroGroup.id == current_id))
        await db.commit()

    assert response.status_code == 200
    groups = {item["code"]: item["label"] for item in response.json()["campaign_groups"]}
    assert groups[f"current-{marker}"] == f"Current {marker}"
    assert f"stale-{marker}" not in groups


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


async def test_a_failed_delivery_is_retried_without_recreating_the_event(
    utilities, monkeypatch
) -> None:
    calls = 0

    async def flaky_send(token, chat_id, text, thread_id=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise telegram.TelegramError("Временная ошибка")

    monkeypatch.setattr(telegram, "send_message", flaky_send)
    with _admin_client() as client:
        client.post(
            "/api/v1/utilities/alerts",
            json=_report(utilities, schedule="every_15"),
        )

    first = await AlertEngine(SessionLocal).run()
    async with SessionLocal() as db:
        event = await db.scalar(
            select(AlertEvent).where(AlertEvent.workspace_id == utilities["workspace"])
        )
        assert event.delivered is False
        assert event.attempts == 1
        event.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

    second = await AlertEngine(SessionLocal).run()
    async with SessionLocal() as db:
        events = list(
            (
                await db.execute(
                    select(AlertEvent).where(
                        AlertEvent.workspace_id == utilities["workspace"]
                    )
                )
            ).scalars()
        )

    assert first["alerts"] == 1
    assert second["alerts"] == 0
    assert second["delivered"] == 1
    assert calls == 2
    assert len(events) == 1
    assert events[0].delivered is True
    assert events[0].attempts == 2


async def test_the_journal_filters_by_delivery(utilities, monkeypatch) -> None:
    """Фильтр «отправлено / не ушло» отбирает записи на сервере.

    Журнал отдаётся последними пятьюдесятью записями: среди них может не быть
    ни одной неудачной, а ищут в нём именно их. Фильтровать в браузере значило
    бы искать в том, что и так уже обрезано.
    """
    async with SessionLocal() as db:
        now = datetime.now(UTC)
        db.add_all(
            [
                AlertEvent(
                    workspace_id=utilities["workspace"],
                    rule_name="Ушло",
                    kind="alert",
                    message="доставлено",
                    delivered=True,
                    created_at=now,
                ),
                AlertEvent(
                    workspace_id=utilities["workspace"],
                    rule_name="Не ушло",
                    kind="alert",
                    message="не доставлено",
                    delivered=False,
                    error="Telegram: chat not found",
                    created_at=now - timedelta(minutes=1),
                ),
            ]
        )
        await db.commit()

    with _admin_client() as client:
        every = client.get("/api/v1/utilities/events").json()["items"]
        sent = client.get("/api/v1/utilities/events?delivered=true").json()["items"]
        stuck = client.get("/api/v1/utilities/events?delivered=false").json()["items"]

    assert {"Ушло", "Не ушло"} <= {row["rule_name"] for row in every}
    assert {row["delivered"] for row in sent} == {True}
    assert {row["delivered"] for row in stuck} == {False}
    assert "Не ушло" in {row["rule_name"] for row in stuck}
    assert "Не ушло" not in {row["rule_name"] for row in sent}


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
    assert sent[0].startswith("⚠️ CAP Alert: 80% достигнуто")


async def test_a_new_period_lets_the_cap_warn_again(utilities, monkeypatch) -> None:
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)

    with _admin_client() as client:
        client.post("/api/v1/utilities/caps", json=_cap(utilities)).json()
    await AlertEngine(SessionLocal).run()

    original_period_range = alerts.cap_period_range

    def next_period(period, today):
        first, last, _key = original_period_range(period, today)
        return first, last, "next-period/d"

    monkeypatch.setattr(alerts, "cap_period_range", next_period)

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


async def test_selected_offers_are_not_intersected_with_cap_owner(utilities) -> None:
    """Тимлид может держать капу на оффер баера своей команды."""
    async with SessionLocal() as db:
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        other = User(
            workspace_id=utilities["workspace"],
            role_id=buyer_role.id,
            login=f"cap-owner-{uuid.uuid4().hex[:8]}",
            name="Другой владелец капы",
            password_hash="not-used",
            status=Status.active,
        )
        db.add(other)
        await db.commit()
        other_id = other.id

    try:
        with _admin_client() as client:
            created = client.post(
                "/api/v1/utilities/caps",
                json=_cap(
                    utilities,
                    name="CAP тимлида на оффер баера",
                    user_id=str(other_id),
                ),
            ).json()
            progress = client.get(
                f"/api/v1/utilities/caps/{created['id']}/progress"
            ).json()

        assert Decimal(str(progress["value"])) == Decimal("8")
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(CapRule).where(CapRule.name == "CAP тимлида на оффер баера")
            )
            await db.execute(delete(User).where(User.id == other_id))
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
    # Неизвестная зона не роняет расчёт, а откатывается к рабочей зоне CRM.
    assert cap_today("Nowhere/Nothing") == cap_today("Europe/Moscow")


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
        offers = await AlertEngine(SessionLocal)._cap_offers(db, rule)
        await db.execute(delete(CapRule).where(CapRule.id == rule.id))
        await db.commit()

    assert "Оффер для алертов" in offers


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


async def test_a_lead_that_becomes_a_sale_reaches_the_chat(
    utilities, monkeypatch
) -> None:
    """Keitaro заводит конверсию лидом и переводит её в sale тем же id.

    Пропуская известные строки целиком, синхронизация навсегда оставляла такой
    депозит лидом, и уведомление по нему не приходило никогда.
    """
    from app.models import KeitaroConversion
    from app.services.keitaro_sync import _upsert_conversions

    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)

    async with SessionLocal() as db:
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == utilities["workspace"]
            )
        )
        config = {
            "id": connection.id,
            "workspace_id": utilities["workspace"],
            "timezone": "Europe/Moscow",
        }
        row = {
            "conversion_id": "conv-lead-then-sale",
            "status": "lead",
            "campaign_id": "77",
            "offer_id": "120",
            "offer": "Vulkan DE 250$",
            "revenue": "0",
        }
        assert await _upsert_conversions(db, config, [row], {}) == 1
        await db.commit()
    rule_id = await _deposit_rule(utilities)
    await _open_cursor(rule_id)
    # Лид уведомления не даёт: правило смотрит только на продажи.
    assert (await AlertEngine(SessionLocal).run())["alerts"] == 0

    async with SessionLocal() as db:
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == utilities["workspace"]
            )
        )
        config = {"id": connection.id, "workspace_id": utilities["workspace"]}
        row["status"] = "sale"
        row["revenue"] = "250"
        assert await _upsert_conversions(db, config, [row], {}) == 1
        await db.commit()

    assert (await AlertEngine(SessionLocal).run())["alerts"] == 1
    assert len(sent) == 1

    async with SessionLocal() as db:
        stored = await db.scalar(
            select(KeitaroConversion).where(
                KeitaroConversion.external_id == "conv-lead-then-sale"
            )
        )
        assert stored.status == "sale"
        assert stored.revenue == Decimal("250")
        await db.delete(stored)
        await db.commit()


async def test_keitaro_event_aliases_are_saved_with_revenue_as_payout(utilities) -> None:
    """Реальный журнал Keitaro использует event_id и datetime."""
    from app.models import KeitaroConversion
    from app.services.keitaro_sync import _upsert_conversions

    observed_at = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    async with SessionLocal() as db:
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == utilities["workspace"]
            )
        )
        config = {"id": connection.id, "workspace_id": utilities["workspace"]}
        assert await _upsert_conversions(
            db,
            config,
            [
                {
                    "event_id": "real-keitaro-event",
                    "datetime": "2026-09-03 11:59:30",
                    "status": "sale",
                    "revenue": "42.50",
                    "country": "DE",
                },
                {
                    "event_id": "real-keitaro-lead",
                    "datetime": "2026-09-03 11:58:00",
                    "status": "lead",
                    "revenue": "0",
                    "country": "DE",
                },
            ],
            {},
            observed_at=observed_at,
        ) == 2
        await db.commit()

        stored = await db.scalar(
            select(KeitaroConversion).where(
                KeitaroConversion.external_id == "real-keitaro-event"
            )
        )
        assert stored is not None
        assert stored.conversion_at.replace(tzinfo=UTC) == datetime(
            2026, 9, 3, 8, 59, 30, tzinfo=UTC
        )
        assert stored.revenue == Decimal("42.50")
        assert stored.payout == Decimal("42.50")
        assert stored.seen_at.replace(tzinfo=UTC) == observed_at
        lead = await db.scalar(
            select(KeitaroConversion).where(
                KeitaroConversion.external_id == "real-keitaro-lead"
            )
        )
        assert lead is not None
        assert lead.status == "lead"
        await db.delete(stored)
        await db.delete(lead)
        await db.commit()


async def test_first_conversion_import_moves_deposit_cursor_past_history(utilities) -> None:
    """Первичный импорт заполняет дедупликацию, но не рассылает старые sale."""
    from app.models import KeitaroConversion
    from app.services.keitaro_sync import KeitaroSyncEngine

    class ConversionClient:
        async def conversions(self, *_args, **_kwargs):
            return [
                {
                    "event_id": "bootstrap-history-event",
                    "datetime": "2026-09-02 08:00:00",
                    "status": "sale",
                    "revenue": "10",
                    "sub_id": "bootstrap-click",
                }
            ]

        async def click_times(self, sub_ids, **kwargs):
            assert sub_ids == ["bootstrap-click"]
            assert kwargs == {
                "start": date(2026, 6, 4),
                "end": date(2026, 9, 3),
                "timezone": "UTC",
            }
            return {"bootstrap-click": "2026-09-02 07:30:00"}

    async with SessionLocal() as db:
        connection = IntegrationConnection(
            workspace_id=utilities["workspace"],
            name=f"Bootstrap {uuid.uuid4()}",
            base_url="https://bootstrap.example",
            api_key_encrypted=encrypt_secret("test-key"),
            timezone="UTC",
        )
        rule = AlertRule(
            workspace_id=utilities["workspace"],
            name="Bootstrap deposits",
            kind="deposit",
            channel_id=utilities["channel"],
            cursor_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        db.add_all([connection, rule])
        await db.commit()
        connection_id = connection.id
        rule_id = rule.id

    config = {
        "id": connection_id,
        "workspace_id": utilities["workspace"],
        "timezone": "UTC",
    }
    saved = await KeitaroSyncEngine(SessionLocal)._sync_conversions(
        config,
        ConversionClient(),
        date(2026, 9, 2),
        date(2026, 9, 3),
    )
    assert saved == 1

    async with SessionLocal() as db:
        stored = await db.scalar(
            select(KeitaroConversion).where(
                KeitaroConversion.connection_id == connection_id
            )
        )
        rule = await db.get(AlertRule, rule_id)
        assert stored is not None
        assert stored.click_at.replace(tzinfo=UTC) == datetime(
            2026, 9, 2, 7, 30, tzinfo=UTC
        )
        assert rule.cursor_at >= stored.seen_at
        await db.delete(rule)
        await db.delete(stored)
        await db.delete(await db.get(IntegrationConnection, connection_id))
        await db.commit()


async def _deposits(utilities, count: int) -> list:
    """Пачка продаж в журнале — как всплеск после запуска связки."""
    from app.models import KeitaroConversion

    made = []
    async with SessionLocal() as db:
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == utilities["workspace"]
            )
        )
        for index in range(count):
            row = KeitaroConversion(
                workspace_id=utilities["workspace"],
                connection_id=connection.id,
                external_id=f"burst-{index}-{uuid.uuid4()}",
                status="sale",
                conversion_at=datetime(2026, 8, 8, 15, index % 60, tzinfo=UTC),
                click_at=datetime(2026, 8, 8, 11, index % 60, tzinfo=UTC),
                campaign_external_id="77",
                campaign_name="FB | RU | Vulkan",
                offer_external_id="120",
                offer_name="Vulkan DE 250$",
                revenue=Decimal("10"),
                sub_values={},
            )
            db.add(row)
            made.append(row)
        await db.commit()
        return [row.id for row in made]


async def _drop(ids) -> None:
    from app.models import KeitaroConversion

    async with SessionLocal() as db:
        await db.execute(delete(KeitaroConversion).where(KeitaroConversion.id.in_(ids)))
        await db.commit()


async def test_a_burst_of_deposits_goes_as_one_message(utilities, monkeypatch) -> None:
    """Иначе сотня депозитов упрётся в лимит Telegram и растянется на часы."""
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)
    monkeypatch.setattr(alerts, "SEND_PAUSE_SECONDS", 0)
    ids = await _deposits(utilities, 12)
    try:
        rule_id = await _deposit_rule(utilities, message_template="{offer} · {revenue}")
        await _open_cursor(rule_id)

        result = await AlertEngine(SessionLocal).run()

        assert result["alerts"] == 12
        assert len(sent) == 1
        assert sent[0].startswith("🔔 Депозитов: 12")
        assert sent[0].count("Vulkan DE 250$") == 12
    finally:
        await _drop(ids)


async def test_a_few_deposits_stay_separate_messages(utilities, monkeypatch) -> None:
    """До порога склейка только мешала бы: депозит в чате читается по одному."""
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", fake_send)
    monkeypatch.setattr(alerts, "SEND_PAUSE_SECONDS", 0)
    ids = await _deposits(utilities, 3)
    try:
        rule_id = await _deposit_rule(utilities, message_template="{offer} · {revenue}")
        await _open_cursor(rule_id)

        assert (await AlertEngine(SessionLocal).run())["alerts"] == 3
        assert len(sent) == 3
    finally:
        await _drop(ids)


async def test_messages_to_one_chat_keep_a_pause(utilities, monkeypatch) -> None:
    """Telegram принимает около 20 сообщений в минуту в одну группу."""
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    async def fake_send(token, chat_id, text, thread_id=None):
        return None

    monkeypatch.setattr(telegram, "send_message", fake_send)
    monkeypatch.setattr(alerts.asyncio, "sleep", fake_sleep)
    ids = await _deposits(utilities, 3)
    try:
        rule_id = await _deposit_rule(utilities, message_template="{offer}")
        await _open_cursor(rule_id)
        await AlertEngine(SessionLocal).run()
    finally:
        await _drop(ids)

    # Первое сообщение уходит сразу, каждое следующее ждёт своей паузы.
    assert len(waits) == 2
    assert all(0 < wait <= alerts.SEND_PAUSE_SECONDS for wait in waits)


def test_a_cap_message_reads_like_the_agreed_card() -> None:
    """Порядок и подписи согласованы с заказчиком — проверяем целиком."""
    from app.models import CapRule
    from app.services.alerts import render_cap_message

    rule = CapRule(
        name="LuckyStar PE 10$ - 500 ftd",
        metric="sales",
        limit_value=Decimal("500.0000"),
        period="total",
        timezone="Europe/Moscow",
    )
    offers = ["LuckyStar burz PE 10$", "LuckyStar EVS PE 10$"]

    text = render_cap_message(rule, Decimal("500"), 100, offers)

    assert text == (
        "🔴 CAP Alert: 100% достигнуто\n\n"
        "<blockquote>LuckyStar PE 10$ - 500 ftd</blockquote>\n\n"
        "🎰 Офферы: LuckyStar burz PE 10$, LuckyStar EVS PE 10$\n"
        "📈 Прогресс: 500 / 500 (100.0%)\n"
        "⏰ Период: Общий лимит (без сброса)"
    )


def test_an_unreached_cap_only_warns() -> None:
    """До лимита — предупреждение, а не «достигнуто»."""
    from app.models import CapRule
    from app.services.alerts import render_cap_message

    rule = CapRule(
        name="Расход по связке",
        metric="spend",
        limit_value=Decimal("1500.0000"),
        period="day",
        timezone="Europe/Moscow",
    )

    text = render_cap_message(rule, Decimal("1349.5"), 90, ["Оффер А"])

    assert text.startswith("⚠️ CAP Alert: 90% достигнуто")
    # Баера в сообщении нет: кап и так адресный, а строка занимала место.
    assert "Баер" not in text
    # Лимит без хвоста нулей, а сумма — с копейками: это деньги.
    assert "📈 Прогресс: 1349.50 / 1500 (90.0%)" in text


def test_a_long_offer_list_is_cut_at_twenty() -> None:
    """Иначе сообщение упрётся в предел длины Telegram и не уйдёт вовсе."""
    from app.models import CapRule
    from app.services.alerts import render_cap_message

    rule = CapRule(
        name="Связка", metric="sales", limit_value=Decimal("10"),
        period="day", timezone="Europe/Moscow",
    )
    offers = ["Оффер %d" % index for index in range(1, 26)]

    text = render_cap_message(rule, Decimal("10"), 100, offers)

    assert "Оффер 20" in text
    assert "Оффер 21" not in text
    assert "+5" in text


def test_a_name_with_a_tag_stays_text() -> None:
    """Иначе угловая скобка в названии сломает разбор HTML у Telegram."""
    from app.models import CapRule
    from app.services.alerts import render_cap_message

    rule = CapRule(
        name="Кап <b>жирный</b>", metric="sales", limit_value=Decimal("5"),
        period="day", timezone="Europe/Moscow",
    )

    text = render_cap_message(rule, Decimal("5"), 100, ["Оффер <i>А</i>"])

    assert "<blockquote>Кап &lt;b&gt;жирный&lt;/b&gt;</blockquote>" in text
    assert "Оффер &lt;i&gt;А&lt;/i&gt;" in text


async def test_a_cap_writes_to_every_channel_it_was_given(utilities, monkeypatch) -> None:
    """Один лимит ждут в нескольких чатах — сообщение уходит в каждый."""
    sent = []

    async def fake_send(token, chat_id, text, thread_id=None):
        sent.append(chat_id)

    monkeypatch.setattr(telegram, "send_message", fake_send)

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        second = AlertChannel(
            workspace_id=admin.workspace_id,
            name="Тимлид",
            chat_id="-1009876543210",
        )
        db.add(second)
        await db.commit()
        second_id = str(second.id)

    with _admin_client() as client:
        created = client.post(
            "/api/v1/utilities/caps",
            json=_cap(utilities, channel_ids=[str(utilities["channel"]), second_id]),
        )
        listed = client.get("/api/v1/utilities/caps").json()["items"]
    result = await AlertEngine(SessionLocal).run()

    assert created.status_code == 201, created.text
    assert created.json()["channel_ids"] == [str(utilities["channel"]), second_id]
    assert sorted(created.json()["channel_names"]) == ["Команда", "Тимлид"]
    assert result["caps"] == 1
    assert sorted(sent) == ["-1001234567890", "-1009876543210"]
    assert [row["id"] for row in listed] == [created.json()["id"]]


async def test_a_cap_without_a_channel_is_refused(utilities) -> None:
    with _admin_client() as client:
        refused = client.post(
            "/api/v1/utilities/caps", json=_cap(utilities, channel_ids=[], channel_id=None)
        )
    assert refused.status_code == 422
