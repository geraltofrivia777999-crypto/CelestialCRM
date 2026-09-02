"""Интеграция с Partner Integration Service — маршруты управления.

Раздел «Настройки»: карточки интеграций с ПП, привязки офферов, запуск синка
вручную и история прогонов. Данные в финансы раскладывает отдельный сервис
(`partner_deposits`) — здесь только управление и чтение.
"""

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.security import encrypt_secret
from app.models import (
    Offer,
    PartnerIntegration,
    PartnerPendingTag,
    PartnerSyncRun,
    User,
)
from app.services.audit import audit
from app.services.partner_integrations import (
    PartnerIntegrationError,
    PartnerServiceClient,
)
from app.services.partner_sync import (
    fail_run,
    finish_run,
    mapped_offers,
    perform_sync,
)

router = APIRouter(prefix="/partner-integrations", tags=["partner-integrations"])


def _serialize(integration: PartnerIntegration) -> dict:
    return {
        "id": str(integration.id),
        "name": integration.name,
        "partner_name": integration.partner_name,
        "base_url": integration.base_url,
        "external_id": integration.external_id,
        "is_enabled": integration.is_enabled,
        "last_sync_at": integration.last_sync_at,
        "last_sync_status": integration.last_sync_status,
        "last_sync_error": integration.last_sync_error,
        "created_at": integration.created_at,
    }


async def _integration(
    db: AsyncSession, current: User, integration_id: uuid.UUID
) -> PartnerIntegration:
    integration = await db.get(PartnerIntegration, integration_id)
    if not integration or integration.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Интеграция не найдена")
    return integration


@router.get("")
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.view")),
) -> list[dict]:
    rows = list(
        (
            await db.execute(
                select(PartnerIntegration)
                .where(PartnerIntegration.workspace_id == current.workspace_id)
                .order_by(PartnerIntegration.created_at)
            )
        ).scalars()
    )
    # Привязки офферов живут в самих офферах: показываем, сколько их заведено,
    # чтобы из настроек было видно, есть ли вообще чему приезжать. У каждой
    # интеграции свои: номер оффера принадлежит одной партнёрке.
    return [
        {
            **_serialize(row),
            "offers": [
                {
                    "id": str(offer.id),
                    "name": offer.name,
                    "external_offer_id": offer.external_id,
                }
                for offer in await mapped_offers(db, row)
            ],
        }
        for row in rows
    ]


@router.post("", status_code=201)
async def create_integration(
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    partner_name = str(payload.get("partner_name") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not partner_name or not base_url or not api_key:
        raise HTTPException(
            status_code=422,
            detail="Заполните партнёрку, адрес сервиса и API-ключ",
        )
    # Название и ID интеграции на сервисе руками больше не вводят: первое —
    # просто подпись, второе спрашиваем у самого сервиса.
    name = await _free_name(db, current.workspace_id, partner_name)
    integration = PartnerIntegration(
        workspace_id=current.workspace_id,
        name=name,
        partner_name=partner_name,
        base_url=base_url,
        api_key_encrypted=encrypt_secret(api_key),
        is_enabled=payload.get("is_enabled", True),
    )
    integration.external_id = str(payload.get("external_id") or "").strip() or None
    if not integration.external_id:
        integration.external_id = await _service_integration_id(integration, partner_name)
    db.add(integration)
    await audit(
        db, current, "partner.integration_created",
        f"Создана интеграция с ПП «{partner_name}» ({name})",
        request=request, entity_id=str(integration.id),
    )
    await db.commit()
    await db.refresh(integration)
    return _serialize(integration)


async def _free_name(db: AsyncSession, workspace_id: uuid.UUID, base: str) -> str:
    """Название интеграции = имя партнёрки; при совпадении добавляем номер."""
    taken = set(
        await db.scalars(
            select(PartnerIntegration.name).where(
                PartnerIntegration.workspace_id == workspace_id
            )
        )
    )
    if base not in taken:
        return base[:120]
    for suffix in range(2, 100):
        candidate = f"{base} {suffix}"[:120]
        if candidate not in taken:
            return candidate
    raise HTTPException(status_code=422, detail="Слишком много интеграций с таким именем")


async def _service_integration_id(
    integration: PartnerIntegration, partner_name: str
) -> str | None:
    """Спросить у сервиса, какая из его интеграций наша.

    Конфиг коннектора заводят на самом сервисе, и раньше его id вбивали руками.
    Сервис умеет перечислять свои интеграции — берём совпадение по имени, а
    если она там одна, то её. Не ответил или не нашли — не беда: синк тогда
    читает уже собранное сервисом, а id можно связать позже.
    """
    try:
        rows = await PartnerServiceClient(integration).integrations()
    except (PartnerIntegrationError, HTTPException):
        return None
    wanted = partner_name.strip().lower()
    for row in rows:
        names = {
            str(row.get("partner_name") or "").strip().lower(),
            str(row.get("name") or "").strip().lower(),
        }
        if wanted and wanted in names and row.get("id") is not None:
            return str(row["id"])
    if len(rows) == 1 and rows[0].get("id") is not None:
        return str(rows[0]["id"])
    return None


@router.patch("/{integration_id}")
async def update_integration(
    integration_id: uuid.UUID,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    integration = await _integration(db, current, integration_id)
    if "name" in payload and str(payload["name"]).strip():
        integration.name = str(payload["name"]).strip()
    if "partner_name" in payload and str(payload["partner_name"]).strip():
        renamed = str(payload["partner_name"]).strip()
        # Название — подпись партнёрки, отдельного поля для него больше нет:
        # переименовали партнёрку — переименовалась и интеграция.
        if "name" not in payload and integration.name == integration.partner_name:
            integration.name = await _free_name(db, current.workspace_id, renamed)
        integration.partner_name = renamed
    if "base_url" in payload and str(payload["base_url"]).strip():
        integration.base_url = str(payload["base_url"]).strip()
    if "external_id" in payload:
        integration.external_id = str(payload["external_id"] or "").strip() or None
    if "api_key" in payload and str(payload["api_key"]).strip():
        integration.api_key_encrypted = encrypt_secret(str(payload["api_key"]).strip())
    if "is_enabled" in payload:
        integration.is_enabled = bool(payload["is_enabled"])
    await audit(
        db, current, "partner.integration_updated",
        f"Изменена интеграция с ПП «{integration.partner_name}»",
        request=request, entity_id=str(integration.id),
    )
    await db.commit()
    await db.refresh(integration)
    return _serialize(integration)


@router.delete("/{integration_id}")
async def delete_integration(
    integration_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    integration = await _integration(db, current, integration_id)
    name = integration.partner_name
    await db.delete(integration)
    await audit(
        db, current, "partner.integration_deleted",
        f"Удалена интеграция с ПП «{name}»",
        request=request, entity_id=str(integration_id),
    )
    await db.commit()
    return {"deleted": str(integration_id)}


@router.post("/{integration_id}/sync")
async def run_sync(
    integration_id: uuid.UUID,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    """Ручной синк за диапазон: сервис идёт в ПП, факты ложатся в финансы."""
    integration = await _integration(db, current, integration_id)
    try:
        date_from = date.fromisoformat(str(payload.get("date_from") or ""))
        date_to = date.fromisoformat(str(payload.get("date_to") or ""))
    except ValueError:
        raise HTTPException(status_code=422, detail="Даты: YYYY-MM-DD") from None
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="Конец раньше начала")
    run = PartnerSyncRun(
        integration_id=integration.id,
        date_from=date_from,
        date_to=date_to,
        status="running",
        trigger="manual",
    )
    db.add(run)
    integration.last_sync_at = datetime.now(UTC)
    integration.last_sync_status = "running"
    await db.commit()
    await db.refresh(run)

    client = PartnerServiceClient(integration)
    error: str | None = None
    result: dict = {}
    # id запоминаем до работы: откат сессии обесценивает объекты, и обратиться
    # к `run.id` после него значило бы уйти в ленивую загрузку.
    run_id = run.id
    try:
        result = await perform_sync(db, integration, client, date_from, date_to)
        finish_run(run, integration, result)
    except Exception as exc:  # noqa: BLE001 — прогон обязан закрыться в любом случае
        # Ловим всё, а не только сбой сервиса: раньше неожиданная ошибка
        # оставляла прогон навсегда в статусе «running».
        await db.rollback()
        run = await db.get(PartnerSyncRun, run_id)
        integration = await db.get(PartnerIntegration, integration_id)
        error = fail_run(run, integration, exc)
    await db.commit()
    if error:
        raise HTTPException(status_code=502, detail=error)
    await audit(
        db, current, "partner.sync",
        f"Синк ПП «{integration.partner_name}» за {date_from}—{date_to}: "
        f"записано {run.records_upserted}, без баера {run.records_pending}",
        request=request, entity_id=str(integration.id),
    )
    await db.commit()
    return {
        "status": "success",
        "records_upserted": run.records_upserted,
        "records_pending": run.records_pending,
        "records_skipped": run.records_skipped,
        "details": run.details or {},
        "run_id": str(run.id),
    }


@router.get("/service/integrations")
async def service_integrations(
    integration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> list[dict]:
    """Интеграции, заведённые на самом сервисе, — чтобы выбрать, с какой связать.

    Конфиг коннектора к ПП живёт на сервисе; CRM его не пересоздаёт, а
    ссылается на готовый по id.
    """
    integration = await _integration(db, current, integration_id)
    client = PartnerServiceClient(integration)
    try:
        rows = await client.integrations()
    except PartnerIntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "partner_name": str(row.get("partner_name") or ""),
        }
        for row in rows
        if row.get("id") is not None
    ]


@router.get("/{integration_id}/runs")
async def list_runs(
    integration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.view")),
) -> list[dict]:
    integration = await _integration(db, current, integration_id)
    runs = list(
        (
            await db.execute(
                select(PartnerSyncRun)
                .where(PartnerSyncRun.integration_id == integration.id)
                .order_by(PartnerSyncRun.created_at.desc())
                .limit(50)
            )
        ).scalars()
    )
    return [
        {
            "id": str(run.id),
            "date_from": run.date_from,
            "date_to": run.date_to,
            "status": run.status,
            "records_upserted": run.records_upserted,
            "error": run.error,
            "trigger": run.trigger,
            "created_at": run.created_at,
        }
        for run in runs
    ]


# --- реестр «тег → баер» -----------------------------------------------------
#
# Сервис партнёрок отдаёт тег сырой строкой и про наших людей ничего не знает.
# Кому принадлежит трафик, решает CRM — и это единственное, что не даёт
# депозиту разойтись по книгам всех баеров оффера.


@router.get("/tags/pending")
async def list_pending_tags(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.view")),
) -> list[dict]:
    """Теги, которым не нашлось строки в финансах.

    Это не настройка и не маппинг: разбирается такой тег тем, что его заводят
    в книге баера под нужным оффером. Список нужен, чтобы деньги не пропадали
    молча — синк с нулём записей иначе выглядит нормой.
    """
    rows = list(
        (
            await db.execute(
                select(PartnerPendingTag)
                .where(PartnerPendingTag.workspace_id == current.workspace_id)
                .order_by(PartnerPendingTag.last_seen_at.desc())
            )
        ).scalars()
    )
    offer_ids = {row.sample_offer_id for row in rows if row.sample_offer_id}
    offers = {
        offer.id: offer.name
        for offer in (
            await db.execute(select(Offer).where(Offer.id.in_(offer_ids or [uuid.uuid4()])))
        ).scalars()
    }
    return [
        {
            "id": str(row.id),
            "tag": row.tag,
            "reason": row.reason,
            "facts_count": row.facts_count,
            "deposits_total": float(row.deposits_total or 0),
            "offer_name": offers.get(row.sample_offer_id, ""),
            "last_seen_at": row.last_seen_at,
        }
        for row in rows
    ]
