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

from app.core.config import settings
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
    normalize_partner_url,
    platform_template_id,
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
        "platform": integration.platform,
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
    platform = str(payload.get("platform") or "").strip().lower()
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not partner_name or not platform or not base_url or not api_key:
        raise HTTPException(
            status_code=422,
            detail="Заполните партнёрку, платформу, адрес API и ключ",
        )
    try:
        base_url = normalize_partner_url(base_url)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    template_id = platform_template_id(platform)
    if template_id is None:
        raise HTTPException(status_code=422, detail="Неизвестная платформа партнёрки")
    # Название — просто подпись; на сервисе оно же служит слагом, поэтому
    # уникальность в воркспейсе обеспечиваем здесь.
    name = await _free_name(db, current.workspace_id, partner_name)
    integration = PartnerIntegration(
        workspace_id=current.workspace_id,
        name=name,
        partner_name=partner_name,
        platform=platform,
        base_url=base_url,
        api_key_encrypted=encrypt_secret(api_key),
        is_enabled=payload.get("is_enabled", True),
    )
    integration.external_id = await _create_on_service(
        partner_name, name, template_id, base_url, api_key
    )
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


async def _create_on_service(
    partner_name: str, name: str, template_id: int, base_url: str, api_key: str
) -> str:
    """Завести интеграцию на сервисе и вернуть её id.

    Ошибку сервиса поднимаем наверх, а не глотаем: без интеграции на его
    стороне синк не заработает, и карточка, сохранённая «наполовину», выглядела
    бы рабочей, ничего не привозя.
    """
    try:
        created = await PartnerServiceClient().create_integration(
            partner_name=partner_name,
            name=name,
            template_integration_id=template_id,
            base_url=base_url,
            api_key=api_key,
        )
    except PartnerIntegrationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    external_id = created.get("id")
    if external_id is None:
        raise HTTPException(
            status_code=422,
            detail="Сервис партнёрок не вернул id интеграции",
        )
    return str(external_id)


@router.patch("/{integration_id}")
async def update_integration(
    integration_id: uuid.UUID,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    integration = await _integration(db, current, integration_id)
    updated_base_url: str | None = None
    updated_api_key: str | None = None
    if "base_url" in payload:
        try:
            updated_base_url = normalize_partner_url(payload["base_url"])
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    if "api_key" in payload:
        updated_api_key = str(payload["api_key"] or "").strip()
        if not updated_api_key:
            raise HTTPException(status_code=422, detail="Ключ API не может быть пустым")

    # Сначала обновляем рабочую интеграцию в сервисе. Иначе карточка CRM
    # показывает новый адрес, а синк продолжает ходить по старому.
    effective_external_id = (
        str(payload.get("external_id") or "").strip()
        if "external_id" in payload
        else str(integration.external_id or "").strip()
    )
    if effective_external_id and (
        updated_base_url is not None or updated_api_key is not None
    ):
        try:
            await PartnerServiceClient().update_integration(
                effective_external_id,
                base_url=updated_base_url,
                api_key=updated_api_key,
            )
        except PartnerIntegrationError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    if "name" in payload and str(payload["name"]).strip():
        integration.name = str(payload["name"]).strip()
    if "partner_name" in payload and str(payload["partner_name"]).strip():
        renamed = str(payload["partner_name"]).strip()
        # Название — подпись партнёрки, отдельного поля для него больше нет:
        # переименовали партнёрку — переименовалась и интеграция.
        if "name" not in payload and integration.name == integration.partner_name:
            integration.name = await _free_name(db, current.workspace_id, renamed)
        integration.partner_name = renamed
    if "platform" in payload and str(payload["platform"]).strip():
        platform = str(payload["platform"]).strip().lower()
        if platform_template_id(platform) is None:
            raise HTTPException(status_code=422, detail="Неизвестная платформа партнёрки")
        integration.platform = platform
    if updated_base_url is not None:
        integration.base_url = updated_base_url
    if "external_id" in payload:
        integration.external_id = str(payload["external_id"] or "").strip() or None
    if updated_api_key is not None:
        integration.api_key_encrypted = encrypt_secret(updated_api_key)
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


PLATFORM_LABELS = {"affise": "Affise", "alanbase": "Alanbase", "afftech": "AffTech"}


@router.get("/platforms")
async def list_platforms(
    current: User = Depends(require_permission("settings.view")),
) -> list[dict]:
    """Платформы, под которые у сервиса есть готовый шаблон коннектора.

    Список отдаёт бэкенд, а не зашивает форма: соответствие «платформа →
    шаблон» живёт в настройках развёртывания, и добавление четвёртой ПП не
    должно требовать правки интерфейса.
    """
    return [
        {"value": key, "label": PLATFORM_LABELS.get(key, key.title())}
        for key in settings.partner_platform_templates
    ]


@router.get("/service/integrations")
async def service_integrations(
    integration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.view")),
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
