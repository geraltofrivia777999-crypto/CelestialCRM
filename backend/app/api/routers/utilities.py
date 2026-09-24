"""Утилиты: Alert и CAP — ТЗ 9.

Читать правила и журнал может `utilities.view`, менять — `utilities.manage`.
Отдельные права: алерт — это то, что само пишет в чат команды от её имени, и
раздавать это вместе с доступом к настройкам не стоит.

Токен бота хранится зашифрованным и через API не возвращается. Из него видно
только имя бота — по самому токену можно писать куда угодно от лица команды.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import accessible_user_ids, has_permission, require_permission
from app.core.security import decrypt_secret, encrypt_secret
from app.models import (
    AlertChannel,
    AlertEvent,
    AlertRule,
    CapRule,
    KeitaroGroup,
    Offer,
    Status,
    TelegramBot,
    User,
)
from app.schemas import (
    AlertChannelIn,
    AlertRuleIn,
    AlertTestIn,
    CapRuleIn,
    Page,
    TelegramBotIn,
)
from app.services import alert_macros, alerts, telegram
from app.services.alert_fields import WINDOWS, window_range
from app.services.alerts import (
    CAP_METRIC_HINTS,
    CAP_METRICS,
    CAP_PERIODS,
    SCHEDULES,
    ZERO,
    cap_channel_ids,
    cap_metrics,
    cap_metrics_many,
    cap_offer_ids,
    cap_period_range,
    cap_today,
    metrics_for,
    normalize_thresholds,
)
from app.services.audit import audit

router = APIRouter(prefix="/utilities", tags=["utilities"])


def _newest_first(offers: list[Offer]) -> list[Offer]:
    """Свежие офферы — наверху: CAP обычно ставят на то, что только завели.

    Порядок по id Keitaro: трекер выдаёт их по возрастанию, а наш created_at у
    всех одинаковый после первой синхронизации. Без числового id — по времени
    появления в CRM.
    """

    def key(offer: Offer) -> tuple:
        raw = str(offer.external_id or "").strip()
        number = int(raw) if raw.isdigit() else -1
        return (number, offer.created_at.timestamp() if offer.created_at else 0.0)

    return sorted(offers, key=key, reverse=True)


@router.get("/reference")
async def reference(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.view")),
) -> dict:
    """Справочник для форм: показатели, окна, каналы, офферы и люди."""
    offers = list(
        (
            await db.execute(
                select(Offer)
                .where(
                    Offer.workspace_id == current.workspace_id,
                    # CAP работает по статистике Keitaro. Ручные офферы из
                    # раздела «Офферы» в трекере не существуют и здесь не нужны.
                    Offer.connection_id.is_not(None),
                    # После пересинхронизации недоступные и удалённые в Keitaro
                    # строки остаются в истории CRM, но выбирать их для нового
                    # правила нельзя.
                    Offer.keitaro_state == Status.active,
                    # Служебная группа OFFERS принадлежит отдельному разделу
                    # CRM и не должна смешиваться с рабочими офферами CAP.
                    func.lower(func.coalesce(Offer.group_name, ""))
                    != settings.keitaro_offers_group.strip().lower(),
                )
                .order_by(Offer.name, Offer.id)
            )
        ).scalars()
    )
    # Людей в формах — только тех, чьи данные человеку доступны по области
    # доступа его роли: назначить капу на чужого баера он всё равно не сможет.
    people = list(
        (
            await db.execute(
                select(User)
                .where(
                    User.workspace_id == current.workspace_id,
                    User.status == Status.active,
                    User.id.in_(await accessible_user_ids(db, current)),
                )
                .order_by(User.name, User.login)
            )
        ).scalars()
    )
    groups = list(
        (
            await db.execute(
                select(KeitaroGroup.external_id, KeitaroGroup.name)
                .where(
                    KeitaroGroup.workspace_id == current.workspace_id,
                    KeitaroGroup.resource_type == "campaigns",
                )
                .distinct()
            )
        ).all()
    )
    return {
        # Группы кампаний Keitaro — фильтр уведомления о депозитах. Отдельный
        # справочник является источником истины: синхронизация удаляет из него
        # старые группы. Исторические кампании остаются в БД и поэтому для
        # выпадающего списка не подходят.
        "campaign_groups": sorted(
            (
                {"code": str(code), "label": name or str(code)}
                for code, name in groups
            ),
            key=lambda item: item["label"].lower(),
        ),
        "condition_fields": [
            {"code": code, "label": meta["label"], "source": meta["source"]}
            for code, meta in alerts.DEPOSIT_FIELDS.items()
        ],
        "condition_operators": [
            {"code": code, "label": meta["label"], "values": meta["values"]}
            for code, meta in alerts.DEPOSIT_OPERATORS.items()
        ],
        "schedules": [
            {"code": code, "label": preset["label"]}
            for code, preset in SCHEDULES.items()
            if code not in {"twice", "workdays_10"}
        ],
        "macros": {
            kind: [
                {"code": macro.code, "label": macro.label, "example": macro.example}
                for macro in alert_macros.macros_for(kind)
            ]
            for kind in ("deposit", "report")
        },
        "templates": {
            kind: alert_macros.default_template(kind) for kind in ("deposit", "report")
        },
        "cap_metrics": [
            {"code": code, "label": label} for code, label in CAP_METRICS.items()
        ],
        "windows": [{"code": code, "label": label} for code, label in WINDOWS.items()],
        "cap_periods": [
            {"code": code, "label": label} for code, label in CAP_PERIODS.items()
        ],
        "offers": [
            {"id": str(offer.id), "name": offer.name} for offer in _newest_first(offers)
        ],
        # Фильтр депозитов идёт по внешнему id оффера: в конверсии Keitaro
        # присылает свой id, а не наш.
        "keitaro_offers": [
            {"code": offer.external_id, "label": offer.name}
            for offer in offers
            if offer.external_id
        ],
        "users": [
            {"id": str(person.id), "name": person.name or person.login}
            for person in people
        ],
    }


# --- бот ---------------------------------------------------------------------


@router.get("/bot")
async def read_bot(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.view")),
) -> dict:
    bot = await _bot(db, current.workspace_id)
    if not bot:
        return {"connected": False}
    return {
        "connected": True,
        "username": bot.username,
        "status": bot.status.value,
        "checked_at": bot.checked_at,
        "last_error": bot.last_error,
    }


@router.put("/bot")
async def save_bot(
    payload: TelegramBotIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    """Сохранить токен, но только если Telegram его принял.

    Проверка до записи: токен с опечаткой сохранился бы молча, а узнали бы о
    нём в момент, когда алерт был действительно нужен.
    """
    try:
        info = await telegram.check_token(payload.token.strip())
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    bot = await _bot(db, current.workspace_id)
    if not bot:
        bot = TelegramBot(workspace_id=current.workspace_id)
        db.add(bot)
    bot.token_encrypted = encrypt_secret(payload.token.strip())
    bot.username = info.get("username")
    bot.status = Status.active
    bot.checked_at = datetime.now(UTC)
    bot.last_error = None
    await audit(
        db, current, "utilities.bot_saved",
        f"Подключён бот Telegram @{bot.username}", request=request,
    )
    await db.commit()
    return {"connected": True, "username": bot.username}


@router.get("/bot/chats")
async def bot_chats(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    """Чаты, куда бота уже добавили — чтобы не искать chat_id руками."""
    bot = await _bot(db, current.workspace_id)
    if not bot:
        raise HTTPException(status_code=422, detail="Сначала подключите бота Telegram")
    try:
        chats = await telegram.list_chats(decrypt_secret(bot.token_encrypted))
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    known = {row.chat_id for row in await _channels(db, current.workspace_id)}
    return {
        "chats": [dict(chat, known=chat["chat_id"] in known) for chat in chats],
    }


@router.delete("/bot")
async def delete_bot(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    bot = await _bot(db, current.workspace_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Бот не подключён")
    await db.delete(bot)
    await audit(
        db, current, "utilities.bot_deleted", "Отключён бот Telegram", request=request
    )
    await db.commit()
    return {"connected": False}


# --- каналы ------------------------------------------------------------------


@router.get("/channels", response_model=Page)
async def list_channels(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.view")),
) -> Page:
    rows = await _channels(db, current.workspace_id)
    items = [_channel_row(row) for row in rows]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


def _require_channels(current: User) -> None:
    """Каналы правит только тот, кому роль открыла вкладку «Каналы».

    Сам список каналов остаётся у всех, кто видит Утилиты: без него не выбрать
    чат в форме уведомления или CapAlert.
    """
    if not has_permission(current, "utilities.channels"):
        raise HTTPException(status_code=403, detail="Каналы недоступны вашей роли")


@router.post("/channels", status_code=201)
async def create_channel(
    payload: AlertChannelIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    _require_channels(current)
    channel = AlertChannel(workspace_id=current.workspace_id, **payload.model_dump())
    db.add(channel)
    await audit(
        db, current, "utilities.channel_created", f"Добавлен канал «{channel.name}»",
        request=request,
    )
    await db.commit()
    await db.refresh(channel)
    return _channel_row(channel)


@router.patch("/channels/{channel_id}")
async def update_channel(
    channel_id: uuid.UUID,
    payload: AlertChannelIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    _require_channels(current)
    channel = await _channel(db, current, channel_id)
    for field, value in payload.model_dump().items():
        setattr(channel, field, value)
    await audit(
        db, current, "utilities.channel_updated", f"Изменён канал «{channel.name}»",
        request=request, entity_id=str(channel.id),
    )
    await db.commit()
    return _channel_row(channel)


@router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    _require_channels(current)
    channel = await _channel(db, current, channel_id)
    name = channel.name
    await db.delete(channel)
    await audit(
        db, current, "utilities.channel_deleted", f"Удалён канал «{name}»",
        request=request, entity_id=str(channel_id),
    )
    await db.commit()
    return {"deleted": str(channel_id)}


@router.post("/channels/{channel_id}/test")
async def test_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    """Отправить в чат пробное сообщение.

    Единственный надёжный способ проверить chat_id: Bot API не подтверждает
    доступ к чату иначе, чем доставкой.
    """
    _require_channels(current)
    channel = await _channel(db, current, channel_id)
    bot = await _bot(db, current.workspace_id)
    if not bot:
        raise HTTPException(status_code=422, detail="Сначала подключите бота Telegram")
    try:
        await telegram.send_message(
            decrypt_secret(bot.token_encrypted),
            channel.chat_id,
            f"✅ Проверка канала «{channel.name}» из Celestial CRM",
            channel.thread_id,
        )
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"sent": True}


# --- правила Alert (ТЗ 9.1) --------------------------------------------------


@router.get("/alerts", response_model=Page)
async def list_alerts(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.view")),
) -> Page:
    rows = list(
        (
            await db.execute(
                select(AlertRule)
                .where(AlertRule.workspace_id == current.workspace_id)
                .order_by(AlertRule.created_at)
            )
        ).scalars()
    )
    names = await _names(db, current.workspace_id)
    items = [_alert_row(row, names) for row in rows]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.post("/alerts", status_code=201)
async def create_alert(
    payload: AlertRuleIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    await _validate_alert(db, current, payload)
    rule = AlertRule(workspace_id=current.workspace_id, **_alert_values(payload))
    db.add(rule)
    await audit(
        db, current, "utilities.alert_created", f"Создано уведомление «{rule.name}»",
        request=request,
    )
    await db.commit()
    await db.refresh(rule)
    return _alert_row(rule, await _names(db, current.workspace_id))


@router.patch("/alerts/{rule_id}")
async def update_alert(
    rule_id: uuid.UUID,
    payload: AlertRuleIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    rule = await _alert(db, current, rule_id)
    await _validate_alert(db, current, payload)
    for field, value in _alert_values(payload).items():
        setattr(rule, field, value)
    await audit(
        db, current, "utilities.alert_updated", f"Изменено уведомление «{rule.name}»",
        request=request, entity_id=str(rule.id),
    )
    await db.commit()
    return _alert_row(rule, await _names(db, current.workspace_id))


@router.delete("/alerts/{rule_id}")
async def delete_alert(
    rule_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    rule = await _alert(db, current, rule_id)
    name = rule.name
    await db.delete(rule)
    await audit(
        db, current, "utilities.alert_deleted", f"Удалено уведомление «{name}»",
        request=request, entity_id=str(rule_id),
    )
    await db.commit()
    return {"deleted": str(rule_id)}


def _alert_values(payload: AlertRuleIn) -> dict:
    """Поля правила для записи.

    В обычный dict превращается только дерево условий — оно уезжает в
    JSON-колонку. `channel_id` — настоящий внешний ключ, и строка вместо UUID
    его бы сломала.
    """
    values = payload.model_dump()
    values["conditions"] = alerts.normalize_conditions(
        payload.conditions.model_dump(mode="json")
    )
    return values


@router.post("/alerts/test")
async def test_alert(
    payload: AlertTestIn,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    """Отправить в чат пробное сообщение этого правила.

    Проверять текст с макросами до того, как он начнёт уходить в общий чат
    команды — единственный способ увидеть, что получится: подстановки видно
    только на реальных данных, а Telegram ещё и по-своему трактует разметку.

    Депозит подставляется примерный, а отчёт считается по настоящим числам за
    выбранный период: это уже готовая сводка, придумывать для неё пример
    незачем.
    """
    channel = await _channel(db, current, payload.channel_id)
    bot = await _bot(db, current.workspace_id)
    if not bot:
        raise HTTPException(status_code=422, detail="Сначала подключите бота Telegram")

    draft = AlertRule(workspace_id=current.workspace_id, **_alert_values(payload))
    if draft.kind == "report":
        first, last = window_range(draft.window, alerts.rule_now(draft).date())
        metrics = await metrics_for(db, current.workspace_id, first, last)
        message = alerts.render_report(draft, metrics, first, last)
    else:
        message = alert_macros.render(
            draft.message_template or alert_macros.DEFAULT_DEPOSIT_TEMPLATE,
            alert_macros.sample("deposit"),
            escape_values=True,
        )
    try:
        await telegram.send_message(
            decrypt_secret(bot.token_encrypted),
            channel.chat_id,
            "🧪 Тест\n" + message,
            (payload.thread_id or "").strip() or channel.thread_id,
        )
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"sent": True, "message": message}


# --- правила CAP (ТЗ 9.2) ----------------------------------------------------


@router.get("/caps")
async def list_caps(
    status: Status | None = None,
    metric: str | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.view")),
) -> dict:
    """Капы с уже посчитанным прогрессом.

    Прогресс считается здесь, а не отдельным запросом на строку: список из
    двадцати кап иначе стоил бы двадцати круговых поездок.
    """
    # Капа, назначенная на человека, — его: чужие в списке не показываем.
    # Капа без пользователя — общий лимит на связку офферов, она ничья и видна
    # всем, кто вообще открывает раздел.
    visible_users = await accessible_user_ids(db, current)
    filters = [
        CapRule.workspace_id == current.workspace_id,
        or_(CapRule.user_id.is_(None), CapRule.user_id.in_(visible_users)),
    ]
    if status:
        filters.append(CapRule.status == status)
    if metric:
        filters.append(CapRule.metric == metric)
    rows = list(
        (
            await db.execute(
                select(CapRule).where(*filters).order_by(CapRule.created_at)
            )
        ).scalars()
    )
    names = await _names(db, current.workspace_id)
    metrics_by_rule = await cap_metrics_many(db, rows)
    items = []
    for row in rows:
        item = _cap_row(row, names)
        item["progress"] = _cap_progress_values(
            row, metrics_by_rule.get(row.id) or {}
        )
        items.append(item)
    return {
        "items": items,
        "total": len(items),
        "limit": len(items),
        "offset": 0,
        # Счётчики шапки. Собираем на сервере: клиенту иначе пришлось бы
        # пересчитывать их по отфильтрованному списку и врать при фильтрах.
        "summary": {
            "total": len(items),
            "active": sum(1 for item in items if item["status"] == "active"),
            # «Достигнуто» — это про лимит, а не про статус: капа может быть
            # активной и уже выбранной.
            "reached": sum(1 for item in items if item["progress"]["percent"] >= 100),
        },
    }


def _cap_values(payload: CapRuleIn) -> dict:
    """Поля капы для записи.

    В строки приводится только `offer_ids`: это JSON-колонка, и UUID в неё не
    сериализуется. Остальные идентификаторы — настоящие внешние ключи, и
    строка вместо UUID их бы сломала.
    """
    values = payload.model_dump()
    values["offer_ids"] = [str(value) for value in values.get("offer_ids") or []]
    # Первый канал остаётся во внешнем ключе, весь список — в JSON-колонке.
    values["channel_ids"] = [str(value) for value in values.get("channel_ids") or []]
    values["notify_at"] = normalize_thresholds(values.get("notify_at"))
    return values


async def _cap_progress(db: AsyncSession, workspace_id: uuid.UUID, rule: CapRule) -> dict:
    first, last, _ = cap_period_range(rule.period, cap_today(rule.timezone))
    values = await cap_metrics(db, rule, first, last)
    return _cap_progress_values(rule, values)


def _cap_progress_values(rule: CapRule, values: dict) -> dict:
    first, last, _ = cap_period_range(rule.period, cap_today(rule.timezone))
    value = values.get(rule.metric) or ZERO
    percent = int(value / rule.limit_value * 100) if rule.limit_value else 0
    return {
        "value": value,
        "limit": rule.limit_value,
        "percent": percent,
        "period_from": first.isoformat(),
        "period_to": last.isoformat(),
    }


@router.post("/caps", status_code=201)
async def create_cap(
    payload: CapRuleIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    await _validate_cap(db, current, payload)
    values = _cap_values(payload)
    rule = CapRule(workspace_id=current.workspace_id, **values)
    db.add(rule)
    await audit(
        db, current, "utilities.cap_created", f"Создан CAP «{rule.name}»", request=request
    )
    await db.commit()
    await db.refresh(rule)
    return _cap_row(rule, await _names(db, current.workspace_id))


@router.patch("/caps/{rule_id}")
async def update_cap(
    rule_id: uuid.UUID,
    payload: CapRuleIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    rule = await _cap(db, current, rule_id)
    await _validate_cap(db, current, payload)
    values = _cap_values(payload)
    for field, value in values.items():
        setattr(rule, field, value)
    # Пороги изменились — счётчик отправленных сбрасываем, иначе новый порог
    # ниже уже пройденного никогда бы не сработал.
    rule.notified_percent = 0
    await audit(
        db, current, "utilities.cap_updated", f"Изменён CAP «{rule.name}»",
        request=request, entity_id=str(rule.id),
    )
    await db.commit()
    return _cap_row(rule, await _names(db, current.workspace_id))


@router.delete("/caps/{rule_id}")
async def delete_cap(
    rule_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.manage")),
) -> dict:
    rule = await _cap(db, current, rule_id)
    name = rule.name
    await db.delete(rule)
    await audit(
        db, current, "utilities.cap_deleted", f"Удалён CAP «{name}»",
        request=request, entity_id=str(rule_id),
    )
    await db.commit()
    return {"deleted": str(rule_id)}


@router.get("/caps/{rule_id}/progress")
async def cap_progress(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("utilities.view")),
) -> dict:
    """Сколько уже набрано из лимита за текущий период."""
    rule = await _cap(db, current, rule_id)
    return await _cap_progress(db, current.workspace_id, rule)


# --- журнал ------------------------------------------------------------------


@router.get("/events", response_model=Page)
async def list_events(
    limit: int = 50,
    delivered: bool | None = None,
    db: AsyncSession = Depends(get_db),
    # Журнал — отдельная вкладка со своим правом роли.
    current: User = Depends(require_permission("utilities.events")),
) -> Page:
    """Журнал отправок. `delivered` отбирает ушедшие или застрявшие.

    Фильтр серверный, а не в браузере: журнал отдаётся последними записями, и
    среди полусотни ушедших ни одной неудачной могло не оказаться вовсе —
    именно их и ищут, когда открывают этот список.
    """
    visible_users = await accessible_user_ids(db, current)
    visible_caps = select(CapRule.id).where(
        CapRule.workspace_id == current.workspace_id,
        or_(CapRule.user_id.is_(None), CapRule.user_id.in_(visible_users)),
    )
    filters = [
        AlertEvent.workspace_id == current.workspace_id,
        # Срабатывание чужой капы — это её название, оффер и цифры. Видно оно
        # тем же, кому видна сама капа.
        or_(AlertEvent.cap_rule_id.is_(None), AlertEvent.cap_rule_id.in_(visible_caps)),
    ]
    if delivered is not None:
        filters.append(AlertEvent.delivered.is_(delivered))
    rows = list(
        (
            await db.execute(
                select(AlertEvent)
                .where(*filters)
                .order_by(AlertEvent.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars()
    )
    items = [
        {
            "id": str(row.id),
            "rule_name": row.rule_name,
            "kind": row.kind,
            "value": row.value,
            "message": row.message,
            "delivered": row.delivered,
            "error": row.error,
            "attempts": row.attempts,
            "next_attempt_at": row.next_attempt_at,
            "delivered_at": row.delivered_at,
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


async def _bot(db: AsyncSession, workspace_id: uuid.UUID) -> TelegramBot | None:
    return await db.scalar(
        select(TelegramBot).where(TelegramBot.workspace_id == workspace_id)
    )


async def _channels(db: AsyncSession, workspace_id: uuid.UUID) -> list[AlertChannel]:
    return list(
        (
            await db.execute(
                select(AlertChannel)
                .where(AlertChannel.workspace_id == workspace_id)
                .order_by(AlertChannel.name)
            )
        ).scalars()
    )


async def _channel(
    db: AsyncSession, current: User, channel_id: uuid.UUID
) -> AlertChannel:
    channel = await db.get(AlertChannel, channel_id)
    if not channel or channel.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Канал не найден")
    return channel


async def _alert(db: AsyncSession, current: User, rule_id: uuid.UUID) -> AlertRule:
    rule = await db.get(AlertRule, rule_id)
    if not rule or rule.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return rule


async def _cap(db: AsyncSession, current: User, rule_id: uuid.UUID) -> CapRule:
    rule = await db.get(CapRule, rule_id)
    if not rule or rule.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="CAP не найден")
    # Чужая капа отвечает тем же «не найден», что и несуществующая: знать о её
    # существовании человеку тоже незачем.
    if rule.user_id and rule.user_id not in await accessible_user_ids(db, current):
        raise HTTPException(status_code=404, detail="CAP не найден")
    return rule


async def _names(db: AsyncSession, workspace_id: uuid.UUID) -> dict:
    return {
        "channels": {
            row.id: row.name for row in await _channels(db, workspace_id)
        },
        "users": {
            row.id: row.name or row.login
            for row in (
                await db.execute(select(User).where(User.workspace_id == workspace_id))
            ).scalars()
        },
        "offers": {
            row.id: row.name
            for row in (
                await db.execute(select(Offer).where(Offer.workspace_id == workspace_id))
            ).scalars()
        },
    }


async def _validate_targets(db: AsyncSession, current: User, payload) -> None:
    for channel_id in getattr(payload, "channel_ids", None) or [payload.channel_id]:
        await _channel(db, current, channel_id)
    if payload.user_id is not None:
        user = await db.get(User, payload.user_id)
        if not user or user.workspace_id != current.workspace_id:
            raise HTTPException(
                status_code=422, detail="Такого пользователя в воркспейсе нет"
            )
        if user.id not in await accessible_user_ids(db, current):
            raise HTTPException(
                status_code=422, detail="Этот пользователь вне вашей зоны видимости"
            )
    if getattr(payload, "offer_id", None) is not None:
        offer = await db.get(Offer, payload.offer_id)
        if not offer or offer.workspace_id != current.workspace_id:
            raise HTTPException(status_code=422, detail="Такого оффера в воркспейсе нет")
    wanted = set(getattr(payload, "offer_ids", None) or [])
    if wanted:
        found = await db.scalar(
            select(func.count()).select_from(Offer).where(
                Offer.workspace_id == current.workspace_id, Offer.id.in_(wanted)
            )
        )
        if (found or 0) != len(wanted):
            raise HTTPException(status_code=422, detail="Один из офферов не найден")


async def _validate_alert(db: AsyncSession, current: User, payload: AlertRuleIn) -> None:
    await _channel(db, current, payload.channel_id)


async def _validate_cap(db: AsyncSession, current: User, payload) -> None:
    await _validate_targets(db, current, payload)


def _channel_row(channel: AlertChannel) -> dict:
    return {
        "id": str(channel.id),
        "name": channel.name,
        "chat_id": channel.chat_id,
        "thread_id": channel.thread_id,
        "status": channel.status.value,
    }


def _alert_row(rule: AlertRule, names: dict) -> dict:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "status": rule.status.value,
        "kind": rule.kind,
        "kind_label": (
            "Отчёт" if rule.kind == "report" else "Уведомление по депозитам"
        ),
        "channel_id": str(rule.channel_id),
        "channel_name": names["channels"].get(rule.channel_id),
        "thread_id": rule.thread_id,
        "conditions": alerts.normalize_conditions(rule.conditions),
        "conditions_text": alerts.describe_conditions(
            alerts.normalize_conditions(rule.conditions)
        ),
        "window": rule.window,
        "window_label": WINDOWS.get(rule.window, rule.window),
        "schedule": rule.schedule,
        "schedule_label": alerts.schedule_label(rule.schedule),
        "timezone": rule.timezone,
        "message_template": rule.message_template,
        "last_fired_at": rule.last_fired_at,
    }


def _cap_row(rule: CapRule, names: dict) -> dict:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "status": rule.status.value,
        "channel_id": str(rule.channel_id),
        "channel_name": names["channels"].get(rule.channel_id),
        "channel_ids": [str(value) for value in cap_channel_ids(rule)],
        "channel_names": [
            names["channels"].get(value)
            for value in cap_channel_ids(rule)
            if names["channels"].get(value)
        ],
        "thread_id": rule.thread_id,
        "offer_ids": [str(value) for value in cap_offer_ids(rule)],
        "offer_names": [
            names["offers"].get(value)
            for value in cap_offer_ids(rule)
            if names["offers"].get(value)
        ],
        "user_id": str(rule.user_id) if rule.user_id else None,
        "user_name": names["users"].get(rule.user_id),
        "metric": rule.metric,
        "metric_label": CAP_METRICS.get(rule.metric),
        "metric_hint": CAP_METRIC_HINTS.get(rule.metric),
        "limit_value": rule.limit_value,
        "period": rule.period,
        "timezone": rule.timezone,
        "notify_at": rule.notify_at or [],
        "notified_percent": rule.notified_percent,
        "last_fired_at": rule.last_fired_at,
    }
