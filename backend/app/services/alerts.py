"""Утилиты: движок уведомлений и капов — ТЗ 9.

Метрики берутся из уже загруженного Медиаборда и синхронизированной статистики
Meta, а не из трекера напрямую: алерт должен говорить о тех же числах, что
человек видит на экране, иначе спор «у меня в CRM другое» неразрешим.

Уведомлений два вида, и они устроены по-разному. «Уведомление по депозитам»
идёт от событий: пришла продажа в журнале конверсий — ушло сообщение. «Отчёт»
идёт от времени: наступил слот расписания — ушла сводка за период.

Макросы сообщений живут в `alert_macros`: по одному каталогу работают форма,
кнопка «Тест» и этот движок, поэтому разойтись им негде.
"""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret
from app.models import (
    AlertChannel,
    AlertEvent,
    AlertRule,
    CapRule,
    KeitaroConversion,
    MediaRecord,
    Offer,
    Status,
    TelegramBot,
    User,
)
from app.services import alert_macros, telegram
from app.services.alert_fields import format_value, media_values, window_range

ZERO = Decimal("0")
# Сколько конверсий разбираем за один прогон. Ограничение спасает от хвоста:
# после долгого простоя очередь может быть в тысячи строк, и вывалить их
# все в Telegram разом — значит упереться в его лимиты.
MAX_DEPOSITS = 200

__all__ = [
    "AlertEngine",
    "CAP_METRICS",
    "CAP_METRIC_HINTS",
    "CAP_PERIODS",
    "SCHEDULES",
    "cap_offer_ids",
    "cap_period_range",
    "cap_today",
    "DEPOSIT_FIELDS",
    "DEPOSIT_OPERATORS",
    "deposit_matches",
    "describe_conditions",
    "normalize_conditions",
    "metrics_for",
    "normalize_thresholds",
    "reached_threshold",
    "render_cap_message",
    "render_deposit",
    "render_report",
    "scheduled_now",
    "window_range",
]

# Пресеты закрывают частые интервалы, а произвольное ежедневное время хранится
# безопасным кодом `daily_HHMM`: cron-строку в форме никто не проверит глазами.
SCHEDULES = {
    "every_15": {"label": "Каждые 15 минут", "every_minutes": 15},
    "every_30": {"label": "Каждые 30 минут", "every_minutes": 30},
    "hourly": {"label": "Каждый час", "every_minutes": 60},
    "every_4h": {"label": "Каждые 4 часа", "every_minutes": 240},
    "daily_09": {"label": "Ежедневно в 9:00", "hours": [9]},
    "daily_18": {"label": "Ежедневно в 18:00", "hours": [18]},
    "twice": {"label": "Дважды в день, 9:00 и 18:00", "hours": [9, 18]},
    "workdays_10": {"label": "Пн–Пт в 10:00", "hours": [10], "workdays": True},
}
CUSTOM_SCHEDULE = re.compile(r"^daily_((?:[01]\d|2[0-3]))([0-5]\d)$")

# Что можно ограничить капом. Деньги тоже: «не больше 1000 в день на оффер» —
# такой же кап, как и по депозитам.
CAP_METRICS = {
    "sales": "Депозиты (FTD)",
    "leads": "Лиды (регистрации)",
    "installs": "Инсталлы",
    "spend": "Расход (SPEND)",
}
# Что именно складывается в метрику. Без подписи «депозиты» и «лиды» путают,
# а лимит партнёрки ставится на что-то одно.
CAP_METRIC_HINTS = {
    "sales": "Считает FTD из Медиаборда",
    "leads": "Считает регистрации из Медиаборда",
    "installs": "Считает инсталлы из Медиаборда",
    "spend": "Считает расход: ручной, иначе рассчитанный по агентам",
}
CAP_PERIODS = {
    "day": "День",
    "week": "Неделя",
    "month": "Месяц",
    # Общий лимит на всё время: счётчик не обнуляется никогда. Партнёрка часто
    # даёт не «300 в день», а «3000 всего» — по дневной капе такой лимит не
    # выразить, и его пришлось бы сторожить глазами.
    "total": "Общий лимит (без сброса)",
}
# Отсюда считается общий лимит. Дата заведомо раньше любых данных CRM: у капы
# «без сброса» нет начала периода, а запрос всё равно нужно чем-то ограничить.
CAP_EPOCH = date(2000, 1, 1)


def cap_offer_ids(rule: CapRule) -> list[uuid.UUID]:
    """Офферы капы. Строки из JSON приводим к UUID и молча пропускаем мусор."""
    result: list[uuid.UUID] = []
    for value in rule.offer_ids or []:
        try:
            result.append(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return result


def cap_today(timezone_name: str | None) -> date:
    """Сегодня по таймзоне капы.

    Полночь сервера и полночь команды — разные моменты, а счётчик капы
    сбрасывается именно в её полночь.
    """
    try:
        return datetime.now(ZoneInfo(timezone_name or "UTC")).date()
    except Exception:
        return datetime.now(UTC).date()


def cap_period_range(period: str, today: date) -> tuple[date, date, str]:
    """Границы периода капа и его метка — по метке видно, что период сменился.

    У общего лимита метка постоянная: период не меняется никогда, и счётчик
    отправленных порогов не обнуляется — в этом весь смысл «без сброса».
    """
    if period == "total":
        return CAP_EPOCH, today, "all"
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6), f"{start.isoformat()}/w"
    if period == "month":
        start = today.replace(day=1)
        return start, today, f"{start.strftime('%Y-%m')}/m"
    return today, today, f"{today.isoformat()}/d"


async def metrics_for(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    first: date,
    last: date,
    *,
    user_id: uuid.UUID | None = None,
    offer_id: uuid.UUID | None = None,
    offer_ids: list[uuid.UUID] | None = None,
) -> dict[str, Decimal | None]:
    """Показатели Медиаборда за период с необязательными срезами.

    `offer_ids` складывает несколько офферов в один набор чисел — так работает
    капа на связку офферов с общим лимитом партнёрки.
    """
    filters = [
        MediaRecord.workspace_id == workspace_id,
        MediaRecord.record_date >= first,
        MediaRecord.record_date <= last,
    ]
    if user_id:
        filters.append(MediaRecord.buyer_id == user_id)
    if offer_id:
        filters.append(MediaRecord.offer_id == offer_id)
    if offer_ids:
        filters.append(MediaRecord.offer_id.in_(list(offer_ids)))
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(MediaRecord.revenue), 0),
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            MediaRecord.spend_override, MediaRecord.spend_calculated
                        )
                    ),
                    0,
                ),
                func.coalesce(func.sum(MediaRecord.installs), 0),
                func.coalesce(func.sum(MediaRecord.registrations), 0),
                func.coalesce(func.sum(MediaRecord.ftd), 0),
            ).where(*filters)
        )
    ).one()
    return media_values(
        Decimal(str(row[0] or 0)),
        Decimal(str(row[1] or 0)),
        int(row[2] or 0),
        int(row[3] or 0),
        int(row[4] or 0),
    )


def schedule_label(code: str) -> str:
    custom = CUSTOM_SCHEDULE.fullmatch(code or "")
    if custom:
        return f"Ежедневно в {custom.group(1)}:{custom.group(2)}"
    return SCHEDULES.get(code, {}).get("label", code)


# Поля условий уведомления о депозитах. Их ровно два: по чему ещё фильтровать
# депозит, в конверсии просто нет — остальное это уже не фильтр, а отчёт.
DEPOSIT_FIELDS = {
    "campaign_group": {
        "label": "Группа кампаний Keitaro",
        "source": "campaign_groups",
        "id": "campaign_group_id",
        "text": "campaign_group_name",
    },
    "offer": {
        "label": "Оффер",
        "source": "keitaro_offers",
        "id": "offer_external_id",
        "text": "offer_name",
    },
}
# Операторы. «Не в списке» здесь не роскошь: список офферов растёт каждую
# неделю, и правило «всё, кроме этих трёх» иначе пришлось бы переписывать после
# каждого нового оффера.
DEPOSIT_OPERATORS = {
    "in": {"label": "в списке", "values": True},
    "not_in": {"label": "не в списке", "values": True},
    "contains": {"label": "содержит", "values": False},
    "not_contains": {"label": "не содержит", "values": False},
}


def normalize_conditions(raw: object, depth: int = 1) -> dict:
    """Привести дерево условий из JSON к безопасному виду.

    Мусор молча выбрасывается, а не роняет движок: правило пишется формой, но
    лежит в JSON-колонке, и однажды туда попадёт что-то неожиданное.
    """
    if not isinstance(raw, dict):
        return {"op": "and", "items": []}
    op = "or" if str(raw.get("op") or "and").lower() == "or" else "and"
    items: list[dict] = []
    for entry in raw.get("items") or []:
        if not isinstance(entry, dict):
            continue
        if "items" in entry:
            # Вложенность ровно на один уровень: «(A и (B или (C и D)))» никто
            # не проверит глазами перед тем, как пустить правило в общий чат.
            if depth >= 2:
                continue
            nested = normalize_conditions(entry, depth + 1)
            if nested["items"]:
                items.append(nested)
            continue
        field = str(entry.get("field") or "")
        operator = str(entry.get("operator") or "in")
        if field not in DEPOSIT_FIELDS or operator not in DEPOSIT_OPERATORS:
            continue
        if DEPOSIT_OPERATORS[operator]["values"]:
            values = [str(value) for value in entry.get("values") or [] if str(value).strip()]
            if not values:
                continue
            items.append({"field": field, "operator": operator, "values": values[:200]})
            continue
        text = str(entry.get("text") or "").strip()
        if text:
            items.append({"field": field, "operator": operator, "text": text[:200]})
    return {"op": op, "items": items[:20]}


def _condition_holds(item: dict, conversion: KeitaroConversion) -> bool:
    meta = DEPOSIT_FIELDS[item["field"]]
    operator = item["operator"]
    if operator in {"in", "not_in"}:
        value = str(getattr(conversion, meta["id"], "") or "")
        listed = value in set(item.get("values") or [])
        return listed if operator == "in" else not listed
    # «Содержит» сравнивает название, а не id: человек пишет «Vulkan», а не 120.
    text = str(getattr(conversion, meta["text"], "") or "").casefold()
    needle = str(item.get("text") or "").casefold()
    found = bool(needle) and needle in text
    return found if operator == "contains" else not found


def conditions_hold(tree: dict, conversion: KeitaroConversion) -> bool:
    """Сработало ли дерево условий на этой конверсии.

    Пустое дерево истинно: правило «все депозиты» не должно требовать ни одного
    условия — это самый частый случай, а не забытая настройка.
    """
    items = (tree or {}).get("items") or []
    if not items:
        return True
    results = [
        conditions_hold(item, conversion) if "items" in item
        else _condition_holds(item, conversion)
        for item in items
    ]
    return all(results) if (tree.get("op") or "and") == "and" else any(results)


def deposit_matches(rule: AlertRule, conversion: KeitaroConversion) -> bool:
    return conditions_hold(normalize_conditions(rule.conditions), conversion)


def describe_conditions(tree: dict) -> str:
    """Условие человеческим текстом — для списка правил."""
    items = (tree or {}).get("items") or []
    if not items:
        return "любые депозиты"
    joiner = " И " if (tree.get("op") or "and") == "and" else " ИЛИ "
    parts = []
    for item in items:
        if "items" in item:
            parts.append(f"({describe_conditions(item)})")
            continue
        label = DEPOSIT_FIELDS[item["field"]]["label"]
        operator = DEPOSIT_OPERATORS[item["operator"]]["label"]
        if item["operator"] in {"in", "not_in"}:
            count = len(item.get("values") or [])
            parts.append(f"{label} {operator} ({count})")
        else:
            parts.append(f"{label} {operator} «{item.get('text')}»")
    return joiner.join(parts)


def deposit_values(rule: AlertRule, conversion: KeitaroConversion) -> dict:
    values = {
        "campaign": conversion.campaign_name or conversion.campaign_external_id,
        "offer": conversion.offer_name or conversion.offer_external_id,
        "click_at": alert_macros.moment(conversion.click_at, rule.timezone),
        "conversion_at": alert_macros.moment(conversion.conversion_at, rule.timezone),
        "revenue": alert_macros.money(conversion.revenue),
        "country": conversion.country_code,
    }
    subs = conversion.sub_values or {}
    for index in range(1, 11):
        key = f"sub_id_{index}"
        values[key] = subs.get(key) or None
    return values


def render_deposit(rule: AlertRule, conversion: KeitaroConversion) -> str:
    template = rule.message_template or alert_macros.DEFAULT_DEPOSIT_TEMPLATE
    return alert_macros.render(
        template, deposit_values(rule, conversion), escape_values=True
    )


def report_values(metrics: dict, first: date, last: date) -> dict:
    period = (
        first.strftime("%d.%m.%Y")
        if first == last
        else f"{first.strftime('%d.%m.%Y')} — {last.strftime('%d.%m.%Y')}"
    )
    roi = metrics.get("roi")
    return {
        "period": period,
        "leads": int(metrics.get("leads") or 0),
        "sales": int(metrics.get("sales") or 0),
        "revenue": alert_macros.money(metrics.get("revenue")),
        "spend": alert_macros.money(metrics.get("spend")),
        "profit": alert_macros.money(metrics.get("profit")),
        # ROI без расхода не ноль, а неизвестен — так и пишем.
        "roi": alert_macros.money(roi) if roi is not None else None,
    }


def render_report(rule: AlertRule, metrics: dict, first: date, last: date) -> str:
    template = rule.message_template or alert_macros.DEFAULT_REPORT_TEMPLATE
    return alert_macros.render(
        template, report_values(metrics, first, last), escape_values=True
    )


def rule_local(rule: AlertRule, moment: datetime) -> datetime:
    """Момент в таймзоне правила.

    Пересчитываем переданное время, а не берём часы у системных: функция,
    которая внутри смотрит на настенные часы, и проверяется только настенными
    часами — а расписание должно быть проверяемо.
    """
    try:
        return moment.astimezone(ZoneInfo(rule.timezone or "UTC"))
    except Exception:
        return moment.astimezone(UTC)


def rule_now(rule: AlertRule) -> datetime:
    """Сейчас по таймзоне правила: по ней считается период отчёта."""
    return rule_local(rule, datetime.now(UTC))


def scheduled_now(rule: AlertRule, now: datetime) -> bool:
    """Пора ли слать отчёт.

    Точное совпадение минуты не годится: задача просыпается раз в несколько
    минут и легко проскочит нужную. Поэтому у пресетов «раз в N минут» проверка
    по прошедшему времени, а у привязанных к часу — «время наступило и в этот
    слот ещё не отправляли».
    """
    custom = CUSTOM_SCHEDULE.fullmatch(rule.schedule or "")
    preset = SCHEDULES.get(rule.schedule) or SCHEDULES["daily_09"]
    local = rule_local(rule, now)
    fired = _as_utc(rule.last_fired_at)

    every = preset.get("every_minutes")
    if every:
        return not fired or fired + timedelta(minutes=every) <= now

    if preset.get("workdays") and local.weekday() > 4:
        return False
    slots = (
        [(int(custom.group(1)), int(custom.group(2)))]
        if custom
        else [(hour, 0) for hour in preset.get("hours") or []]
    )
    passed = [slot for slot in slots if (local.hour, local.minute) >= slot]
    if not passed:
        return False
    hour, minute = max(passed)
    slot = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if fired and fired >= slot.astimezone(UTC):
        return False
    created = _as_utc(rule.created_at)
    # Правило, созданное после сегодняшнего времени отправки, ждёт следующего
    # слота: иначе оно выстреливает сразу после сохранения.
    return not (not fired and created and created > slot.astimezone(UTC))


def render_cap_message(
    rule: CapRule, value: Decimal, percent: int, subject: str | None
) -> str:
    label = CAP_METRICS.get(rule.metric, rule.metric)
    limit = rule.limit_value.quantize(Decimal("0.01"))
    icon = "🛑" if percent >= 100 else "⚠️"
    who = f"\nКто: {subject}" if subject else ""
    return (
        f"{icon} CAP: {rule.name}{who}\n"
        f"{label}: {format_value(rule.metric, value)} из {limit} ({percent} %)\n"
        f"Период: {CAP_PERIODS.get(rule.period, rule.period)}"
    )


def _as_utc(value: datetime | None) -> datetime | None:
    """Привести время из базы к UTC-aware.

    PostgreSQL отдаёт `timestamptz` с зоной, SQLite — без неё. Сравнить их
    между собой нельзя, и правило падало бы ровно там, где второй прогон
    проверяет паузу.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def normalize_thresholds(raw: object) -> list[int]:
    """Пороги в процентах: целые, по возрастанию, без повторов."""
    if not isinstance(raw, list):
        return [100]
    values: list[int] = []
    for item in raw[:10]:
        try:
            percent = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= percent <= 1000 and percent not in values:
            values.append(percent)
    return sorted(values) or [100]


def reached_threshold(thresholds: list[int], percent: int) -> int:
    """Самый высокий порог, который уже перейден."""
    passed = [value for value in thresholds if percent >= value]
    return max(passed) if passed else 0


class AlertEngine:
    """Проверка правил и доставка уведомлений.

    Сессия создаётся на каждый прогон, а не переиспользуется: задача крутится в
    воркере часами, и долгоживущая транзакция держала бы блокировки.
    """

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def run(self) -> dict:
        async with self._session_factory() as db:
            now = datetime.now(UTC)
            triggered = await self._run_alerts(db, now)
            caps = await self._run_caps(db, now)
            await db.commit()
            return {"alerts": triggered, "caps": caps}

    async def _bot_token(self, db: AsyncSession, workspace_id: uuid.UUID) -> str | None:
        bot = await db.scalar(
            select(TelegramBot).where(
                TelegramBot.workspace_id == workspace_id,
                TelegramBot.status == Status.active,
            )
        )
        if not bot:
            return None
        return decrypt_secret(bot.token_encrypted)

    async def _deliver(
        self,
        db: AsyncSession,
        rule,
        channel: AlertChannel | None,
        message: str,
        value: Decimal | None,
        kind: str,
    ) -> None:
        """Отправить и записать событие — даже если отправка не удалась."""
        event = AlertEvent(
            workspace_id=rule.workspace_id,
            rule_name=rule.name,
            kind=kind,
            value=value,
            message=message,
        )
        if isinstance(rule, CapRule):
            event.cap_rule_id = rule.id
        else:
            event.alert_rule_id = rule.id
        db.add(event)

        token = await self._bot_token(db, rule.workspace_id)
        if not token:
            event.error = "Бот Telegram не подключён"
            return
        if channel is None or channel.status != Status.active:
            event.error = "Канал выключен или удалён"
            return
        try:
            await telegram.send_message(
                token, channel.chat_id, message,
                getattr(rule, "thread_id", None) or channel.thread_id,
            )
            event.delivered = True
        except telegram.TelegramError as exc:
            event.error = str(exc)

    async def _run_alerts(self, db: AsyncSession, now: datetime) -> int:
        rules = list(
            (
                await db.execute(
                    select(AlertRule).where(AlertRule.status == Status.active)
                )
            ).scalars()
        )
        fired = 0
        for rule in rules:
            if rule.kind == "report":
                fired += await self._run_report(db, rule, now)
            else:
                fired += await self._run_deposit(db, rule, now)
        return fired

    async def _run_deposit(self, db: AsyncSession, rule: AlertRule, now: datetime) -> int:
        """Сообщения по новым депозитам.

        Курсор идёт по `seen_at` — когда конверсию увидели мы, а не когда она
        случилась: трекер отдаёт их с задержкой и задним числом, и курсор по
        собственному времени конверсии молча пропускал бы такие.

        У нового правила курсор пуст, и брать всю историю нельзя: включённое
        сегодня правило высыпало бы в чат все депозиты за три месяца. Поэтому
        первый прогон только ставит курсор.
        """
        if rule.cursor_at is None:
            rule.cursor_at = now
            return 0
        rows = list(
            (
                await db.execute(
                    select(KeitaroConversion)
                    .where(
                        KeitaroConversion.workspace_id == rule.workspace_id,
                        KeitaroConversion.status == "sale",
                        KeitaroConversion.seen_at > rule.cursor_at,
                    )
                    .order_by(KeitaroConversion.seen_at)
                    .limit(MAX_DEPOSITS)
                )
            ).scalars()
        )
        if not rows:
            return 0
        channel = await db.get(AlertChannel, rule.channel_id)
        fired = 0
        for conversion in rows:
            if not deposit_matches(rule, conversion):
                continue
            await self._deliver(
                db, rule, channel, render_deposit(rule, conversion),
                conversion.revenue, "deposit",
            )
            fired += 1
        # Курсор двигаем по всем просмотренным, а не только по отправленным:
        # иначе конверсии, не прошедшие фильтр, перебирались бы вечно.
        rule.cursor_at = rows[-1].seen_at
        if fired:
            rule.last_fired_at = now
        return fired

    async def _run_report(self, db: AsyncSession, rule: AlertRule, now: datetime) -> int:
        if not scheduled_now(rule, now):
            return 0
        first, last = window_range(rule.window, rule_now(rule).date())
        metrics = await metrics_for(db, rule.workspace_id, first, last)
        channel = await db.get(AlertChannel, rule.channel_id)
        await self._deliver(
            db, rule, channel, render_report(rule, metrics, first, last),
            metrics.get("profit"), "report",
        )
        rule.last_fired_at = now
        return 1

    async def _cap_subject(self, db: AsyncSession, rule: CapRule) -> str | None:
        """Кого касается капа: баер и перечисленные офферы.

        Больше трёх офферов в заголовок не влезает — остальные сворачиваются
        в «+N», иначе сообщение начиналось бы с простыни названий.
        """
        parts: list[str] = []
        if rule.user_id:
            user = await db.get(User, rule.user_id)
            if user:
                parts.append(user.name or user.login)
        offer_ids = cap_offer_ids(rule)
        if offer_ids:
            names = list(
                (
                    await db.execute(select(Offer.name).where(Offer.id.in_(offer_ids)))
                ).scalars()
            )
            if names:
                shown = ", ".join(sorted(names)[:3])
                if len(names) > 3:
                    shown += f" +{len(names) - 3}"
                parts.append(shown)
        return " · ".join(parts) if parts else None

    async def _run_caps(self, db: AsyncSession, now: datetime) -> int:
        rules = list(
            (
                await db.execute(select(CapRule).where(CapRule.status == Status.active))
            ).scalars()
        )
        fired = 0
        for rule in rules:
            if rule.limit_value <= ZERO:
                continue
            first, last, period_key = cap_period_range(
                rule.period, cap_today(rule.timezone)
            )
            # Новый период — счётчик порогов обнуляется, иначе после смены
            # суток кап молчал бы до самого следующего рубежа.
            if rule.notified_period != period_key:
                rule.notified_period = period_key
                rule.notified_percent = 0
            values = await metrics_for(
                db,
                rule.workspace_id,
                first,
                last,
                user_id=rule.user_id,
                offer_ids=cap_offer_ids(rule),
            )
            value = values.get(rule.metric) or ZERO
            percent = int(value / rule.limit_value * 100)
            thresholds = normalize_thresholds(rule.notify_at)
            reached = reached_threshold(thresholds, percent)
            if reached <= rule.notified_percent:
                continue
            channel = await db.get(AlertChannel, rule.channel_id)
            subject = await self._cap_subject(db, rule)
            await self._deliver(
                db,
                rule,
                channel,
                render_cap_message(rule, value, percent, subject),
                value,
                "cap",
            )
            rule.notified_percent = reached
            rule.last_fired_at = now
            fired += 1
        return fired
