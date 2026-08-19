"""Meta Ads — весь модуль ТЗ 3.2–3.8.

Чтение (кабинеты, статистика) живёт на праве `meta.view`, настройка подключений —
на `meta.manage`, а всё, что тратит деньги в кабинете, — на `meta.launch`.
Разделение не бюрократия: доступ к токену и право запускать заливы в команде
обычно у разных людей.

Расход, показы и клики приходят из Meta; лиды, продажи и доход — из Keitaro, по
ID кампании Meta, который баер кладёт в sub_id ссылки. Без этого sub_id расход
есть, а ROI посчитать не из чего — так и показываем, вместо того чтобы выдавать
пустой доход за ноль.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.analytics import invalidate_dashboard_cache
from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.deps import (
    accessible_user_ids,
    has_full_access,
    has_permission,
    require_any_permission,
    require_permission,
)
from app.core.security import decrypt_secret, encrypt_secret
from app.models import (
    IntegrationConnection,
    LaunchStatus,
    MediaRecord,
    MediaSpendValue,
    MetaAdAccount,
    MetaCreative,
    MetaEntity,
    MetaLaunch,
    MetaLaunchCreative,
    MetaOperation,
    MetaRule,
    MetaRuleEvent,
    MetaSpendCommit,
    MetaStatDaily,
    MetaTemplate,
    Offer,
    Partner,
    SpendProvider,
    Status,
    SyncRun,
    SyncStatus,
    User,
)
from app.schemas import (
    MetaAccountUpdate,
    MetaBundleSettings,
    MetaConnectionCreate,
    MetaConnectionOut,
    MetaConnectionPreview,
    MetaConnectionUpdate,
    MetaLaunchBatch,
    MetaLaunchCreate,
    MetaLaunchUpdate,
    MetaProxyCheck,
    MetaRuleCreate,
    MetaRuleUpdate,
    MetaSessionAttach,
    MetaSessionStart,
    MetaSpendCommitIn,
    MetaTemplateCreate,
    MetaTemplateUpdate,
    Page,
)
from app.services import meta_bundle, meta_hourly, meta_levels, meta_spend
from app.services.audit import audit
from app.services.formulas import amount_with_commission, q
from app.services.meta import (
    AUTH_METHOD_HINTS,
    AUTH_METHODS,
    BID_STRATEGIES,
    BILLING_EVENTS,
    CALL_TO_ACTIONS,
    CUSTOM_EVENT_TYPES,
    IMAGE_MIME_TYPES,
    MAX_UPLOAD_BYTES,
    OBJECTIVES,
    OPTIMIZATION_GOALS,
    PUBLISHER_PLATFORMS,
    VIDEO_MIME_TYPES,
    MetaClient,
    MetaError,
    account_status_label,
    graph_base_url,
    money_from_minor,
    uniquify,
)
from app.services.meta_launch import (
    LaunchValidationError,
    load_launch_context,
    validate_launch,
)
from app.services.meta_metrics import (
    METRIC_LABELS,
    ZERO,
    keitaro_by_campaign,
    metrics,
    totals,
)
from app.services.meta_rules import ACTIONS as RULE_ACTIONS
from app.services.meta_rules import (
    ENTITY_STATUSES,
    FREQUENCIES,
    OPERATORS,
    MetaRuleEngine,
    collect_candidates,
    matches,
)
from app.services.meta_rules import LEVELS as LEVEL_LABELS
from app.services.meta_rules import WINDOWS as RULE_WINDOWS
from app.services.meta_session import MetaSessionError, check_proxy_url, get_session_manager
from app.workers.tasks import publish_meta_launch, sync_meta_connection

router = APIRouter(prefix="/meta", tags=["meta"])

logger = logging.getLogger("meta_router")

MAX_PERIOD_DAYS = 186


def q2(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


@router.get("/connections", response_model=Page)
async def list_connections(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> Page:
    rows = await _connections(db, current.workspace_id)
    items = [
        {
            **MetaConnectionOut.model_validate(row).model_dump(mode="json"),
            "last_run": _run_payload(latest),
        }
        for row, latest in await _with_latest_runs(db, rows)
    ]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.post("/connections/preview")
async def preview_connection(
    payload: MetaConnectionPreview,
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    """Показать кабинеты, которые видны через токен, ничего не сохраняя.

    Третий шаг мастера подключения. Без него список кабинетов появлялся бы только
    после сохранения и первой синхронизации — то есть выбирать было бы уже поздно.
    """
    accounts = await _verify_token(
        payload.access_token,
        payload.business_id,
        proxy=payload.proxy_url,
        user_agent=payload.user_agent,
    )
    return {"accounts": [_preview_row(row) for row in accounts]}


@router.post("/connections", response_model=MetaConnectionOut, status_code=201)
async def create_connection(
    payload: MetaConnectionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.manage")),
) -> IntegrationConnection:
    _require_proxy_for_session(payload.auth_method, payload.proxy_url)
    accounts = await _verify_token(
        payload.access_token,
        payload.business_id,
        proxy=payload.proxy_url,
        user_agent=payload.user_agent,
    )
    duplicate = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == current.workspace_id,
            IntegrationConnection.name == payload.name,
        )
    )
    if duplicate:
        raise HTTPException(status_code=422, detail="Подключение с таким названием уже есть")
    connection = IntegrationConnection(
        workspace_id=current.workspace_id,
        name=payload.name,
        kind="meta",
        # base_url у Meta один на всех и живёт в настройках приложения, но колонка
        # общая с Keitaro и NOT NULL — пишем туда фактический адрес Graph API.
        base_url=graph_base_url(),
        api_key_encrypted=encrypt_secret(payload.access_token),
        sync_interval_minutes=payload.sync_interval_minutes,
        lookback_days=payload.lookback_days,
        external_account_id=payload.business_id,
        attribution_sub_id=payload.attribution_sub_id,
        auth_method=payload.auth_method,
        proxy_url=payload.proxy_url,
        user_agent=payload.user_agent,
    )
    db.add(connection)
    await db.flush()
    imported = _seed_accounts(db, connection, accounts, payload.import_accounts)
    await audit(
        db,
        current,
        "meta.connection_created",
        f"Подключён Meta Ads: {connection.name} ({AUTH_METHODS[payload.auth_method]}), "
        f"импортировано кабинетов: {imported}",
        request=request,
    )
    await db.commit()
    await db.refresh(connection)
    return connection


@router.patch("/connections/{connection_id}", response_model=MetaConnectionOut)
async def update_connection(
    connection_id: uuid.UUID,
    payload: MetaConnectionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.manage")),
) -> IntegrationConnection:
    connection = await _connection(db, current, connection_id)
    changes = payload.model_dump(exclude_unset=True)
    access_token = changes.pop("access_token", None)
    if "business_id" in changes:
        connection.external_account_id = changes.pop("business_id")
    for field, value in changes.items():
        setattr(connection, field, value)
    # Токен сессии (EAAB) живёт только за своим прокси: без него Meta видит
    # «смену IP» и отзывает сессию, а при повторах банит аккаунт.
    _require_proxy_for_session(connection.auth_method, connection.proxy_url)
    if access_token:
        await _verify_token(
            access_token,
            connection.external_account_id,
            proxy=connection.proxy_url,
            user_agent=connection.user_agent,
        )
        connection.api_key_encrypted = encrypt_secret(access_token)
    await audit(
        db,
        current,
        "meta.connection_updated",
        f"Изменено подключение Meta Ads: {connection.name}",
        request=request,
        entity_id=str(connection.id),
    )
    await db.commit()
    await db.refresh(connection)
    return connection


@router.delete("/connections/{connection_id}", status_code=200)
async def delete_connection(
    connection_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    connection = await _connection(db, current, connection_id)
    name = connection.name
    # Кабинеты, объекты и статистика уходят каскадом. Ручных данных на них нет —
    # всё это копия того, что в любой момент можно вычитать из Meta заново.
    await db.delete(connection)
    await audit(
        db,
        current,
        "meta.connection_deleted",
        f"Удалено подключение Meta Ads: {name}",
        request=request,
        entity_id=str(connection_id),
    )
    await db.commit()
    return {"deleted": str(connection_id)}


@router.post("/connections/{connection_id}/check")
async def check_connection(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    """Что этим токеном реально доступно.

    Проверяем пробными вызовами, а не `debug_token`: у токена из сессии
    аккаунта приложение чужое, его секрета у нас нет, и посмотреть права
    официальным способом невозможно. Запрос, который прошёл, — единственное
    надёжное доказательство доступа.
    """
    connection = await _connection(db, current, connection_id)
    token = decrypt_secret(connection.api_key_encrypted)
    accounts = await _verify_token(
        token,
        connection.external_account_id,
        proxy=connection.proxy_url,
        user_agent=connection.user_agent,
    )
    client = client_for(connection)

    async def probe(action) -> dict:
        try:
            return {"ok": True, "count": len(await action())}
        except MetaError as exc:
            return {"ok": False, "error": str(exc)}

    checks = {
        "accounts": {"ok": True, "count": len(accounts)},
        "businesses": await probe(client.businesses),
        "pages": await probe(client.pages),
    }
    external_id = str(accounts[0].get("id") or "")
    checks["entities"] = await probe(lambda: client.entities(external_id, "campaign"))
    checks["pixels"] = await probe(lambda: client.pixels(external_id))
    return {
        "ok": True,
        "accounts": len(accounts),
        "auth_method": connection.auth_method,
        "auth_method_label": AUTH_METHODS.get(connection.auth_method, connection.auth_method),
        "auth_method_hint": AUTH_METHOD_HINTS.get(connection.auth_method, ""),
        "checks": checks,
    }


# --- Браузерные сессии для токена EAAB ---------------------------------------
# Токен сессии получают не вставкой готовой строки, а из живого браузера с
# cookies и прокси аккаунта: так Meta не видит «смену IP», и токен с аккаунтом
# не банятся. Браузер всегда headful; при капче/чекпойнте оператор входит
# вручную через VNC (порт 5900), а после входа сессия сохраняется на диск и
# EAAB извлекается автоматически.


@router.post("/session/start")
async def session_start(
    payload: MetaSessionStart,
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    """Запускает браузер Facebook с cookies и прокси аккаунта.

    Без работающего прокси браузер не стартует — это защита от бана: cookies,
    показанные чужому IP, помечают сессию как угнанную. Если cookies валидны,
    вход происходит сразу; иначе — ручной вход через VNC, и после него сессия
    автосохраняется, а EAAB-токен извлекается автоматически.
    """
    if not settings.meta_session_enabled:
        raise HTTPException(status_code=503, detail="Браузерные сессии отключены")
    manager = get_session_manager()
    try:
        state = await asyncio.wait_for(
            manager.start(
                session_id=payload.connection_id or None,
                cookies=payload.cookies,
                proxy_url=payload.proxy_url,
                user_agent=payload.user_agent,
            ),
            timeout=120,
        )
    except MetaSessionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Timed out while launching browser") from exc
    except Exception as exc:
        logger.exception("meta session start failed")
        raise HTTPException(status_code=500, detail=f"Failed to start session: {exc}") from exc
    return {
        "session_id": state.id,
        "status": state.status,
        "error": state.error,
        "message": (
            "Cookies приняты — проверяем авторизацию."
            if payload.cookies.strip()
            else "Браузер запущен. Выполните вход вручную (VNC, порт 5900) — "
            "сессия сохранится, токен извлечётся автоматически."
        ),
    }


@router.get("/session/{session_id}/status")
async def session_status(
    session_id: str,
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    state = get_session_manager().get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Сессия браузера не найдена")
    return state.to_dict()


@router.post("/session/{session_id}/token")
async def session_token(
    session_id: str,
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    """Извлекает EAAB-токен из живой браузерной сессии."""
    manager = get_session_manager()
    try:
        result = await manager.extract_token(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("meta session token extraction failed")
        raise HTTPException(status_code=500, detail=f"Token extraction failed: {exc}") from exc
    if not result.get("token"):
        state = manager.get(session_id)
        fb_hint = None
        if state:
            url = state.page.url if state.page else ""
            if "checkpoint" in url or "captcha" in url:
                fb_hint = "Facebook показывает капчу — требуется ручное вмешательство (VNC)."
            elif "blocked" in url or "login" in url:
                fb_hint = "Аккаунт забанен или сессия протухла — требуется повторный вход."
        detail = (
            f"EAAB token not found. Попробованы методы: "
            f"{', '.join(result.get('methods_tried', [])) or '—'}."
        )
        if fb_hint:
            detail = f"{detail} {fb_hint}"
        raise HTTPException(status_code=422, detail=detail)
    return result


@router.post("/session/{session_id}/attach")
async def session_attach(
    session_id: str,
    payload: MetaSessionAttach,
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    """Привязывает сохранённую сессию мастера к подключению Meta.

    После этого синхронизация сможет при смерти токена восстановить браузерную
    сессию, извлечь новый EAAB и продолжить работу без участия человека.
    """
    manager = get_session_manager()
    try:
        return await manager.attach(session_id, payload.connection_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/session/{session_id}/close")
async def session_close(
    session_id: str,
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    await get_session_manager().close(session_id)
    return {"session_id": session_id, "status": "closed"}


@router.get("/sessions")
async def sessions_list(current: User = Depends(require_permission("meta.manage"))) -> dict:
    return {"sessions": get_session_manager().list_sessions()}


@router.post("/proxy/check")
async def proxy_check(payload: MetaProxyCheck) -> dict:
    """Проверка прокси: внешний IP и задержка через него.

    Тот же маршрут, которым позже пойдёт браузер и все запросы подключения, —
    если проверка не прошла, браузер не запустится, а аккаунт не пострадает.
    """
    result = await check_proxy_url(payload.proxy_url)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Proxy check failed"))
    return result


def _require_proxy_for_session(auth_method: str, proxy_url: str | None) -> None:
    """Токен сессии (EAAB) работает только через прокси — иначе бан аккаунта."""
    if auth_method == "session" and not (proxy_url or "").strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "Токен сессии (EAAB) требует прокси: без него Meta видит смену IP "
                "и отзывает сессию, а при повторах банит аккаунт. Укажите прокси, "
                "с которого заходите в Ads Manager."
            ),
        )


@router.post("/connections/{connection_id}/sync", status_code=202)
async def start_sync(
    connection_id: uuid.UUID,
    request: Request,
    mode: str = "incremental",
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    if mode not in {"incremental", "backfill"}:
        raise HTTPException(status_code=422, detail="Mode must be incremental or backfill")
    connection = await _connection(db, current, connection_id)
    running = await db.scalar(
        select(SyncRun).where(
            SyncRun.connection_id == connection.id,
            SyncRun.status.in_([SyncStatus.queued, SyncStatus.running]),
        )
    )
    if running:
        return {"run_id": str(running.id), "status": running.status.value}
    run = SyncRun(
        connection_id=connection.id,
        mode=mode,
        status=SyncStatus.queued,
        details={"phase": "queued", "source": "manual"},
    )
    db.add(run)
    await db.flush()
    response = {"run_id": str(run.id), "status": run.status.value}
    await audit(
        db,
        current,
        "meta.sync_started",
        f"Запущена синхронизация Meta Ads ({mode}) для {connection.name}",
        request=request,
        entity_id=str(connection.id),
    )
    await db.commit()
    sync_meta_connection.delay(str(connection.id), str(run.id), mode)
    return response


@router.get("/connections/{connection_id}/runs", response_model=Page)
async def list_runs(
    connection_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> Page:
    connection = await _connection(db, current, connection_id)
    total = await db.scalar(
        select(func.count()).select_from(SyncRun).where(SyncRun.connection_id == connection.id)
    )
    rows = list(
        (
            await db.execute(
                select(SyncRun)
                .where(SyncRun.connection_id == connection.id)
                .order_by(SyncRun.started_at.desc())
                .limit(min(limit, 100))
                .offset(offset)
            )
        ).scalars()
    )
    return Page(
        items=[_run_payload(row) for row in rows],
        total=total or 0,
        limit=min(limit, 100),
        offset=offset,
    )


@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: uuid.UUID,
    payload: MetaAccountUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.manage")),
) -> dict:
    account = await db.get(MetaAdAccount, account_id)
    if not account or account.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Кабинет не найден")
    changes = payload.model_dump(exclude_unset=True)
    if "owner_id" in changes:
        owner_id = changes["owner_id"]
        if owner_id is not None:
            owner = await db.get(User, owner_id)
            if not owner or owner.workspace_id != current.workspace_id:
                raise HTTPException(status_code=422, detail="Такого пользователя нет")
        account.owner_id = owner_id
    if "status" in changes and changes["status"] is not None:
        account.status = changes["status"]
    await audit(
        db,
        current,
        "meta.account_updated",
        f"Изменён кабинет Meta {account.name}",
        request=request,
        entity_id=str(account.id),
    )
    await db.commit()
    return {"id": str(account.id), "owner_id": str(account.owner_id) if account.owner_id else None}


@router.get("/overview")
async def overview(
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> dict:
    start, end = _period(date_from, date_to)
    connections = await _connections(db, current.workspace_id)
    accounts = await _visible_accounts(db, current, account_id, owner_id)
    account_ids = [account.id for account in accounts]

    stats: list[MetaStatDaily] = []
    if account_ids:
        stats = list(
            (
                await db.execute(
                    select(MetaStatDaily).where(
                        MetaStatDaily.account_id.in_(account_ids),
                        MetaStatDaily.record_date >= start,
                        MetaStatDaily.record_date <= end,
                    )
                )
            ).scalars()
        )

    sub_id = next(
        (
            connection.attribution_sub_id
            for connection in connections
            if connection.attribution_sub_id
        ),
        None,
    )
    keitaro = await keitaro_by_campaign(db, current.workspace_id, start, end, sub_id)

    owners = await _owner_names(db, accounts)
    campaigns = await _campaign_rows(db, accounts, stats, keitaro)
    account_rows = _account_rows(accounts, stats, keitaro, owners)

    return {
        "configured": bool(connections),
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "attribution": {
            "sub_id": sub_id,
            "matched_campaigns": sum(1 for row in campaigns if row["revenue"] is not None),
            "total_campaigns": len(campaigns),
        },
        "currencies": sorted({account.currency for account in accounts}),
        "totals": totals(stats, keitaro),
        "accounts": account_rows,
        "campaigns": campaigns,
    }


@router.get("/overview/levels/{level}")
async def overview_level(
    level: str,
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> dict:
    """Один срез обзора: от людей до объявлений.

    Данные одни и те же — меняется только ключ группировки. Разносить это по
    восьми ручкам смысла нет: они читали бы одну и ту же выборку.
    """
    if level not in meta_levels.LEVELS:
        raise HTTPException(status_code=404, detail="Unknown overview level")
    start, end = _period(date_from, date_to)
    connections = await _connections(db, current.workspace_id)
    accounts = await _visible_accounts(db, current, account_id, owner_id)
    account_ids = [account.id for account in accounts]

    stats: list[MetaStatDaily] = []
    if account_ids:
        stats = list(
            (
                await db.execute(
                    select(MetaStatDaily).where(
                        MetaStatDaily.account_id.in_(account_ids),
                        MetaStatDaily.record_date >= start,
                        MetaStatDaily.record_date <= end,
                    )
                )
            ).scalars()
        )
    sub_id = next(
        (
            connection.attribution_sub_id
            for connection in connections
            if connection.attribution_sub_id
        ),
        None,
    )
    keitaro = await keitaro_by_campaign(db, current.workspace_id, start, end, sub_id)
    graph = await meta_levels.load_graph(db, current.workspace_id, account_ids)
    rows = meta_levels.rows_for(level, stats, graph, keitaro, search=search)
    rows = meta_levels.merge(
        rows, meta_levels.empty_rows(level, graph), search=search
    )
    return {
        "level": level,
        "label": meta_levels.LEVEL_LABELS[level],
        "configured": bool(connections),
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "has_revenue": level in meta_levels.REVENUE_LEVELS and bool(sub_id),
        "currencies": sorted({account.currency for account in accounts}),
        "rows": rows,
        "totals": totals(stats, keitaro),
    }


@router.get("/insights/hourly")
async def hourly_insights(
    external_id: str,
    level: str = "ads",
    date_from: date | None = None,
    date_to: date | None = None,
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> dict:
    """Расход по часам для одной кампании, адсета или объявления.

    Ходим в Meta по требованию и кешируем ответ на несколько минут: почасовую
    статистику смотрят точечно, и хранить её копию целиком незачем.
    """
    entity_level = {"campaigns": "campaign", "adsets": "adset", "ads": "ad"}.get(level)
    if not entity_level:
        raise HTTPException(status_code=422, detail="Уровень должен быть campaigns, adsets или ads")
    start, end = _period(date_from, date_to)
    if (end - start).days > 30:
        raise HTTPException(
            status_code=422,
            detail="По часам период не длиннее 31 дня — иначе Meta отдаёт его частями "
            "дольше, чем человек готов ждать окно.",
        )

    # Объект должен принадлежать кабинету, который этот пользователь видит,
    # иначе по чужому ID можно было бы вытащить чужой расход.
    accounts = {
        account.id: account for account in await _visible_accounts(db, current, None, None)
    }
    entity = await db.scalar(
        select(MetaEntity).where(
            MetaEntity.workspace_id == current.workspace_id,
            MetaEntity.level == entity_level,
            MetaEntity.external_id == external_id,
        )
    )
    if not entity or entity.account_id not in accounts:
        raise HTTPException(status_code=404, detail="Объект не найден")
    account = accounts[entity.account_id]

    key = meta_hourly.cache_key(current.workspace_id, external_id, start, end)
    redis = None
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        cached = None if refresh else await redis.get(key)
        if cached:
            await redis.aclose()
            return json.loads(cached)
    except Exception:
        redis = None

    connection = await db.get(IntegrationConnection, account.connection_id)
    if not connection:
        raise HTTPException(status_code=422, detail="У кабинета нет подключения Meta")
    client = client_for(connection)
    try:
        hours = await meta_hourly.load(client, external_id, start, end)
    except MetaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    payload = {
        "external_id": external_id,
        "level": level,
        "name": entity.name,
        "account_name": account.name,
        "currency": account.currency,
        # Часы считает Meta и только по таймзоне кабинета — говорим об этом прямо.
        "timezone": account.timezone_name,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "days": (end - start).days + 1,
        "hours": hours,
        "totals": meta_hourly.summarize(hours),
    }
    if redis:
        try:
            await redis.setex(key, meta_hourly.CACHE_TTL_SECONDS, meta_hourly.encode(payload))
            await redis.aclose()
        except Exception:
            pass
    return payload


async def _campaign_hours(
    db: AsyncSession,
    current: User,
    accounts: dict[uuid.UUID, MetaAdAccount],
    entity: MetaEntity,
    day: date,
    refresh: bool,
) -> list[dict]:
    """Почасовой расход одной кампании за день, через тот же кеш, что и окно.

    Ключ кеша общий с «расходом по часам» на карточке: человек обычно сначала
    смотрит часы, потом фиксирует, и второй раз спрашивать Meta незачем.
    """
    key = meta_hourly.cache_key(current.workspace_id, entity.external_id, day, day)
    redis = None
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        cached = None if refresh else await redis.get(key)
        if cached:
            await redis.aclose()
            return json.loads(cached).get("hours") or meta_hourly.empty_hours()
    except Exception:
        redis = None

    account = accounts[entity.account_id]
    client, _ = await _account_client(db, account)
    try:
        hours = await meta_hourly.load(client, entity.external_id, day, day)
    except MetaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if redis:
        try:
            payload = {
                "external_id": entity.external_id,
                "level": "campaigns",
                "name": entity.name,
                "account_name": account.name,
                "currency": account.currency,
                "timezone": account.timezone_name,
                "period": {"from": day.isoformat(), "to": day.isoformat()},
                "days": 1,
                "hours": hours,
                "totals": meta_hourly.summarize(hours),
            }
            await redis.setex(key, meta_hourly.CACHE_TTL_SECONDS, meta_hourly.encode(payload))
            await redis.aclose()
        except Exception:
            pass
    return hours


@router.get("/spend/window")
async def spend_window(
    day: date,
    hour_from: int = 0,
    hour_to: int = 24,
    account_id: uuid.UUID | None = None,
    campaign_ids: list[str] | None = Query(default=None),
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> dict:
    """Кампании, которые крутились в этот отрезок дня, и их расход за него.

    Дневная статистика такого не умеет — она знает только сумму за сутки.
    Поэтому по каждой кампании, у которой в этот день вообще был расход, берётся
    почасовая разбивка. Кампании без расхода за день не спрашиваем: это лишний
    поход в Meta ради заведомого нуля.

    `campaign_ids` — те кампании, которые отметили в таблице. Их и только их
    спрашиваем по часам: без списка каждое открытие формы дёргало Meta по всем
    кампаниям дня, хотя относят на оффер две-три. Отмеченная кампания попадает
    в ответ даже с нулём за день — иначе выбранная строка молча пропала бы.
    """
    if not 0 <= hour_from < hour_to <= 24:
        raise HTTPException(
            status_code=422,
            detail="Окно задаётся часами от 0 до 24, конец позже начала",
        )
    accounts = {
        account.id: account
        for account in await _visible_accounts(db, current, account_id, None)
    }
    if not accounts:
        return {
            "day": day.isoformat(),
            "window": {"from": hour_from, "to": hour_to},
            "rows": [],
            "total": 0.0,
            "timezones": [],
        }

    daily_stmt = (
        select(
            MetaStatDaily.campaign_external_id,
            MetaStatDaily.account_id,
            func.sum(MetaStatDaily.spend),
        )
        .where(
            MetaStatDaily.account_id.in_(list(accounts)),
            MetaStatDaily.record_date == day,
            MetaStatDaily.campaign_external_id.is_not(None),
        )
        .group_by(MetaStatDaily.campaign_external_id, MetaStatDaily.account_id)
    )
    if campaign_ids:
        daily_stmt = daily_stmt.where(MetaStatDaily.campaign_external_id.in_(campaign_ids))
    daily = list((await db.execute(daily_stmt)).all())
    wanted = campaign_ids if campaign_ids else [item[0] for item in daily]
    if not wanted:
        return {
            "day": day.isoformat(),
            "window": {"from": hour_from, "to": hour_to},
            "rows": [],
            "total": 0.0,
            "timezones": sorted({account.timezone_name for account in accounts.values()}),
        }

    entities = {
        row.external_id: row
        for row in (
            await db.execute(
                select(MetaEntity).where(
                    MetaEntity.workspace_id == current.workspace_id,
                    MetaEntity.level == "campaign",
                    MetaEntity.external_id.in_(wanted),
                )
            )
        ).scalars()
    }
    commits = await meta_spend.commits_for_day(db, current.workspace_id, day)
    day_spend_by_campaign = {item[0]: item[2] for item in daily}

    rows = []
    total = Decimal("0")
    for campaign_id in wanted:
        entity = entities.get(campaign_id)
        if not entity or entity.account_id not in accounts:
            continue
        day_spend = day_spend_by_campaign.get(campaign_id)
        # За день ноль — часы спрашивать не у чего: в окне тоже будет ноль.
        hours = (
            await _campaign_hours(db, current, accounts, entity, day, refresh)
            if day_spend
            else []
        )
        amount = meta_spend.window_spend(hours, hour_from, hour_to)
        total += amount
        account = accounts[entity.account_id]
        rows.append(
            {
                "campaign_id": campaign_id,
                "name": entity.name or campaign_id,
                "account_name": account.name,
                "currency": account.currency,
                "timezone": account.timezone_name,
                "status": entity.effective_status,
                "day_spend": float(Decimal(str(day_spend or 0)).quantize(Decimal("0.01"))),
                "spend": float(amount),
                # Часы, уже отнесённые на какой-то оффер: без них про занятое
                # окно узнаёшь только из отказа при сохранении.
                "taken_hours": meta_spend.taken_hours(commits, campaign_id),
            }
        )
    rows.sort(key=lambda row: (-row["spend"], row["name"]))
    return {
        "day": day.isoformat(),
        "window": {"from": hour_from, "to": hour_to},
        "rows": rows,
        "total": float(total),
        "timezones": sorted({row["timezone"] for row in rows if row["timezone"]}),
    }


@router.get("/spend/commits")
async def spend_commits(
    day: date,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> dict:
    """Что уже зафиксировано за этот день."""
    commits = await meta_spend.commits_for_day(db, current.workspace_id, day)
    if not commits:
        return {"items": []}
    offers = dict(
        (
            await db.execute(
                select(Offer.id, Offer.name).where(
                    Offer.id.in_({commit.offer_id for commit in commits})
                )
            )
        ).all()
    )
    people = dict(
        (
            await db.execute(
                select(User.id, User.name).where(
                    User.id.in_({commit.buyer_id for commit in commits})
                )
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": str(commit.id),
                "campaign_id": commit.campaign_external_id,
                "campaign_name": commit.campaign_name,
                "hour_from": commit.hour_from,
                "hour_to": commit.hour_to,
                "window": meta_spend.describe_window(commit.hour_from, commit.hour_to),
                "offer": offers.get(commit.offer_id),
                "buyer": people.get(commit.buyer_id),
                "base_amount": commit.base_amount,
                "created_at": commit.created_at,
            }
            for commit in commits
        ]
    }


@router.post("/spend/commit", status_code=201)
async def commit_spend(
    payload: MetaSpendCommitIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_any_permission("meta.launch", "media.manage")),
) -> dict:
    """Отнести расход выбранных кампаний за отрезок дня на оффер.

    Пишется одна запись Медиаборда на связку «день + баер + оффер», а расход
    ложится на выбранного агента — процент агента считает та же формула, что и
    при ручном вводе, иначе два способа завести расход давали бы разные числа.

    Право — `media.manage` или `meta.launch`. Фиксацию делает баер со своего
    аккаунта, а `meta.launch` (заливы, автоправила, деньги в кабинете) у него не
    обязано быть. Ничего нового `media.manage` не открывает: этот же расход он и
    так заводит в Медиаборде руками, здесь только считает его Meta.

    Баер фиксирует расход на себя — иначе любой из них мог бы записать свой
    расход на чужой день. Отнести на другого человека может тот, кто и так ведёт
    чужие кабинеты (`meta.manage`).
    """
    if payload.buyer_id != current.id and not has_permission(current, "meta.manage"):
        raise HTTPException(status_code=403, detail="Расход фиксируется на себя")
    window = (payload.hour_from, payload.hour_to)
    accounts = {
        account.id: account
        for account in await _visible_accounts(db, current, None, None)
    }
    entities = {
        row.external_id: row
        for row in (
            await db.execute(
                select(MetaEntity).where(
                    MetaEntity.workspace_id == current.workspace_id,
                    MetaEntity.level == "campaign",
                    MetaEntity.external_id.in_(payload.campaign_ids),
                )
            )
        ).scalars()
    }
    missing = [
        campaign_id
        for campaign_id in payload.campaign_ids
        if campaign_id not in entities or entities[campaign_id].account_id not in accounts
    ]
    if missing:
        raise HTTPException(status_code=404, detail="Кампания не найдена: " + missing[0])

    offer = await db.get(Offer, payload.offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=422, detail="Такого оффера в воркспейсе нет")
    buyer = await db.get(User, payload.buyer_id)
    if not buyer or buyer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=422, detail="Такого пользователя в воркспейсе нет")
    provider = await db.get(SpendProvider, payload.provider_id)
    if not provider or provider.workspace_id != current.workspace_id:
        raise HTTPException(status_code=422, detail="Такого агента в воркспейсе нет")

    commits = await meta_spend.commits_for_day(db, current.workspace_id, payload.record_date)
    for campaign_id in payload.campaign_ids:
        clash = meta_spend.conflict(commits, campaign_id, window)
        if clash:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"«{entities[campaign_id].name or campaign_id}» уже отнесена на "
                    f"{meta_spend.describe_window(clash.hour_from, clash.hour_to)} — "
                    "снимите прежнюю привязку или выберите другое окно"
                ),
            )

    record = await db.scalar(
        select(MediaRecord).where(
            MediaRecord.workspace_id == current.workspace_id,
            MediaRecord.record_date == payload.record_date,
            MediaRecord.buyer_id == payload.buyer_id,
            MediaRecord.offer_id == payload.offer_id,
        )
    )
    if not record:
        record = MediaRecord(
            workspace_id=current.workspace_id,
            record_date=payload.record_date,
            buyer_id=payload.buyer_id,
            offer_id=payload.offer_id,
            source="meta",
        )
        db.add(record)
        await db.flush()

    added = Decimal("0")
    for campaign_id in payload.campaign_ids:
        entity = entities[campaign_id]
        hours = await _campaign_hours(
            db, current, accounts, entity, payload.record_date, False
        )
        amount = meta_spend.window_spend(hours, payload.hour_from, payload.hour_to)
        added += amount
        db.add(
            MetaSpendCommit(
                workspace_id=current.workspace_id,
                record_date=payload.record_date,
                hour_from=payload.hour_from,
                hour_to=payload.hour_to,
                campaign_external_id=campaign_id,
                campaign_name=entity.name,
                account_id=entity.account_id,
                media_record_id=record.id,
                provider_id=provider.id,
                buyer_id=payload.buyer_id,
                offer_id=payload.offer_id,
                base_amount=amount,
                created_by_id=current.id,
            )
        )

    total = await _apply_provider_spend(db, record, provider, added)
    await audit(
        db,
        current,
        "meta.spend_committed",
        f"Расход {meta_spend.describe_window(*window)} отнесён на «{offer.name}»",
        request=request,
        entity_type="media_record",
        entity_id=str(record.id),
    )
    await db.commit()
    await invalidate_dashboard_cache(current.workspace_id)
    return {
        "media_record_id": str(record.id),
        "base_amount": float(added),
        "spend": float(total),
        "commission_pct": float(provider.commission_pct),
    }


@router.delete("/spend/commits/{commit_id}")
async def delete_commit(
    commit_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_any_permission("meta.launch", "media.manage")),
) -> dict:
    """Снять привязку и вычесть её расход обратно.

    Без этого ошибочную фиксацию нельзя было бы отменить — только вычитать
    руками из чужой записи Медиаборда.
    """
    commit = await db.get(MetaSpendCommit, commit_id)
    if not commit or commit.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Привязка не найдена")
    record = await db.get(MediaRecord, commit.media_record_id)
    provider = await db.get(SpendProvider, commit.provider_id)
    if record and provider:
        await _apply_provider_spend(db, record, provider, -commit.base_amount)
    await db.delete(commit)
    await audit(
        db, current, "meta.spend_commit_removed",
        f"Снята привязка расхода {meta_spend.describe_window(commit.hour_from, commit.hour_to)}",
        request=request, entity_id=str(commit_id),
    )
    await db.commit()
    await invalidate_dashboard_cache(current.workspace_id)
    return {"deleted": str(commit_id)}


async def _apply_provider_spend(
    db: AsyncSession, record: MediaRecord, provider: SpendProvider, delta: Decimal
) -> Decimal:
    """Прибавить (или вычесть) расход агента и пересчитать итог записи.

    Прибавляем, а не перезаписываем: за день на один оффер фиксируют несколько
    окон, и второе не должно стирать первое.
    """
    value = await db.scalar(
        select(MediaSpendValue).where(
            MediaSpendValue.media_record_id == record.id,
            MediaSpendValue.provider_id == provider.id,
        )
    )
    if value is None:
        value = MediaSpendValue(
            media_record_id=record.id, provider_id=provider.id, base_amount=Decimal("0")
        )
        db.add(value)
    value.base_amount = max(Decimal("0"), (value.base_amount or Decimal("0")) + delta)
    await db.flush()

    values = list(
        (
            await db.execute(
                select(MediaSpendValue).where(
                    MediaSpendValue.media_record_id == record.id
                )
            )
        ).scalars()
    )
    providers = {
        row.id: row
        for row in (
            await db.execute(
                select(SpendProvider).where(
                    SpendProvider.id.in_([item.provider_id for item in values])
                )
            )
        ).scalars()
    }
    total = Decimal("0")
    for item in values:
        owner = providers.get(item.provider_id)
        total += (
            item.manual_amount_override
            if item.manual_amount_override is not None
            else amount_with_commission(
                item.base_amount, owner.commission_pct if owner else Decimal("0")
            )
        )
    record.spend_calculated = q(total)
    return record.spend_calculated


@router.get("/reference")
async def reference(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> dict:
    """Справочники для форм: что Meta принимает и что есть в этом воркспейсе."""
    accounts = await _visible_accounts(db, current, None, None)
    offers = list(
        (
            await db.execute(
                select(Offer)
                .where(Offer.workspace_id == current.workspace_id)
                .order_by(Offer.name)
                .limit(500)
            )
        ).scalars()
    )
    partners = list(
        (
            await db.execute(
                select(Partner)
                .where(Partner.workspace_id == current.workspace_id)
                .order_by(Partner.name)
            )
        ).scalars()
    )
    return {
        "objectives": OBJECTIVES,
        "optimization_goals": OPTIMIZATION_GOALS,
        "billing_events": BILLING_EVENTS,
        "bid_strategies": BID_STRATEGIES,
        "call_to_actions": CALL_TO_ACTIONS,
        "publisher_platforms": PUBLISHER_PLATFORMS,
        "custom_event_types": CUSTOM_EVENT_TYPES,
        "metrics": METRIC_LABELS,
        "rule_actions": RULE_ACTIONS,
        "rule_levels": LEVEL_LABELS,
        "rule_statuses": ENTITY_STATUSES,
        "rule_windows": RULE_WINDOWS,
        "rule_operators": OPERATORS,
        "rule_frequencies": {str(key): value for key, value in FREQUENCIES.items()},
        # Справочники связки — цели, окна атрибуции, макросы. Отдельной ручкой их
        # заводить незачем: формы всё равно грузят этот справочник целиком.
        "bundle": meta_bundle.reference(),
        "launch_statuses": [status.value for status in LaunchStatus],
        "accounts": [
            {
                "id": str(account.id),
                "name": account.name,
                "external_id": account.external_id,
                "currency": account.currency,
                "status": account.status.value,
            }
            for account in accounts
        ],
        "offers": [
            {"id": str(offer.id), "name": offer.name, "geo": offer.geo} for offer in offers
        ],
        "partners": [{"id": str(row.id), "name": row.name} for row in partners],
    }


@router.get("/targeting")
async def targeting(
    kind: Literal["interest", "locale"],
    q: str = "",
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> dict:
    """Поиск по справочникам таргетинга Meta — интересы и языки.

    Своего справочника здесь быть не может: интересы Meta заводит и
    переименовывает сама, а её ID — единственное, что она принимает в
    `flexible_spec`. Ходим её же токеном: другого доступа к этим спискам нет.
    """
    query = q.strip()
    if len(query) < 2:
        # Meta на пустой запрос отдаёт случайную выборку — показывать её как
        # подсказку значит предлагать наугад.
        return {"items": []}
    connection = await db.scalar(
        select(IntegrationConnection)
        .where(
            IntegrationConnection.workspace_id == current.workspace_id,
            IntegrationConnection.kind == "meta",
            IntegrationConnection.status == Status.active,
        )
        .order_by(IntegrationConnection.created_at)
    )
    if not connection:
        raise HTTPException(
            status_code=422,
            detail="Нет активного подключения Meta — справочники берутся её токеном",
        )
    client = client_for(connection)
    try:
        rows = await client.targeting_search(kind, query)
    except MetaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    items = []
    for row in rows:
        value = row.get("id") if kind == "interest" else row.get("key")
        if value is None:
            continue
        items.append(
            {
                "id": str(value),
                "name": str(row.get("name") or value),
                # Путь вида «Интересы/Дополнительные интересы» — по одному
                # названию интересы Meta различить нельзя, их тысячи.
                "path": " / ".join(str(part) for part in (row.get("path") or [])),
                "audience": row.get("audience_size_lower_bound"),
            }
        )
    return {"items": items}


@router.get("/accounts/{account_id}/assets")
async def account_assets(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    """Страницы и пиксели кабинета — их выбирают на шаге «Кабинеты».

    Спрашиваем у Meta в момент открытия шага, а не берём из синхронизации:
    пиксель заводят в Events Manager перед самым заливом, и ждать следующей
    синхронизации ради него никто не станет.
    """
    account = await _account(db, current, account_id)
    client, _ = await _account_client(db, account)
    try:
        pages = await client.account_pages(account.external_id)
        pixels = await client.pixels(account.external_id)
    except MetaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "pages": [
            {"id": str(row.get("id")), "name": str(row.get("name") or row.get("id"))}
            for row in pages
            if row.get("id")
        ],
        "pixels": [
            {"id": str(row.get("id")), "name": str(row.get("name") or row.get("id"))}
            for row in pixels
            if row.get("id")
        ],
    }


# --- Шаблоны (ТЗ 3.5) ---------------------------------------------------------


@router.get("/templates", response_model=Page)
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> Page:
    rows = list(
        (
            await db.execute(
                select(MetaTemplate)
                .where(MetaTemplate.workspace_id == current.workspace_id)
                .order_by(MetaTemplate.name)
            )
        ).scalars()
    )
    owners = await _user_names(db, {row.created_by_id for row in rows})
    items = [_template_row(row, owners) for row in rows]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.post("/templates", status_code=201)
async def create_template(
    payload: MetaTemplateCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    duplicate = await db.scalar(
        select(MetaTemplate).where(
            MetaTemplate.workspace_id == current.workspace_id,
            MetaTemplate.name == payload.name,
        )
    )
    if duplicate:
        raise HTTPException(status_code=422, detail="Шаблон с таким названием уже есть")
    values = payload.model_dump()
    template = MetaTemplate(
        workspace_id=current.workspace_id,
        created_by_id=current.id,
        **values,
    )
    _derive_bundle(template)
    db.add(template)
    await audit(
        db, current, "meta.template_created", f"Создан шаблон Meta: {template.name}",
        request=request,
    )
    await db.commit()
    await db.refresh(template)
    return _template_row(template)


@router.patch("/templates/{template_id}")
async def update_template(
    template_id: uuid.UUID,
    payload: MetaTemplateUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    template = await db.get(MetaTemplate, template_id)
    if not template or template.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "settings" and value is None:
            continue
        setattr(template, field, value)
    _derive_bundle(template)
    await audit(
        db, current, "meta.template_updated", f"Изменён шаблон Meta: {template.name}",
        request=request, entity_id=str(template.id),
    )
    await db.commit()
    await db.refresh(template)
    return _template_row(template)


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    template = await db.get(MetaTemplate, template_id)
    if not template or template.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    name = template.name
    # У заливов template_id обнулится (SET NULL) — уже опубликованные кампании
    # это не затрагивает, шаблон нужен только в момент публикации.
    await db.delete(template)
    await audit(
        db, current, "meta.template_deleted", f"Удалён шаблон Meta: {name}",
        request=request, entity_id=str(template_id),
    )
    await db.commit()
    return {"deleted": str(template_id)}


# --- Креативы (ТЗ 3.6) --------------------------------------------------------


@router.get("/creatives", response_model=Page)
async def list_creatives(
    account_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> Page:
    accounts = {
        account.id: account
        for account in await _visible_accounts(db, current, account_id, None)
    }
    if not accounts:
        return Page(items=[], total=0, limit=0, offset=0)
    rows = list(
        (
            await db.execute(
                select(MetaCreative)
                .where(MetaCreative.account_id.in_(list(accounts)))
                .order_by(MetaCreative.created_at.desc())
            )
        ).scalars()
    )
    items = [_creative_row(row, accounts.get(row.account_id)) for row in rows]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.post("/creatives", status_code=201)
async def upload_creative(
    request: Request,
    account_id: uuid.UUID,
    name: str = "",
    unique: bool = False,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    """Загрузить картинку или видео в рекламный кабинет.

    Файл уходит в Meta и остаётся там: у нас хранится только хэш или ID. Держать
    у себя копию незачем — в объявление всё равно попадает то, что лежит в Meta.

    `unique` дописывает в файл случайную метку: один и тот же баннер на двадцати
    кабинетах Meta узнаёт по хэшу, и метка его меняет, не трогая изображение.
    """
    account = await _account(db, current, account_id)
    content = await file.read()
    if unique:
        content = uniquify(content, file.content_type or "")
    kind = _creative_kind(file.content_type or "", len(content))

    client, _ = await _account_client(db, account)
    try:
        if kind == "video":
            response = await client.upload_video(
                account.external_id,
                file_name=file.filename or "video.mp4",
                content=content,
                mime_type=file.content_type or "video/mp4",
            )
        else:
            response = await client.upload_image(
                account.external_id,
                file_name=file.filename or "image.jpg",
                content=content,
                mime_type=file.content_type or "image/jpeg",
            )
    except MetaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    creative = MetaCreative(
        workspace_id=current.workspace_id,
        account_id=account.id,
        kind=kind,
        name=(name or file.filename or "Креатив")[:240],
        file_name=file.filename,
        mime_type=file.content_type,
        byte_size=len(content),
        external_hash=str(response.get("hash") or "") or None,
        external_id=str(response.get("id") or "") or None,
        thumbnail_url=str(response.get("picture") or response.get("url") or "") or None,
        permalink_url=str(response.get("permalink_url") or "") or None,
        uploaded_by_id=current.id,
        external_payload=response,
    )
    if not creative.external_hash and not creative.external_id:
        raise HTTPException(
            status_code=422,
            detail="Meta приняла файл, но не вернула его идентификатор — попробуйте ещё раз",
        )
    db.add(creative)
    await audit(
        db, current, "meta.creative_uploaded",
        f"Загружен креатив «{creative.name}» в кабинет {account.name}", request=request,
    )
    await db.commit()
    await db.refresh(creative)
    return _creative_row(creative, account)


@router.delete("/creatives/{creative_id}")
async def delete_creative(
    creative_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    creative = await db.get(MetaCreative, creative_id)
    if not creative or creative.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Креатив не найден")
    used = await db.scalar(
        select(func.count())
        .select_from(MetaLaunchCreative)
        .where(MetaLaunchCreative.creative_id == creative_id)
    )
    if used:
        raise HTTPException(
            status_code=422,
            detail="Креатив используется в заливах — сначала уберите его оттуда",
        )
    name = creative.name
    # В самой Meta файл остаётся: удалять его отсюда нельзя, на нём могут висеть
    # объявления из кампаний, созданных вне CRM.
    await db.delete(creative)
    await audit(
        db, current, "meta.creative_deleted", f"Удалён креатив «{name}» из CRM",
        request=request, entity_id=str(creative_id),
    )
    await db.commit()
    return {"deleted": str(creative_id)}


# --- Заливы (ТЗ 3.3 и 3.4) ----------------------------------------------------


@router.get("/launches", response_model=Page)
async def list_launches(
    account_id: uuid.UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> Page:
    accounts = {
        account.id: account
        for account in await _visible_accounts(db, current, account_id, None)
    }
    if not accounts:
        return Page(items=[], total=0, limit=0, offset=0)
    filters = [
        MetaLaunch.workspace_id == current.workspace_id,
        MetaLaunch.account_id.in_(list(accounts)),
    ]
    if status:
        filters.append(MetaLaunch.status == status)
    rows = list(
        (
            await db.execute(
                select(MetaLaunch).where(*filters).order_by(MetaLaunch.created_at.desc())
            )
        ).scalars()
    )
    creatives = await _launch_creative_ids(db, [row.id for row in rows])
    owners = await _user_names(db, {row.owner_id for row in rows if row.owner_id})
    items = [
        _launch_row(row, accounts.get(row.account_id), owners, creatives.get(row.id, []))
        for row in rows
    ]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.post("/launches", status_code=201)
async def create_launch(
    payload: MetaLaunchCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    account = await _account(db, current, payload.account_id)
    changes = payload.model_dump(exclude={"creative_ids"})
    launch = MetaLaunch(
        workspace_id=current.workspace_id,
        owner_id=changes.pop("owner_id", None) or current.id,
        **changes,
    )
    db.add(launch)
    await db.flush()
    await _sync_launch_creatives(db, launch, payload.creative_ids)
    await audit(
        db, current, "meta.launch_created", f"Создан залив «{launch.name}» в {account.name}",
        request=request, entity_id=str(launch.id),
    )
    await db.commit()
    await db.refresh(launch)
    return _launch_row(
        launch,
        account,
        await _user_names(db, {launch.owner_id}),
        [str(value) for value in payload.creative_ids],
    )


# Поля залива, которые задаются на конкретный кабинет прямо в мастере.
_LAUNCH_OVERRIDES = {"page_id", "pixel_id", "link_url", "daily_budget", "name"}


@router.post("/launches/batch", status_code=201)
async def create_launch_batch(
    payload: MetaLaunchBatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    """Залить одну связку сразу на несколько кабинетов.

    Кабинет, который не проходит проверку, не отменяет остальные: он
    возвращается отдельной строкой с причиной, а годные уходят в очередь.
    Заготовки при этом создаются для всех — иначе после ошибки пришлось бы
    заново набивать форму.

    Пауза между кабинетами разносит заливы по времени: двадцать кабинетов,
    стартующих в одну секунду с одним креативом, — это ровно тот след, из-за
    которого прилетает бан.
    """
    accounts = {}
    for account_id in dict.fromkeys(payload.account_ids):
        accounts[account_id] = await _account(db, current, account_id)
    shared = payload.model_dump(
        exclude={
            "account_ids",
            "creatives_by_account",
            "publish",
            "account_delay_seconds",
            "overrides",
            "ads_by_account",
        }
    )
    owner_id = shared.pop("owner_id", None) or current.id
    base_at = shared.pop("publish_at", None)
    now = datetime.now(UTC)
    if base_at is not None and base_at.tzinfo is None:
        base_at = base_at.replace(tzinfo=UTC)

    created: list[MetaLaunch] = []
    for order, account_id in enumerate(accounts):
        publish_at = (base_at or now) + timedelta(
            seconds=order * payload.account_delay_seconds
        )
        # Что задано на конкретный кабинет — своя страница, пиксель, ссылка,
        # бюджет — перекрывает общее: остальное в пачке одинаковое.
        own = {
            field: value
            for field, value in (payload.overrides.get(account_id) or {}).items()
            if field in _LAUNCH_OVERRIDES and value not in (None, "")
        }
        launch = MetaLaunch(
            workspace_id=current.workspace_id,
            owner_id=owner_id,
            account_id=account_id,
            publish_at=publish_at if publish_at > now else None,
            ads=[
                item.model_dump(mode="json")
                for item in payload.ads_by_account.get(account_id, [])
            ],
            **{**shared, **own},
        )
        db.add(launch)
        await db.flush()
        await _sync_launch_creatives(
            db, launch, payload.creatives_by_account.get(account_id, [])
        )
        created.append(launch)

    results: list[dict] = []
    queued: list[uuid.UUID] = []
    scheduled = 0
    for launch in created:
        row = {
            "id": str(launch.id),
            "account_id": str(launch.account_id),
            "account_name": accounts[launch.account_id].name,
            "ok": True,
            "status": "draft",
        }
        if payload.publish:
            context = await load_launch_context(db, launch.id)
            try:
                if not context:
                    raise LaunchValidationError("Залив не найден")
                validate_launch(context)
                if launch.publish_at:
                    # Запланированный залив ставит в очередь планировщик, когда
                    # придёт время. Держать задачу в брокере сутками нельзя:
                    # перезапуск воркера её потеряет, а залив не состоится.
                    scheduled += 1
                    row["status"] = "scheduled"
                    row["publish_at"] = launch.publish_at.isoformat()
                else:
                    queued.append(launch.id)
                    row["status"] = "queued"
            except LaunchValidationError as exc:
                row["ok"] = False
                row["error"] = str(exc)
                launch.publish_at = None
        results.append(row)

    await audit(
        db,
        current,
        "meta.launch_batch_created",
        f"Связка «{payload.name}» залита на {len(created)} кабинетов"
        + (f", в очередь ушло {len(queued)}" if payload.publish else ""),
        request=request,
    )
    await db.commit()
    # Задачи ставятся только после коммита: воркер читает залив из базы.
    for launch_id in queued:
        publish_meta_launch.delay(str(launch_id), str(current.id))
    return {
        "created": len(created),
        "queued": len(queued),
        "scheduled": scheduled,
        "results": results,
    }


@router.get("/operations", response_model=Page)
async def list_operations(
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    launch_id: uuid.UUID | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> Page:
    """Очередь заливки — журнал вызовов Graph API по видимым кабинетам."""
    accounts = {
        account.id: account for account in await _visible_accounts(db, current, None, None)
    }
    if not accounts:
        return Page(items=[], total=0, limit=0, offset=0)
    filters = [
        MetaOperation.workspace_id == current.workspace_id,
        MetaLaunch.account_id.in_(list(accounts)),
    ]
    if status:
        filters.append(MetaOperation.status == status)
    if launch_id:
        filters.append(MetaOperation.launch_id == launch_id)
    if date_from:
        filters.append(func.date(MetaOperation.created_at) >= date_from)
    if date_to:
        filters.append(func.date(MetaOperation.created_at) <= date_to)
    rows = list(
        (
            await db.execute(
                select(MetaOperation, MetaLaunch)
                .join(MetaLaunch, MetaLaunch.id == MetaOperation.launch_id)
                .where(*filters)
                .order_by(MetaOperation.created_at.desc())
                .limit(min(limit, 500))
            )
        ).all()
    )
    authors = await _user_names(db, {row[0].created_by_id for row in rows})
    items = [
        {
            "id": str(operation.id),
            "launch_id": str(launch.id),
            "launch_name": launch.name,
            "account_name": accounts[launch.account_id].name,
            "kind": operation.kind,
            "status": operation.status,
            "target_external_id": operation.target_external_id,
            "error": operation.error,
            "created_at": operation.created_at.isoformat() if operation.created_at else None,
            "created_by": authors.get(operation.created_by_id),
            "launch_status": launch.status.value,
        }
        for operation, launch in rows
    ]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.patch("/launches/{launch_id}")
async def update_launch(
    launch_id: uuid.UUID,
    payload: MetaLaunchUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    launch = await _launch(db, current, launch_id)
    changes = payload.model_dump(exclude_unset=True)
    creative_ids = changes.pop("creative_ids", None)
    if launch.campaign_external_id:
        # После публикации менять параметры таргета бессмысленно: в Meta уже
        # создан adset, и наши поля с ним больше не связаны.
        locked = {
            field
            for field in ("account_id", "template_id", "link_url", "page_id", "pixel_id")
            if field in changes
        }
        if locked:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Залив уже опубликован — эти поля меняются только в кабинете Meta: "
                    + ", ".join(sorted(locked))
                ),
            )
    for field, value in changes.items():
        setattr(launch, field, value)
    if creative_ids is not None:
        if launch.campaign_external_id:
            raise HTTPException(
                status_code=422,
                detail="У опубликованного залива состав креативов не меняется",
            )
        await _sync_launch_creatives(db, launch, creative_ids)
    await audit(
        db, current, "meta.launch_updated", f"Изменён залив «{launch.name}»",
        request=request, entity_id=str(launch.id),
    )
    await db.commit()
    await db.refresh(launch)
    account = await db.get(MetaAdAccount, launch.account_id)
    linked = await _launch_creative_ids(db, [launch.id])
    return _launch_row(
        launch, account, await _user_names(db, {launch.owner_id}), linked.get(launch.id, [])
    )


@router.delete("/launches/{launch_id}")
async def delete_launch(
    launch_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    launch = await _launch(db, current, launch_id)
    if launch.campaign_external_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "Залив опубликован: удаление записи в CRM не остановит кампанию в Meta. "
                "Сначала остановите её, потом удаляйте."
            ),
        )
    name = launch.name
    await db.delete(launch)
    await audit(
        db, current, "meta.launch_deleted", f"Удалён залив «{name}»",
        request=request, entity_id=str(launch_id),
    )
    await db.commit()
    return {"deleted": str(launch_id)}


@router.post("/launches/{launch_id}/validate")
async def validate_launch_endpoint(
    launch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    """Проверить залив, не отправляя ничего в Meta."""
    await _launch(db, current, launch_id)
    context = await load_launch_context(db, launch_id)
    if not context:
        raise HTTPException(status_code=404, detail="Залив не найден")
    try:
        validate_launch(context)
    except LaunchValidationError as exc:
        return {"ok": False, "problems": str(exc).split("; ")}
    return {"ok": True, "problems": []}


@router.post("/launches/{launch_id}/publish", status_code=202)
async def publish_launch(
    launch_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    launch = await _launch(db, current, launch_id)
    if launch.status == LaunchStatus.publishing:
        return {"status": "publishing", "id": str(launch.id)}
    context = await load_launch_context(db, launch_id)
    if not context:
        raise HTTPException(status_code=404, detail="Залив не найден")
    try:
        validate_launch(context)
    except LaunchValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit(
        db, current, "meta.launch_published",
        f"Публикация залива «{launch.name}»"
        + (" со снятием с паузы" if launch.activate_on_publish else " на паузе"),
        request=request, entity_id=str(launch.id),
    )
    await db.commit()
    publish_meta_launch.delay(str(launch_id), str(current.id))
    return {"status": "queued", "id": str(launch_id)}


@router.post("/launches/{launch_id}/status")
async def set_launch_status(
    launch_id: uuid.UUID,
    request: Request,
    action: str,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    """Пауза и возобновление кампании — ТЗ 3.4."""
    if action not in {"pause", "resume"}:
        raise HTTPException(status_code=422, detail="Действие должно быть pause или resume")
    launch = await _launch(db, current, launch_id)
    result = await _apply_launch_action(db, current, launch, action, request)
    await db.commit()
    return result


@router.post("/launches/bulk")
async def bulk_launch_action(
    request: Request,
    action: str,
    ids: list[uuid.UUID],
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    """Массовое управление заливами — ТЗ 3.4.

    Ошибка на одном заливе не отменяет остальные: каждый обрабатывается сам по
    себе, а в ответ уходит список с результатом по каждому.
    """
    if action not in {"pause", "resume", "publish"}:
        raise HTTPException(status_code=422, detail="Действие должно быть pause, resume или publish")
    if len(ids) > 50:
        raise HTTPException(status_code=422, detail="За раз можно обработать не больше 50 заливов")
    results = []
    for launch_id in ids:
        try:
            launch = await _launch(db, current, launch_id)
            if action == "publish":
                context = await load_launch_context(db, launch_id)
                if not context:
                    raise HTTPException(status_code=404, detail="Залив не найден")
                validate_launch(context)
                publish_meta_launch.delay(str(launch_id), str(current.id))
                results.append({"id": str(launch_id), "ok": True, "status": "queued"})
            else:
                results.append(
                    {
                        "id": str(launch_id),
                        "ok": True,
                        **await _apply_launch_action(db, current, launch, action, request),
                    }
                )
        except (HTTPException, LaunchValidationError, MetaError) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            results.append({"id": str(launch_id), "ok": False, "error": detail})
    await db.commit()
    return {"results": results, "ok": sum(1 for row in results if row["ok"])}


@router.get("/launches/{launch_id}/operations", response_model=Page)
async def launch_operations(
    launch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> Page:
    await _launch(db, current, launch_id)
    rows = list(
        (
            await db.execute(
                select(MetaOperation)
                .where(MetaOperation.launch_id == launch_id)
                .order_by(MetaOperation.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )
    items = [
        {
            "id": str(row.id),
            "kind": row.kind,
            "status": row.status,
            "target_external_id": row.target_external_id,
            "error": row.error,
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


# --- Автоправила (ТЗ 3.8) -----------------------------------------------------


@router.get("/rules", response_model=Page)
async def list_rules(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> Page:
    rows = list(
        (
            await db.execute(
                select(MetaRule)
                .where(MetaRule.workspace_id == current.workspace_id)
                .order_by(MetaRule.name)
            )
        ).scalars()
    )
    accounts = {
        account.id: account.name
        for account in await _visible_accounts(db, current, None, None)
    }
    items = [_rule_row(row, accounts) for row in rows]
    return Page(items=items, total=len(items), limit=len(items), offset=0)


@router.post("/rules", status_code=201)
async def create_rule(
    payload: MetaRuleCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    duplicate = await db.scalar(
        select(MetaRule).where(
            MetaRule.workspace_id == current.workspace_id, MetaRule.name == payload.name
        )
    )
    if duplicate:
        raise HTTPException(status_code=422, detail="Правило с таким названием уже есть")
    if payload.account_id:
        await _account(db, current, payload.account_id)
    values = payload.model_dump()
    # В строки приводим только условия: это JSON-колонка. `account_id` и
    # `launch_id` — настоящие внешние ключи, и строка вместо UUID их ломает.
    values["conditions"] = _rule_conditions(values.get("conditions"))
    rule = MetaRule(
        workspace_id=current.workspace_id, created_by_id=current.id, **values
    )
    db.add(rule)
    await audit(
        db, current, "meta.rule_created",
        f"Создано автоправило «{rule.name}»: {LEVEL_LABELS.get(rule.level, rule.level)}, "
        f"условий {len(rule.conditions or [])} → {rule.action}",
        request=request,
    )
    await db.commit()
    await db.refresh(rule)
    return _rule_row(rule, {})


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: uuid.UUID,
    payload: MetaRuleUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    rule = await db.get(MetaRule, rule_id)
    if not rule or rule.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    changes = payload.model_dump(exclude_unset=True)
    if "conditions" in changes:
        changes["conditions"] = _rule_conditions(changes["conditions"])
    for field, value in changes.items():
        setattr(rule, field, value)
    if rule.action in {"increase_budget", "decrease_budget"} and rule.level == "ad":
        raise HTTPException(
            status_code=422,
            detail="У объявления нет собственного бюджета — выберите кампанию или адсет",
        )
    await audit(
        db, current, "meta.rule_updated", f"Изменено автоправило «{rule.name}»",
        request=request, entity_id=str(rule.id),
    )
    await db.commit()
    await db.refresh(rule)
    return _rule_row(rule, {})


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    rule = await db.get(MetaRule, rule_id)
    if not rule or rule.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    name = rule.name
    await db.delete(rule)
    await audit(
        db, current, "meta.rule_deleted", f"Удалено автоправило «{name}»",
        request=request, entity_id=str(rule_id),
    )
    await db.commit()
    return {"deleted": str(rule_id)}


@router.post("/rules/{rule_id}/preview")
async def preview_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> dict:
    """Показать, по каким кампаниям правило сработало бы прямо сейчас.

    Ничего не выполняет и не пишет в журнал — до включения правила с действием
    это единственный способ увидеть его последствия заранее.
    """
    rule = await db.get(MetaRule, rule_id)
    if not rule or rule.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    rows = await collect_candidates(db, rule)
    hits = [row for row in rows if matches(rule, row["metrics"])]
    wanted = [str(item.get("metric") or "") for item in (rule.conditions or [])]
    return {
        "scanned": len(rows),
        "matched": len(hits),
        "level": rule.level,
        "objects": [
            {
                "external_id": row["external_id"],
                "name": row["entity_name"],
                "account_name": row["account_name"],
                # Значения ровно тех метрик, по которым правило и решает.
                "values": {metric: row["metrics"].get(metric) for metric in wanted},
                "spend": row["metrics"]["spend"],
            }
            for row in hits[:50]
        ],
    }


@router.post("/rules/run", status_code=202)
async def run_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.launch")),
) -> dict:
    """Прогнать все правила прямо сейчас, не дожидаясь расписания."""
    await audit(db, current, "meta.rules_run", "Ручной прогон автоправил Meta", request=request)
    await db.commit()
    return await MetaRuleEngine(SessionLocal).run()


@router.get("/rule-events", response_model=Page)
async def list_rule_events(
    limit: int = 50,
    unread: bool = False,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> Page:
    filters = [MetaRuleEvent.workspace_id == current.workspace_id]
    if unread:
        filters.append(MetaRuleEvent.acknowledged_at.is_(None))
    rows = list(
        (
            await db.execute(
                select(MetaRuleEvent)
                .where(*filters)
                .order_by(MetaRuleEvent.created_at.desc())
                .limit(min(limit, 200))
            )
        ).scalars()
    )
    total = await db.scalar(
        select(func.count()).select_from(MetaRuleEvent).where(*filters)
    )
    items = [
        {
            "id": str(row.id),
            "rule_id": str(row.rule_id),
            "campaign_external_id": row.campaign_external_id,
            "campaign_name": row.campaign_name,
            "metric": row.metric,
            "metric_value": float(row.metric_value) if row.metric_value is not None else None,
            "action": row.action,
            "applied": row.applied,
            "message": row.message,
            "error": row.error,
            "acknowledged": row.acknowledged_at is not None,
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return Page(items=items, total=total or 0, limit=min(limit, 200), offset=0)


@router.post("/rule-events/ack")
async def acknowledge_rule_events(
    ids: list[uuid.UUID] | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("meta.view")),
) -> dict:
    filters = [
        MetaRuleEvent.workspace_id == current.workspace_id,
        MetaRuleEvent.acknowledged_at.is_(None),
    ]
    if ids:
        filters.append(MetaRuleEvent.id.in_(ids))
    rows = list((await db.execute(select(MetaRuleEvent).where(*filters))).scalars())
    now = datetime.now(UTC)
    for row in rows:
        row.acknowledged_at = now
    await db.commit()
    return {"acknowledged": len(rows)}


async def _account(
    db: AsyncSession, current: User, account_id: uuid.UUID
) -> MetaAdAccount:
    account = await db.get(MetaAdAccount, account_id)
    if not account or account.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Кабинет не найден")
    if not await has_full_access(db, current):
        if account.owner_id not in await accessible_user_ids(db, current):
            raise HTTPException(status_code=404, detail="Кабинет не найден")
    return account


async def _launch(db: AsyncSession, current: User, launch_id: uuid.UUID) -> MetaLaunch:
    launch = await db.get(MetaLaunch, launch_id)
    if not launch or launch.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Залив не найден")
    if not await has_full_access(db, current):
        allowed = await accessible_user_ids(db, current)
        account = await db.get(MetaAdAccount, launch.account_id)
        if launch.owner_id not in allowed and (not account or account.owner_id not in allowed):
            raise HTTPException(status_code=404, detail="Залив не найден")
    return launch


async def _account_client(
    db: AsyncSession, account: MetaAdAccount
) -> tuple[MetaClient, IntegrationConnection]:
    connection = await db.get(IntegrationConnection, account.connection_id)
    if not connection:
        raise HTTPException(status_code=422, detail="У кабинета нет подключения Meta")
    return client_for(connection), connection


async def _apply_launch_action(
    db: AsyncSession,
    current: User,
    launch: MetaLaunch,
    action: str,
    request: Request,
) -> dict:
    if not launch.campaign_external_id:
        raise HTTPException(
            status_code=422, detail="Залив ещё не опубликован — останавливать нечего"
        )
    account = await db.get(MetaAdAccount, launch.account_id)
    if not account:
        raise HTTPException(status_code=422, detail="Кабинет залива не найден")
    client, _ = await _account_client(db, account)
    status = "PAUSED" if action == "pause" else "ACTIVE"
    operation = MetaOperation(
        workspace_id=launch.workspace_id,
        launch_id=launch.id,
        kind=f"campaign_{action}",
        target_external_id=launch.campaign_external_id,
        status="pending",
        request={"status": status},
        created_by_id=current.id,
    )
    db.add(operation)
    await db.flush()
    try:
        response = await client.set_status(launch.campaign_external_id, status)
    except MetaError as exc:
        operation.status = "failed"
        operation.error = str(exc)
        await db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    operation.status = "success"
    operation.response = response if isinstance(response, dict) else {}
    launch.status = LaunchStatus.paused if action == "pause" else LaunchStatus.active
    await audit(
        db, current, f"meta.launch_{action}",
        f"Залив «{launch.name}»: {'остановлен' if action == 'pause' else 'возобновлён'}",
        request=request, entity_id=str(launch.id),
    )
    return {"id": str(launch.id), "status": launch.status.value}


async def _sync_launch_creatives(
    db: AsyncSession, launch: MetaLaunch, creative_ids: list[uuid.UUID]
) -> None:
    existing = {
        link.creative_id: link
        for link in (
            await db.execute(
                select(MetaLaunchCreative).where(MetaLaunchCreative.launch_id == launch.id)
            )
        ).scalars()
    }
    wanted = list(dict.fromkeys(creative_ids))
    if wanted:
        found = list(
            (
                await db.execute(
                    select(MetaCreative).where(
                        MetaCreative.id.in_(wanted),
                        MetaCreative.workspace_id == launch.workspace_id,
                    )
                )
            ).scalars()
        )
        if len(found) != len(wanted):
            raise HTTPException(status_code=422, detail="Один из креативов не найден")
        for creative in found:
            if creative.account_id != launch.account_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"Креатив «{creative.name}» загружен в другой кабинет",
                )
    for position, creative_id in enumerate(wanted):
        link = existing.pop(creative_id, None)
        if link:
            link.position = position
            continue
        db.add(
            MetaLaunchCreative(
                launch_id=launch.id, creative_id=creative_id, position=position
            )
        )
    for link in existing.values():
        await db.delete(link)


async def _launch_creative_ids(
    db: AsyncSession, launch_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Состав креативов по заливам — форма редактирования отмечает по нему галочки."""
    if not launch_ids:
        return {}
    rows = await db.execute(
        select(MetaLaunchCreative.launch_id, MetaLaunchCreative.creative_id)
        .where(MetaLaunchCreative.launch_id.in_(launch_ids))
        .order_by(MetaLaunchCreative.position)
    )
    grouped: dict[uuid.UUID, list[str]] = {}
    for launch_id, creative_id in rows:
        grouped.setdefault(launch_id, []).append(str(creative_id))
    return grouped


async def _user_names(db: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    clean = {value for value in ids if value}
    if not clean:
        return {}
    rows = await db.execute(select(User.id, User.name).where(User.id.in_(clean)))
    return {row.id: row.name for row in rows}


def _creative_kind(mime_type: str, size: int) -> str:
    if size <= 0:
        raise HTTPException(status_code=422, detail="Файл пустой")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ",
        )
    if mime_type in VIDEO_MIME_TYPES:
        return "video"
    if mime_type in IMAGE_MIME_TYPES:
        return "image"
    raise HTTPException(
        status_code=422,
        detail="Meta принимает JPG, PNG, GIF, WEBP и видео MP4/MOV/WEBM",
    )


def _derive_bundle(template: MetaTemplate) -> None:
    """Достроить связку до того, что ждёт Graph API.

    Цель кампании в интерфейсе одна («Лиды»), а в Meta это пара
    objective + optimization_goal, и держать их врозь нельзя: сохранённая цель
    и сохранённая оптимизация разъехались бы при первой же правке. Бюджет по
    той же причине: сумма в форме одна, а полей в Meta два, и заполнять надо
    ровно то, которое выбрано типом.
    """
    settings = MetaBundleSettings(**(template.settings or {}))
    goal = meta_bundle.goal_for(settings.campaign.goal)
    template.objective = goal["objective"]
    template.optimization_goal = goal["optimization_goal"]
    template.billing_event = goal["billing_event"]
    amount = template.daily_budget or template.lifetime_budget
    if settings.campaign.budget_kind == "lifetime":
        template.daily_budget, template.lifetime_budget = None, amount
    else:
        template.daily_budget, template.lifetime_budget = amount, None
    template.settings = settings.model_dump(mode="json")


def _template_row(template: MetaTemplate, owners: dict | None = None) -> dict:
    return {
        "id": str(template.id),
        "name": template.name,
        "created_by": (owners or {}).get(template.created_by_id),
        "objective": template.objective,
        "optimization_goal": template.optimization_goal,
        "billing_event": template.billing_event,
        "bid_strategy": template.bid_strategy,
        "geo": template.geo or [],
        "age_min": template.age_min,
        "age_max": template.age_max,
        "genders": template.genders or [],
        "languages": template.languages or [],
        "placements": template.placements or {},
        "interests": template.interests or [],
        "daily_budget": float(template.daily_budget) if template.daily_budget else None,
        "lifetime_budget": (
            float(template.lifetime_budget) if template.lifetime_budget else None
        ),
        "page_id": template.page_id,
        "pixel_id": template.pixel_id,
        "custom_event_type": template.custom_event_type,
        "call_to_action": template.call_to_action,
        "notes": template.notes,
        "status": template.status.value,
        "settings": MetaBundleSettings(**(template.settings or {})).model_dump(mode="json"),
    }


def _creative_row(creative: MetaCreative, account: MetaAdAccount | None) -> dict:
    return {
        "id": str(creative.id),
        "account_id": str(creative.account_id),
        "account_name": account.name if account else "",
        "kind": creative.kind,
        "name": creative.name,
        "file_name": creative.file_name,
        "byte_size": creative.byte_size,
        "external_hash": creative.external_hash,
        "external_id": creative.external_id,
        "thumbnail_url": creative.thumbnail_url,
        "created_at": creative.created_at,
    }


def _launch_row(
    launch: MetaLaunch,
    account: MetaAdAccount | None,
    owners: dict[uuid.UUID, str],
    creative_ids: list[str],
) -> dict:
    return {
        "id": str(launch.id),
        "name": launch.name,
        "account_id": str(launch.account_id),
        "account_name": account.name if account else "",
        "currency": account.currency if account else "USD",
        "template_id": str(launch.template_id) if launch.template_id else None,
        "offer_id": str(launch.offer_id) if launch.offer_id else None,
        "partner_id": str(launch.partner_id) if launch.partner_id else None,
        "owner_id": str(launch.owner_id) if launch.owner_id else None,
        "owner_name": owners.get(launch.owner_id) if launch.owner_id else None,
        "geo": launch.geo,
        "daily_budget": float(launch.daily_budget or 0),
        "spend_limit": float(launch.spend_limit) if launch.spend_limit else None,
        "start_date": launch.start_date.isoformat() if launch.start_date else None,
        "end_date": launch.end_date.isoformat() if launch.end_date else None,
        "link_url": launch.link_url,
        "primary_text": launch.primary_text,
        "headline": launch.headline,
        "description": launch.description,
        "call_to_action": launch.call_to_action,
        "page_id": launch.page_id,
        "pixel_id": launch.pixel_id,
        "status": launch.status.value,
        "activate_on_publish": launch.activate_on_publish,
        "publish_at": launch.publish_at,
        "start_at": launch.start_at,
        "pause_campaigns": launch.pause_campaigns,
        "pause_adsets": launch.pause_adsets,
        "pause_ads": launch.pause_ads,
        "campaign_external_id": launch.campaign_external_id,
        "adset_external_id": launch.adset_external_id,
        "creatives": len(creative_ids),
        "creative_ids": creative_ids,
        "published_at": launch.published_at,
        "last_error": launch.last_error,
    }


def _rule_conditions(raw: object) -> list[dict]:
    """Условия для JSON-колонки: пороги приводим к строке.

    Decimal в JSON не сериализуется, а float молча терял бы точность порога.
    """
    return [
        {
            "metric": str(item.get("metric") or ""),
            "operator": str(item.get("operator") or "lt"),
            "value": str(item.get("value")),
        }
        for item in (raw or [])
        if isinstance(item, dict)
    ]


def _rule_row(rule: MetaRule, accounts: dict[uuid.UUID, str]) -> dict:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "account_id": str(rule.account_id) if rule.account_id else None,
        "account_name": accounts.get(rule.account_id) if rule.account_id else None,
        "launch_id": str(rule.launch_id) if rule.launch_id else None,
        "level": rule.level,
        "entity_status": rule.entity_status,
        "window": rule.window,
        "conditions": rule.conditions or [],
        "min_spend": float(rule.min_spend or 0),
        "action": rule.action,
        "action_value": float(rule.action_value) if rule.action_value is not None else None,
        "is_enabled": rule.is_enabled,
        "frequency_minutes": rule.frequency_minutes,
        "cooldown_minutes": rule.cooldown_minutes,
        "last_checked_at": rule.last_checked_at,
        "last_triggered_at": rule.last_triggered_at,
    }


async def _connections(
    db: AsyncSession, workspace_id: uuid.UUID
) -> list[IntegrationConnection]:
    return list(
        (
            await db.execute(
                select(IntegrationConnection)
                .where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.kind == "meta",
                )
                .order_by(IntegrationConnection.name)
            )
        ).scalars()
    )


async def _connection(
    db: AsyncSession, current: User, connection_id: uuid.UUID
) -> IntegrationConnection:
    connection = await db.get(IntegrationConnection, connection_id)
    if (
        not connection
        or connection.workspace_id != current.workspace_id
        or connection.kind != "meta"
    ):
        raise HTTPException(status_code=404, detail="Подключение не найдено")
    return connection


async def _with_latest_runs(
    db: AsyncSession, connections: list[IntegrationConnection]
) -> list[tuple[IntegrationConnection, SyncRun | None]]:
    if not connections:
        return []
    runs = list(
        (
            await db.execute(
                select(SyncRun)
                .where(SyncRun.connection_id.in_([item.id for item in connections]))
                .order_by(SyncRun.started_at.desc())
            )
        ).scalars()
    )
    latest: dict[uuid.UUID, SyncRun] = {}
    for run in runs:
        latest.setdefault(run.connection_id, run)
    return [(connection, latest.get(connection.id)) for connection in connections]


def client_for(connection: IntegrationConnection) -> MetaClient:
    """Клиент этого подключения — с его токеном, прокси и user-agent."""
    return MetaClient(
        decrypt_secret(connection.api_key_encrypted),
        proxy=connection.proxy_url,
        user_agent=connection.user_agent,
    )


async def _verify_token(
    access_token: str,
    business_id: str | None,
    *,
    proxy: str | None = None,
    user_agent: str | None = None,
) -> list[dict]:
    """Токен принимается, только если через него реально видны кабинеты.

    Проверка «/me отвечает» ничего не доказывает: она проходит и с токеном без
    ads_read, а первая же синхронизация падает. Возвращаем сами кабинеты — их
    показывает мастер подключения на шаге выбора.
    """
    client = MetaClient(access_token, proxy=proxy, user_agent=user_agent)
    try:
        await client.check()
        accounts = await client.ad_accounts(business_id)
    except MetaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not accounts:
        raise HTTPException(
            status_code=422,
            detail=(
                "Токен принят, но через него не видно ни одного рекламного кабинета. "
                "Проверьте, что системному пользователю выдан доступ к кабинетам "
                "в Business Manager."
            ),
        )
    return accounts


def _preview_row(row: dict) -> dict:
    return {
        "external_id": str(row.get("id") or ""),
        "name": str(row.get("name") or row.get("id") or ""),
        "account_status": account_status_label(row.get("account_status")),
        "currency": str(row.get("currency") or "USD"),
        "timezone_name": row.get("timezone_name"),
        "amount_spent": float(money_from_minor(row.get("amount_spent")) or 0),
        "spend_cap": (
            float(money_from_minor(row.get("spend_cap")))
            if money_from_minor(row.get("spend_cap")) is not None
            else None
        ),
    }


def _seed_accounts(
    db: AsyncSession,
    connection: IntegrationConnection,
    accounts: list[dict],
    selected: list[str] | None,
) -> int:
    """Создать кабинеты сразу при подключении, а не ждать синхронизации.

    Невыбранные кабинеты не пропускаются, а заводятся выключенными: список в
    Business Manager со временем меняется, и держать перед глазами полную картину
    полезнее, чем гадать, почему кабинет «пропал». Синхронизация статус не трогает
    и по выключенным кабинетам ничего не тянет.
    """
    wanted = {value.strip() for value in (selected or []) if value.strip()}
    imported = 0
    for row in accounts:
        external_id = str(row.get("id") or "").strip()
        if not external_id:
            continue
        enabled = not wanted or external_id in wanted
        imported += int(enabled)
        db.add(
            MetaAdAccount(
                workspace_id=connection.workspace_id,
                connection_id=connection.id,
                external_id=external_id,
                name=str(row.get("name") or external_id)[:240],
                account_status=account_status_label(row.get("account_status")),
                currency=str(row.get("currency") or "USD")[:8],
                timezone_name=str(row.get("timezone_name") or "") or None,
                spend_cap=money_from_minor(row.get("spend_cap")),
                amount_spent=money_from_minor(row.get("amount_spent")) or ZERO,
                balance=money_from_minor(row.get("balance")),
                status=Status.active if enabled else Status.inactive,
                external_payload=row,
            )
        )
    return imported


async def _visible_accounts(
    db: AsyncSession,
    current: User,
    account_id: uuid.UUID | None,
    owner_id: uuid.UUID | None,
) -> list[MetaAdAccount]:
    filters = [MetaAdAccount.workspace_id == current.workspace_id]
    if not await has_full_access(db, current):
        # Баер видит кабинеты, которые закреплены за ним или за его людьми.
        # Кабинет без ответственного остаётся видимым только руководству.
        filters.append(MetaAdAccount.owner_id.in_(await accessible_user_ids(db, current)))
    if account_id:
        filters.append(MetaAdAccount.id == account_id)
    if owner_id:
        filters.append(MetaAdAccount.owner_id == owner_id)
    return list(
        (
            await db.execute(
                select(MetaAdAccount).where(*filters).order_by(MetaAdAccount.name)
            )
        ).scalars()
    )


async def _owner_names(
    db: AsyncSession, accounts: list[MetaAdAccount]
) -> dict[uuid.UUID, str]:
    owner_ids = {account.owner_id for account in accounts if account.owner_id}
    if not owner_ids:
        return {}
    rows = await db.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
    return {row.id: row.name for row in rows}


def _account_rows(
    accounts: list[MetaAdAccount],
    stats: list[MetaStatDaily],
    keitaro: dict[str, dict],
    owners: dict[uuid.UUID, str],
) -> list[dict]:
    by_account: dict[uuid.UUID, list[MetaStatDaily]] = {}
    for row in stats:
        by_account.setdefault(row.account_id, []).append(row)
    rows = []
    for account in accounts:
        account_stats = by_account.get(account.id, [])
        values = totals(account_stats, keitaro)
        rows.append(
            {
                "id": str(account.id),
                "external_id": account.external_id,
                "name": account.name,
                "currency": account.currency,
                "account_status": account.account_status,
                "status": account.status.value,
                "timezone_name": account.timezone_name,
                "spend_cap": float(account.spend_cap) if account.spend_cap else None,
                "amount_spent": float(account.amount_spent or 0),
                "balance": float(account.balance) if account.balance is not None else None,
                "owner_id": str(account.owner_id) if account.owner_id else None,
                "owner_name": owners.get(account.owner_id) if account.owner_id else None,
                **values,
            }
        )
    return rows


async def _campaign_rows(
    db: AsyncSession,
    accounts: list[MetaAdAccount],
    stats: list[MetaStatDaily],
    keitaro: dict[str, dict],
) -> list[dict]:
    if not accounts:
        return []
    account_names = {account.id: account.name for account in accounts}
    entities = list(
        (
            await db.execute(
                select(MetaEntity).where(
                    MetaEntity.account_id.in_(list(account_names)),
                    MetaEntity.level == "campaign",
                )
            )
        ).scalars()
    )
    by_external = {entity.external_id: entity for entity in entities}

    grouped: dict[str, list[MetaStatDaily]] = {}
    for row in stats:
        if row.campaign_external_id:
            grouped.setdefault(row.campaign_external_id, []).append(row)

    rows = []
    for external_id, campaign_stats in grouped.items():
        entity = by_external.get(external_id)
        account_id = campaign_stats[0].account_id
        tracker = keitaro.get(external_id)
        values = metrics(
            sum((row.spend or ZERO for row in campaign_stats), ZERO),
            sum(row.impressions or 0 for row in campaign_stats),
            sum(row.clicks or 0 for row in campaign_stats),
            tracker["revenue"] if tracker else None,
            tracker["leads"] if tracker else 0,
            tracker["sales"] if tracker else 0,
        )
        rows.append(
            {
                "external_id": external_id,
                "name": entity.name if entity else f"Кампания {external_id}",
                "effective_status": entity.effective_status if entity else None,
                "objective": entity.objective if entity else None,
                "daily_budget": (
                    float(entity.daily_budget)
                    if entity and entity.daily_budget is not None
                    else None
                ),
                "account_id": str(account_id),
                "account_name": account_names.get(account_id, ""),
                **values,
            }
        )
    rows.sort(key=lambda row: row["spend"], reverse=True)
    return rows


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = datetime.now(UTC).date()
    end = date_to or today
    start = date_from or (end - timedelta(days=6))
    if start > end:
        raise HTTPException(status_code=422, detail="Начало периода позже конца")
    if (end - start).days > MAX_PERIOD_DAYS:
        raise HTTPException(status_code=422, detail="Период не может быть длиннее полугода")
    return start, end


def _run_payload(run: SyncRun | None) -> dict | None:
    if not run:
        return None
    return {
        "id": str(run.id),
        "status": run.status.value,
        "mode": run.mode,
        "progress_pct": run.progress_pct,
        "rows_processed": run.rows_processed,
        "error": run.error,
        "details": run.details,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
