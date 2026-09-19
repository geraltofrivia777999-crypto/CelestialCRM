import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal, get_db
from app.core.deps import get_current_user, require_permission
from app.core.security import decrypt_secret, encrypt_secret
from app.models import (
    FinanceRecord,
    IdempotencyRecord,
    IntegrationConnection,
    KeitaroCampaign,
    KeitaroGroup,
    KeitaroStatDaily,
    MediaRecord,
    Offer,
    Partner,
    Status,
    SyncRun,
    SyncStatus,
    User,
)
from app.schemas import ConnectionCreate, ConnectionOut, ConnectionUpdate, Page
from app.services.audit import audit
from app.services.keitaro import KeitaroClient, KeitaroError
from app.services.keitaro_sync import KeitaroSyncEngine
from app.workers.tasks import sync_keitaro_connection

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Периоды пересинхронизации из настроек подключения. Список закрытый: год по
# дню — предел того, что имеет смысл тянуть из трекера одним запуском.
RESYNC_DAYS = (30, 90, 180, 365)

# Кнопку «Обновить» в Медиаборде жмут часто, а синхронизация ходит в трекер:
# данные свежее минуты заново не тянем.
REFRESH_COOLDOWN = timedelta(seconds=60)


@router.get("/keitaro", response_model=Page)
async def list_connections(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.view")),
) -> Page:
    filters = [
        IntegrationConnection.workspace_id == current.workspace_id,
        IntegrationConnection.kind == "keitaro",
    ]
    total = await db.scalar(
        select(func.count()).select_from(IntegrationConnection).where(*filters)
    )
    items = list(
        (
            await db.execute(
                select(IntegrationConnection)
                .where(*filters)
                .order_by(IntegrationConnection.name)
                .limit(min(limit, 100))
                .offset(offset)
            )
        ).scalars()
    )
    return Page(
        items=[ConnectionOut.model_validate(item).model_dump(mode="json") for item in items],
        total=total or 0,
        limit=min(limit, 100),
        offset=offset,
    )


@router.post("/keitaro", response_model=ConnectionOut, status_code=201)
async def create_connection(
    payload: ConnectionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> IntegrationConnection:
    client = KeitaroClient(payload.base_url, payload.api_key)
    try:
        await client.check()
    except KeitaroError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    connection = IntegrationConnection(
        workspace_id=current.workspace_id,
        name=payload.name,
        base_url=payload.base_url.rstrip("/"),
        api_key_encrypted=encrypt_secret(payload.api_key),
        sync_interval_minutes=payload.sync_interval_minutes,
        timezone=payload.timezone,
        buyer_sub_id=payload.buyer_sub_id,
        lookback_days=payload.lookback_days,
    )
    db.add(connection)
    await audit(
        db,
        current,
        "integration.created",
        f"Created Keitaro connection {connection.name}",
        request=request,
    )
    await db.commit()
    await db.refresh(connection)
    return connection


@router.patch("/keitaro/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: uuid.UUID,
    payload: ConnectionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> IntegrationConnection:
    connection = await db.get(IntegrationConnection, connection_id)
    if not connection or connection.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    changes = payload.model_dump(exclude_none=True)
    api_key = changes.pop("api_key", None)
    for field, value in changes.items():
        if field == "base_url":
            value = value.rstrip("/")
        setattr(connection, field, value)
    if api_key:
        connection.api_key_encrypted = encrypt_secret(api_key)
    if payload.base_url or api_key:
        client = KeitaroClient(
            connection.base_url,
            api_key or decrypt_secret(connection.api_key_encrypted),
        )
        try:
            await client.check()
        except KeitaroError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit(
        db,
        current,
        "integration.updated",
        f"Updated Keitaro connection {connection.name}",
        request=request,
        entity_id=str(connection.id),
    )
    await db.commit()
    await db.refresh(connection)
    return connection


@router.delete("/keitaro/{connection_id}", status_code=200)
async def delete_connection(
    connection_id: uuid.UUID,
    request: Request,
    purge: bool = False,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    connection = await db.get(IntegrationConnection, connection_id)
    if not connection or connection.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")

    # Offers, партнёрки, кампании и статистика уходят каскадом вместе с подключением.
    # Медиаборд и Финансы каскада не имеют — это ручные данные команды, и удалить их
    # можно только по отдельному подтверждению, иначе БД просто отклонит удаление.
    offer_ids = select(Offer.id).where(Offer.connection_id == connection.id)
    media_count = (
        await db.scalar(
            select(func.count()).select_from(MediaRecord).where(
                MediaRecord.offer_id.in_(offer_ids)
            )
        )
        or 0
    )
    finance_count = (
        await db.scalar(
            select(func.count()).select_from(FinanceRecord).where(
                FinanceRecord.offer_id.in_(offer_ids)
            )
        )
        or 0
    )
    if (media_count or finance_count) and not purge:
        raise HTTPException(
            status_code=409,
            detail=(
                "На офферах этого подключения держатся записи: "
                f"Медиаборд — {media_count}, Финансы — {finance_count}. "
                "Удаление подключения сотрёт их вместе с офферами."
            ),
        )
    if purge:
        await db.execute(delete(MediaRecord).where(MediaRecord.offer_id.in_(offer_ids)))
        await db.execute(delete(FinanceRecord).where(FinanceRecord.offer_id.in_(offer_ids)))

    name = connection.name
    await db.delete(connection)
    await audit(
        db,
        current,
        "integration.deleted",
        f"Deleted Keitaro connection {name}",
        request=request,
        entity_id=str(connection_id),
    )
    await db.commit()
    return {
        "deleted": str(connection_id),
        "media_records_removed": media_count if purge else 0,
        "finance_records_removed": finance_count if purge else 0,
    }


@router.get("/keitaro/overview")
async def keitaro_overview(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.view")),
) -> dict:
    connection_filters = [
        IntegrationConnection.workspace_id == current.workspace_id,
        IntegrationConnection.kind == "keitaro",
    ]
    connections = list(
        (
            await db.execute(
                select(IntegrationConnection)
                .where(*connection_filters)
                .order_by(IntegrationConnection.name)
            )
        ).scalars()
    )
    connection_ids = [connection.id for connection in connections]
    latest_runs: dict[uuid.UUID, SyncRun] = {}
    if connection_ids:
        runs = list(
            (
                await db.execute(
                    select(SyncRun)
                    .where(SyncRun.connection_id.in_(connection_ids))
                    # `started_at` пуст у задания, потерянного до запуска.
                    # PostgreSQL ставит NULL первым при DESC, и древняя ошибка
                    # иначе навсегда перекрывает более новый успешный запуск.
                    .order_by(SyncRun.created_at.desc())
                )
            ).scalars()
        )
        for run in runs:
            latest_runs.setdefault(run.connection_id, run)
    counts = {
        "partners": await db.scalar(
            select(func.count()).select_from(Partner).where(
                Partner.workspace_id == current.workspace_id
            )
        )
        or 0,
        "offers": await db.scalar(
            select(func.count()).select_from(Offer).where(
                Offer.workspace_id == current.workspace_id
            )
        )
        or 0,
        "campaigns": await db.scalar(
            select(func.count()).select_from(KeitaroCampaign).where(
                KeitaroCampaign.workspace_id == current.workspace_id
            )
        )
        or 0,
        "stat_rows": await db.scalar(
            select(func.count()).select_from(KeitaroStatDaily).where(
                KeitaroStatDaily.workspace_id == current.workspace_id
            )
        )
        or 0,
    }
    return {
        "configured": bool(connections),
        "healthy": bool(connections)
        and all(
            latest_runs.get(connection.id) is None
            or latest_runs[connection.id].status != SyncStatus.failed
            for connection in connections
        ),
        "counts": counts,
        "connections": [
            {
                **ConnectionOut.model_validate(connection).model_dump(mode="json"),
                "last_run": _run_payload(latest_runs.get(connection.id)),
            }
            for connection in connections
        ],
    }


@router.get("/keitaro/sidebar-status")
async def keitaro_sidebar_status(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict:
    connections = list(
        (
            await db.execute(
                select(IntegrationConnection)
                .where(
                    IntegrationConnection.workspace_id == current.workspace_id,
                    IntegrationConnection.kind == "keitaro",
                )
                .order_by(IntegrationConnection.name)
            )
        ).scalars()
    )
    if not connections:
        return {
            "configured": False,
            "state": "not_configured",
            "connection_id": None,
            "progress_pct": 0,
            "last_sync_at": None,
            "error": None,
        }

    connection_ids = [connection.id for connection in connections]
    runs = list(
        (
            await db.execute(
                select(SyncRun)
                .where(SyncRun.connection_id.in_(connection_ids))
                .order_by(SyncRun.created_at.desc())
            )
        ).scalars()
    )
    latest_by_connection: dict[uuid.UUID, SyncRun] = {}
    for run in runs:
        latest_by_connection.setdefault(run.connection_id, run)

    # Только последний запуск каждого подключения описывает его текущее
    # состояние. Старый осиротевший running не должен навечно держать меню на
    # нуле после более новой успешной синхронизации.
    current_runs = list(latest_by_connection.values())
    running = next(
        (run for run in current_runs if run.status == SyncStatus.running), None
    )
    queued = next(
        (run for run in current_runs if run.status == SyncStatus.queued), None
    )
    failed = next(
        (
            run
            for run in latest_by_connection.values()
            if run.status == SyncStatus.failed
        ),
        None,
    )
    active_connection = next(
        (connection for connection in connections if connection.status == Status.active),
        connections[0],
    )
    last_sync_at = max(
        (
            value
            for value in [
                *(connection.last_sync_at for connection in connections),
                *(run.finished_at for run in latest_by_connection.values()),
            ]
            if value is not None
        ),
        default=None,
    )
    if running:
        state = "syncing"
    elif queued:
        state = "queued"
    elif failed:
        state = "error"
    elif any(connection.status == Status.active for connection in connections):
        state = "active"
    else:
        state = "inactive"
    return {
        "configured": True,
        "state": state,
        "connection_id": str(active_connection.id),
        "progress_pct": running.progress_pct if running else 0,
        "last_sync_at": last_sync_at,
        "error": failed.error if failed else None,
    }


async def _active_group_names(db: AsyncSession, workspace_id: uuid.UUID) -> dict[str, set[str]]:
    """Группы активных кампаний и офферов — то, что форма пользователя уже видит.

    Старые кампании и офферы синхронизация хранит ради истории, помечая
    неактивными; их группы к новому пользователю не привязывают.
    """
    campaigns = (
        await db.execute(
            select(KeitaroCampaign.group_name).where(
                KeitaroCampaign.workspace_id == workspace_id,
                KeitaroCampaign.status == Status.active,
            )
        )
    ).scalars()
    offers = (
        await db.execute(
            select(Offer.group_name).where(
                Offer.workspace_id == workspace_id,
                Offer.keitaro_state == "active",
            )
        )
    ).scalars()
    return {
        "campaigns": {name.strip() for name in campaigns if name and name.strip()},
        "offers": {name.strip() for name in offers if name and name.strip()},
    }


class _RecordingGroups:
    """Клиент Keitaro, запоминающий, какие группы трекер вернул сейчас.

    Таблица групп в CRM не знает, что группу в трекере удалили или закрыли к
    ней доступ, — актуальный список есть только в ответе самого Keitaro.
    """

    def __init__(self, client: KeitaroClient) -> None:
        self._client = client
        self.names: dict[str, set[str]] = {"campaigns": set(), "offers": set()}

    async def groups(self, resource_type: str) -> list[dict]:
        rows = await self._client.groups(resource_type)
        self.names.setdefault(resource_type, set()).update(
            str(row.get("name") or "").strip() for row in rows if str(row.get("name") or "").strip()
        )
        return rows

    def __getattr__(self, name: str):
        return getattr(self._client, name)


@router.get("/keitaro/groups")
async def list_keitaro_groups(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> dict:
    """Актуальные группы для привязки пользователя, включая пока пустые."""
    rows = (
        await db.execute(
            select(KeitaroGroup.resource_type, KeitaroGroup.name)
            .join(
                IntegrationConnection,
                IntegrationConnection.id == KeitaroGroup.connection_id,
            )
            .where(
                KeitaroGroup.workspace_id == current.workspace_id,
                IntegrationConnection.status == Status.active,
                IntegrationConnection.kind == "keitaro",
            )
        )
    ).all()
    groups: dict[str, set[str]] = {"campaigns": set(), "offers": set()}
    for resource_type, name in rows:
        normalized = str(name or "").strip()
        if resource_type in groups and normalized:
            groups[resource_type].add(normalized)
    return {
        "campaign_groups": sorted(groups["campaigns"], key=str.lower),
        "offer_groups": sorted(groups["offers"], key=str.lower),
    }


@router.post("/keitaro/groups/refresh")
async def refresh_keitaro_groups(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> dict:
    """Подтянуть группы Keitaro сразу, не дожидаясь синхронизации.

    Нового баера заводят так: в Keitaro открывают CRM доступ к его группе, а в
    CRM привязывают к ней пользователя. До ближайшей синхронизации — а это до
    четверти часа — новой группы в форме нет. Здесь загружаются только
    справочники (группы, офферы, кампании): несколько запросов, без отчёта по
    дням, поэтому ответ приходит сразу, а не фоновой задачей.

    В ответе — группы, которые Keitaro отдал сейчас, плюс группы активных
    кампаний и офферов; новыми считаются те, которых CRM раньше не видела.
    """
    connections = list(
        (
            await db.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == current.workspace_id,
                    IntegrationConnection.kind == "keitaro",
                    IntegrationConnection.status == Status.active,
                )
            )
        ).scalars()
    )
    if not connections:
        raise HTTPException(status_code=422, detail="Keitaro не подключён или выключен")
    workspace_id, user_id = current.workspace_id, current.id
    before = await _active_group_names(db, workspace_id)
    stored = (
        await db.execute(
            select(KeitaroGroup.resource_type, KeitaroGroup.name).where(
                KeitaroGroup.workspace_id == workspace_id
            )
        )
    ).all()
    for kind, name in stored:
        if name and name.strip():
            before.setdefault(kind, set()).add(name.strip())
    targets = [
        (
            {
                "id": connection.id,
                "workspace_id": connection.workspace_id,
                "base_url": connection.base_url,
                "timezone": connection.timezone or "Europe/Moscow",
            },
            decrypt_secret(connection.api_key_encrypted),
        )
        for connection in connections
    ]
    # Справочники пишет движок в своих сеансах. Транзакцию этого сеанса,
    # открытую ещё проверкой прав, закрываем: иначе движок ждёт её, пока она
    # ждёт ответа движка. Пользователя после отката перечитываем.
    await db.rollback()
    engine = KeitaroSyncEngine(SessionLocal, client_factory=KeitaroClient)
    live: dict[str, set[str]] = {"campaigns": set(), "offers": set()}
    for config, api_key in targets:
        client = _RecordingGroups(KeitaroClient(config["base_url"], api_key))
        try:
            await engine._sync_references(config, client)
        except KeitaroError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for kind, names in client.names.items():
            live.setdefault(kind, set()).update(names)
    after = await _active_group_names(db, workspace_id)

    def ordered(values: set[str]) -> list[str]:
        return sorted(values, key=str.lower)

    campaign_groups = live["campaigns"] | after["campaigns"]
    offer_groups = live["offers"] | after["offers"]
    result = {
        "campaign_groups": ordered(campaign_groups),
        "offer_groups": ordered(offer_groups),
        "new_campaign_groups": ordered(campaign_groups - before.get("campaigns", set())),
        "new_offer_groups": ordered(offer_groups - before.get("offers", set())),
    }
    await audit(
        db,
        await db.get(User, user_id),
        "integration.groups_refreshed",
        "Refreshed Keitaro groups: "
        f"{len(result['new_campaign_groups'])} new campaign, "
        f"{len(result['new_offer_groups'])} new offer",
        request=request,
    )
    await db.commit()
    return result


@router.post("/keitaro/offers/refresh")
async def refresh_keitaro_offers(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    """Обновить каталог офферов для CapAlert без загрузки статистики."""
    connection_ids = list(
        (
            await db.execute(
                select(IntegrationConnection.id).where(
                    IntegrationConnection.workspace_id == current.workspace_id,
                    IntegrationConnection.kind == "keitaro",
                    IntegrationConnection.status == Status.active,
                )
            )
        ).scalars()
    )
    if not connection_ids:
        raise HTTPException(status_code=422, detail="Keitaro не подключён или выключен")
    actor_id = current.id
    await db.rollback()
    engine = KeitaroSyncEngine(SessionLocal, client_factory=KeitaroClient)
    totals = {"offer_groups": 0, "affiliate_networks": 0, "offers": 0}
    try:
        for connection_id in connection_ids:
            counts = await engine.refresh_offer_catalog(str(connection_id))
            for key in totals:
                totals[key] += counts.get(key, 0)
    except KeitaroError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit(
        db,
        await db.get(User, actor_id),
        "integration.offer_catalog_refreshed",
        f"Refreshed Keitaro offer catalog: {totals['offers']} offers, "
        f"{totals['offer_groups']} groups",
        request=request,
    )
    await db.commit()
    return totals


@router.post("/keitaro/refresh", status_code=202)
async def refresh_keitaro(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("media.view")),
) -> dict:
    """Обновить данные Keitaro из Медиаборда.

    Ручной запуск в настройках доступен только администратору, а свежие цифры
    нужны баеру и тимлиду — поэтому здесь отдельная точка с правом Медиаборда.
    Настроек она не касается: ставит ту же инкрементальную синхронизацию, что и
    планировщик, по всем активным подключениям воркспейса. Идущую синхронизацию
    не дублирует, а подключение, обновлённое меньше минуты назад, пропускает.
    """
    connections = list(
        (
            await db.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == current.workspace_id,
                    IntegrationConnection.kind == "keitaro",
                    IntegrationConnection.status == Status.active,
                )
            )
        ).scalars()
    )
    if not connections:
        raise HTTPException(status_code=422, detail="Keitaro не подключён или выключен")
    now = datetime.now(UTC)
    queued: list[tuple[IntegrationConnection, SyncRun]] = []
    running = 0
    for connection in connections:
        active = await db.scalar(
            select(SyncRun).where(
                SyncRun.connection_id == connection.id,
                SyncRun.status.in_([SyncStatus.queued, SyncStatus.running]),
            )
        )
        if active:
            running += 1
            continue
        last = connection.last_sync_at
        if last and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last and now - last < REFRESH_COOLDOWN:
            continue
        run = SyncRun(
            connection_id=connection.id,
            mode="incremental",
            status=SyncStatus.queued,
            details={"phase": "queued", "source": "mediaboard"},
        )
        db.add(run)
        await db.flush()
        queued.append((connection, run))
    if queued:
        await audit(
            db,
            current,
            "integration.sync_started",
            f"Mediaboard refresh for {len(queued)} Keitaro connection(s)",
            request=request,
        )
    await db.commit()
    for connection, run in queued:
        sync_keitaro_connection.delay(str(connection.id), str(run.id), "incremental")
    return {
        "state": "syncing" if queued or running else "fresh",
        "queued": len(queued),
        "running": running,
    }


@router.post("/keitaro/{connection_id}/check")
async def check_connection(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    connection = await db.get(IntegrationConnection, connection_id)
    if not connection or connection.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    client = KeitaroClient(connection.base_url, decrypt_secret(connection.api_key_encrypted))
    try:
        await client.check()
    except KeitaroError as exc:
        # Without this the endpoint answered a misconfigured tracker with a 500 and
        # the UI showed "Ошибка запроса" instead of what Keitaro actually said.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/keitaro/{connection_id}/sync", status_code=202)
async def start_sync(
    connection_id: uuid.UUID,
    request: Request,
    mode: str = "incremental",
    days: int | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    if mode not in {"incremental", "backfill", "resync"}:
        raise HTTPException(
            status_code=422, detail="Mode must be incremental, backfill or resync"
        )
    if mode == "resync" and days not in RESYNC_DAYS:
        raise HTTPException(
            status_code=422, detail="Период пересинхронизации: 30, 90, 180 или 365 дней"
        )
    if mode != "resync" and days is not None:
        raise HTTPException(
            status_code=422, detail="Период задаётся только для пересинхронизации"
        )
    connection = await db.get(IntegrationConnection, connection_id)
    if not connection or connection.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    if idempotency_key:
        existing = await db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.workspace_id == current.workspace_id,
                IdempotencyRecord.key == idempotency_key,
            )
        )
        if existing:
            return existing.response
    running = await db.scalar(
        select(SyncRun).where(
            SyncRun.connection_id == connection.id,
            SyncRun.status.in_([SyncStatus.queued, SyncStatus.running]),
        )
    )
    if running:
        # Второй запуск поверх идущего не ставим: оба писали бы одни и те же дни.
        # По `already_running` окно понимает, что его пересинхронизация не началась.
        return {
            "run_id": str(running.id),
            "status": running.status.value,
            "mode": running.mode,
            "already_running": True,
        }
    run = SyncRun(
        connection_id=connection.id,
        mode=mode,
        status=SyncStatus.queued,
        details={
            "phase": "queued",
            "source": "manual",
            **({"days": days} if mode == "resync" else {}),
        },
    )
    db.add(run)
    await db.flush()
    response = {"run_id": str(run.id), "status": run.status.value}
    if idempotency_key:
        db.add(
            IdempotencyRecord(
                workspace_id=current.workspace_id,
                key=idempotency_key,
                operation="keitaro.sync",
                response=response,
            )
        )
    await audit(
        db,
        current,
        "integration.sync_started",
        f"Started {mode} sync for {connection.name}"
        + (f" ({days} days)" if mode == "resync" else ""),
        request=request,
        entity_id=str(connection.id),
    )
    await db.commit()
    sync_keitaro_connection.delay(str(connection.id), str(run.id), mode, days)
    return response


@router.get("/keitaro/{connection_id}/runs", response_model=Page)
async def list_sync_runs(
    connection_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.view")),
) -> Page:
    connection = await db.get(IntegrationConnection, connection_id)
    if not connection or connection.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    total = await db.scalar(
        select(func.count()).select_from(SyncRun).where(SyncRun.connection_id == connection_id)
    )
    rows = list(
        (
            await db.execute(
                select(SyncRun)
                .where(SyncRun.connection_id == connection_id)
                .order_by(SyncRun.created_at.desc())
                .limit(min(limit, 100))
                .offset(offset)
            )
        ).scalars()
    )
    items = [
        {
            "id": str(row.id),
            "status": row.status.value,
            "mode": row.mode,
            "progress_pct": row.progress_pct,
            "rows_processed": row.rows_processed,
            "error": row.error,
            "details": row.details,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }
        for row in rows
    ]
    return Page(items=items, total=total or 0, limit=min(limit, 100), offset=offset)


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
