import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.core.security import decrypt_secret, encrypt_secret
from app.models import (
    FinanceRecord,
    IdempotencyRecord,
    IntegrationConnection,
    KeitaroCampaign,
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
from app.workers.tasks import sync_keitaro_connection

router = APIRouter(prefix="/integrations", tags=["integrations"])


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
                    .order_by(SyncRun.started_at.desc())
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
                .order_by(SyncRun.started_at.desc())
            )
        ).scalars()
    )
    latest_by_connection: dict[uuid.UUID, SyncRun] = {}
    for run in runs:
        latest_by_connection.setdefault(run.connection_id, run)

    running = next(
        (
            run
            for run in runs
            if run.status in {SyncStatus.queued, SyncStatus.running}
        ),
        None,
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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    if mode not in {"incremental", "backfill"}:
        raise HTTPException(status_code=422, detail="Mode must be incremental or backfill")
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
        f"Started {mode} sync for {connection.name}",
        request=request,
        entity_id=str(connection.id),
    )
    await db.commit()
    sync_keitaro_connection.delay(str(connection.id), str(run.id), mode)
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
                .order_by(SyncRun.started_at.desc())
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
