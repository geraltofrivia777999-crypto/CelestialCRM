import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import accessible_user_ids, has_full_access, require_permission
from app.models import (
    CapRule,
    CountryTier,
    FinanceRecord,
    KeitaroCampaign,
    MediaRecord,
    Offer,
    OfferBuyer,
    OfferLead,
    OfferStatus,
    Partner,
    Status,
    User,
)
from app.schemas import (
    AssignBuyers,
    AssignLeads,
    CatalogStatusUpdate,
    CountryTiersIn,
    OfferIn,
    OfferOut,
    OfferStarUpdate,
    OfferStatusUpdate,
    Page,
)
from app.services.audit import audit
from app.services.country_tiers import TIER_1
from app.services.finance_pull import pull_offer_to_books
from app.services.geo import countries, normalize_geo

MAX_PAGE_SIZE = 500

router = APIRouter(tags=["references"])


@router.get("/partners", response_model=Page)
async def list_partners(
    search: str | None = None,
    status: Status | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    # Партнёрка здесь — справочник фильтра Медиаборда, а не отдельный модуль.
    # Поэтому отдельного partners.view больше нет: доступ совпадает с доской.
    current: User = Depends(require_permission("media.view")),
) -> Page:
    filters = [Partner.workspace_id == current.workspace_id]
    if search:
        filters.append(Partner.name.ilike(f"%{search}%"))
    if status:
        filters.append(Partner.status == status)
    total = await db.scalar(select(func.count()).select_from(Partner).where(*filters))
    rows = list(
        (
            await db.execute(
                select(Partner)
                .where(*filters)
                .order_by(Partner.name)
                .limit(min(limit, MAX_PAGE_SIZE))
                .offset(offset)
            )
        ).scalars()
    )
    offer_counts = dict(
        (
            await db.execute(
                select(Offer.partner_id, func.count(Offer.id))
                .where(Offer.partner_id.in_([row.id for row in rows]))
                .group_by(Offer.partner_id)
            )
        ).all()
    ) if rows else {}
    return Page(
        items=[
            {
                "id": str(row.id),
                "external_id": row.external_id,
                "name": row.name,
                "status": row.status.value,
                "status_overridden": row.status_overridden,
                "offers_count": offer_counts.get(row.id, 0),
            }
            for row in rows
        ],
        total=total or 0,
        limit=min(limit, MAX_PAGE_SIZE),
        offset=offset,
    )


@router.patch("/partners/{partner_id}/status")
async def set_partner_status(
    partner_id: uuid.UUID,
    payload: CatalogStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    partner = await db.get(Partner, partner_id)
    if not partner or partner.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Partner not found")
    if payload.status not in {Status.active, Status.inactive}:
        raise HTTPException(status_code=422, detail="Unsupported partner status")
    partner.status = payload.status
    partner.status_overridden = True
    await audit(
        db,
        current,
        "partner.status_changed",
        f"Changed {partner.name} status to {payload.status.value}",
        request=request,
        entity_type="partner",
        entity_id=str(partner.id),
    )
    await db.commit()
    return {
        "id": str(partner.id),
        "status": partner.status.value,
        "status_overridden": partner.status_overridden,
    }


@router.get("/campaigns", response_model=Page)
async def list_campaigns(
    search: str | None = None,
    group: str | None = None,
    status: Status | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("media.view")),
) -> Page:
    filters = [KeitaroCampaign.workspace_id == current.workspace_id]
    if search:
        filters.append(KeitaroCampaign.name.ilike(f"%{search}%"))
    if group:
        filters.append(KeitaroCampaign.group_name == group)
    if status:
        filters.append(KeitaroCampaign.status == status)
    total = await db.scalar(
        select(func.count()).select_from(KeitaroCampaign).where(*filters)
    )
    rows = list(
        (
            await db.execute(
                select(KeitaroCampaign)
                .where(*filters)
                .order_by(KeitaroCampaign.name)
                .limit(min(limit, MAX_PAGE_SIZE))
                .offset(offset)
            )
        ).scalars()
    )
    return Page(
        items=[
            {
                "id": str(row.id),
                "external_id": row.external_id,
                "name": row.name,
                "group_name": row.group_name,
                "traffic_source_external_id": row.traffic_source_external_id,
                "cost_type": row.cost_type,
                "status": row.status.value,
            }
            for row in rows
        ],
        total=total or 0,
        limit=min(limit, MAX_PAGE_SIZE),
        offset=offset,
    )


async def _buyer_offer_scope(
    db: AsyncSession, current: User, for_buyer_id: uuid.UUID | None
):
    """«Оффера этого человека» — его группа Keitaro плюс назначенное ему лично.

    Назначений теперь два: оффер сначала отдают тимлиду, потом тимлид раздаёт
    его баерам, — поэтому в область видимости входят обе связи.

    Без `for_buyer_id` берётся вся видимая ветка: баер видит свои офферы, тимлид —
    свои и офферы своих баеров. Полный доступ не сужаем: администратор ведёт
    справочник целиком, и офферы, за которыми никто не закреплён, ему тоже нужны.
    """
    if for_buyer_id:
        visible = await accessible_user_ids(db, current)
        if for_buyer_id not in visible:
            raise HTTPException(status_code=404, detail="User not found")
        user_ids = {for_buyer_id}
    else:
        if await has_full_access(db, current):
            return None
        user_ids = await accessible_user_ids(db, current)
    groups = set(
        (
            await db.scalars(
                select(func.lower(User.keitaro_offer_group))
                .where(User.id.in_(user_ids), User.keitaro_offer_group.isnot(None))
            )
        ).all()
    )
    groups.discard("")
    conditions = [
        Offer.id.in_(select(OfferBuyer.offer_id).where(OfferBuyer.user_id.in_(user_ids))),
        Offer.id.in_(select(OfferLead.offer_id).where(OfferLead.user_id.in_(user_ids))),
    ]
    if groups:
        conditions.append(func.lower(func.trim(Offer.group_name)).in_(groups))
    return or_(*conditions)


async def _people(db: AsyncSession, offer_ids: list[uuid.UUID]) -> tuple[dict, dict]:
    """Тимлиды и баеры сразу по всем офферам страницы — двумя запросами."""
    if not offer_ids:
        return {}, {}
    result: list[dict[uuid.UUID, list[dict]]] = []
    for table in (OfferLead, OfferBuyer):
        rows = (
            await db.execute(
                select(table.offer_id, User.id, User.name)
                .join(User, User.id == table.user_id)
                .where(table.offer_id.in_(offer_ids))
                .order_by(User.name)
            )
        ).all()
        grouped: dict[uuid.UUID, list[dict]] = {}
        for offer_id, user_id, name in rows:
            grouped.setdefault(offer_id, []).append({"id": str(user_id), "name": name})
        result.append(grouped)
    return result[0], result[1]


# Холд и Стоп ставит человек, и переназначение их не трогает: оффер могли
# остановить по требованию партнёрки, а баеры при этом остаются на месте.
MANUAL_STATUSES = {OfferStatus.hold, OfferStatus.stop}


def workflow_status(
    current: OfferStatus, has_leads: bool, has_buyers: bool
) -> OfferStatus:
    """Статус, который следует из назначений: ничей → тимлид → баеры."""
    if current in MANUAL_STATUSES:
        return current
    if has_buyers:
        return OfferStatus.working
    if has_leads:
        return OfferStatus.active
    return OfferStatus.free


async def _refresh_status(db: AsyncSession, offer: Offer) -> None:
    leads = await db.scalar(
        select(func.count()).select_from(OfferLead).where(OfferLead.offer_id == offer.id)
    )
    buyers = await db.scalar(
        select(func.count()).select_from(OfferBuyer).where(OfferBuyer.offer_id == offer.id)
    )
    offer.status = workflow_status(offer.status, bool(leads), bool(buyers))


async def _manual_offer(db: AsyncSession, current: User, offer_id: uuid.UUID) -> Offer:
    """Оффер, который CRM имеет право менять целиком.

    Синхронизированную строку править нельзя: имя, GEO и партнёрку ей всё
    равно перезапишет следующая синхронизация Keitaro.
    """
    offer = await db.get(Offer, offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Offer not found")
    if offer.connection_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Оффер синхронизирован из Keitaro — его поля меняются в трекере.",
        )
    return offer


async def _valid_users(
    db: AsyncSession, current: User, ids: list[uuid.UUID]
) -> list[User]:
    wanted = set(ids)
    if not wanted:
        return []
    users = list(
        (
            await db.execute(
                select(User).where(
                    User.workspace_id == current.workspace_id, User.id.in_(wanted)
                )
            )
        ).scalars()
    )
    if len(users) != len(wanted):
        raise HTTPException(status_code=422, detail="One or more users are invalid")
    return users


@router.get("/offers", response_model=Page)
async def list_offers(
    search: str | None = None,
    geo: str | None = None,
    partner_id: uuid.UUID | None = None,
    buyer_id: uuid.UUID | None = None,
    status: OfferStatus | None = None,
    group: str | None = None,
    only_offers_group: bool = False,
    exclude_offers_group: bool = False,
    manual: bool | None = None,
    scope_offers: bool = False,
    for_buyer_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    starred: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.view")),
) -> Page:
    filters = [Offer.workspace_id == current.workspace_id]
    if search:
        filters.append(Offer.name.ilike(f"%{search}%"))
    if geo:
        filters.append(Offer.geo == normalize_geo(geo))
    if partner_id:
        filters.append(Offer.partner_id == partner_id)
    if status:
        filters.append(Offer.status == status)
    if starred is not None:
        filters.append(Offer.is_starred.is_(starred))
    # Раздел «Оффера» ведёт только свои строки; у синхронизированных заполнен
    # `connection_id`, и их поля принадлежат трекеру.
    if manual is not None:
        filters.append(
            Offer.connection_id.is_(None) if manual else Offer.connection_id.isnot(None)
        )
    # The "Оффера" module only works with one Keitaro group; the campaign and
    # media filters still need every offer, so the scope is opt-in per request.
    wanted_group = group or (settings.keitaro_offers_group if only_offers_group else None)
    if wanted_group:
        filters.append(func.lower(Offer.group_name) == wanted_group.strip().lower())
    # Медиаборд, наоборот, эту группу не показывает: она принадлежит модулю
    # «Оффера» — как и ручные офферы, у которых своего трафика в Keitaro нет.
    if exclude_offers_group:
        filters.append(
            func.lower(func.coalesce(Offer.group_name, ""))
            != settings.keitaro_offers_group.strip().lower()
        )
        filters.append(Offer.connection_id.isnot(None))
    if scope_offers or for_buyer_id:
        scope = await _buyer_offer_scope(db, current, for_buyer_id)
        if scope is not None:
            filters.append(scope)
    stmt = select(Offer).where(*filters)
    if buyer_id:
        stmt = stmt.join(OfferBuyer).where(OfferBuyer.user_id == buyer_id)
    if lead_id:
        stmt = stmt.where(
            Offer.id.in_(select(OfferLead.offer_id).where(OfferLead.user_id == lead_id))
        )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    offers = list(
        (
            await db.execute(
                # `id` is the tie-breaker: names repeat, and without a total
                # order LIMIT/OFFSET pages overlap.
                stmt.order_by(Offer.is_starred.desc(), Offer.name, Offer.id)
                .limit(min(limit, MAX_PAGE_SIZE))
                .offset(offset)
            )
        ).scalars()
    )
    offer_ids = [offer.id for offer in offers]
    leads, buyers = await _people(db, offer_ids)
    # Капа держит офферы списком в JSON, поэтому считаем на стороне Python:
    # выражения по массиву в JSON расходятся между PostgreSQL и SQLite.
    caps: dict[uuid.UUID, int] = {}
    if offer_ids:
        wanted = set(offer_ids)
        rows = await db.scalars(
            select(CapRule.offer_ids).where(CapRule.workspace_id == current.workspace_id)
        )
        for stored in rows:
            for value in stored or []:
                try:
                    offer_uuid = uuid.UUID(str(value))
                except (TypeError, ValueError):
                    continue
                if offer_uuid in wanted:
                    caps[offer_uuid] = caps.get(offer_uuid, 0) + 1
    return Page(
        items=[
            {
                **OfferOut.model_validate(offer).model_dump(mode="json"),
                # Rows synced before GEO normalization still hold full country
                # names, so the code is resolved on read as well.
                "geo": normalize_geo(offer.geo),
                "partner": offer.partner.name if offer.partner else None,
                "partner_id": str(offer.partner_id) if offer.partner_id else None,
                "is_manual": offer.connection_id is None,
                "leads": leads.get(offer.id, []),
                "buyers": buyers.get(offer.id, []),
                "caps_count": caps.get(offer.id, 0),
            }
            for offer in offers
        ],
        total=total or 0,
        limit=min(limit, MAX_PAGE_SIZE),
        offset=offset,
    )


@router.get("/offers/reference")
async def offers_reference(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.view")),
) -> dict:
    """Списки для формы оффера.

    GEO и партнёрки берутся из того, что синхронизировано из Keitaro: сам
    оффер заводится руками, но справочники остаются трекерными, чтобы GEO
    и названия партнёрок совпадали с Медиабордом и Финансами.
    """
    geo_rows = (
        await db.scalars(
            select(Offer.geo)
            .where(Offer.workspace_id == current.workspace_id, Offer.geo.isnot(None))
            .distinct()
        )
    ).all()
    geos = sorted({normalize_geo(value) for value in geo_rows} - {None})
    partners = (
        await db.execute(
            select(Partner.id, Partner.name)
            .where(Partner.workspace_id == current.workspace_id)
            .order_by(Partner.name)
        )
    ).all()
    return {
        "geos": geos,
        "partners": [{"id": str(row_id), "name": name} for row_id, name in partners],
        "statuses": [status.value for status in OfferStatus],
    }


@router.post("/offers", response_model=dict, status_code=201)
async def create_offer(
    payload: OfferIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    leads = await _valid_users(db, current, payload.lead_ids)
    buyers = await _valid_users(db, current, payload.buyer_ids)
    geo = normalize_geo(payload.geo)
    offer = Offer(
        workspace_id=current.workspace_id,
        name=payload.name.strip(),
        geo=geo[:12] if geo else None,
        cap=(payload.cap or "").strip() or None,
        cpa=payload.cpa,
        cpa_currency=payload.cpa_currency,
        kpi=(payload.kpi or "").strip() or None,
        comment=(payload.comment or "").strip() or None,
        partner_id=payload.partner_id,
        status=workflow_status(OfferStatus.free, bool(leads), bool(buyers)),
    )
    db.add(offer)
    await db.flush()
    db.add_all([OfferLead(offer_id=offer.id, user_id=user.id) for user in leads])
    db.add_all([OfferBuyer(offer_id=offer.id, user_id=user.id) for user in buyers])
    await pull_offer_to_books(db, offer, [user.id for user in buyers])
    await audit(
        db,
        current,
        "offer.created",
        f"Created offer {offer.name}",
        request=request,
        entity_type="offer",
        entity_id=str(offer.id),
    )
    await db.commit()
    return {"id": str(offer.id), "status": offer.status.value}


@router.put("/offers/{offer_id}")
async def update_offer(
    offer_id: uuid.UUID,
    payload: OfferIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    offer = await _manual_offer(db, current, offer_id)
    leads = await _valid_users(db, current, payload.lead_ids)
    buyers = await _valid_users(db, current, payload.buyer_ids)
    geo = normalize_geo(payload.geo)
    offer.name = payload.name.strip()
    offer.geo = geo[:12] if geo else None
    offer.cap = (payload.cap or "").strip() or None
    offer.cpa = payload.cpa
    offer.cpa_currency = payload.cpa_currency
    offer.kpi = (payload.kpi or "").strip() or None
    offer.comment = (payload.comment or "").strip() or None
    offer.partner_id = payload.partner_id
    await db.execute(delete(OfferLead).where(OfferLead.offer_id == offer.id))
    await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id == offer.id))
    db.add_all([OfferLead(offer_id=offer.id, user_id=user.id) for user in leads])
    db.add_all([OfferBuyer(offer_id=offer.id, user_id=user.id) for user in buyers])
    await pull_offer_to_books(db, offer, [user.id for user in buyers])
    offer.status = workflow_status(offer.status, bool(leads), bool(buyers))
    await audit(
        db,
        current,
        "offer.updated",
        f"Updated offer {offer.name}",
        request=request,
        entity_type="offer",
        entity_id=str(offer.id),
    )
    await db.commit()
    return {"id": str(offer.id), "status": offer.status.value}


@router.delete("/offers/{offer_id}")
async def delete_offer(
    offer_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    offer = await _manual_offer(db, current, offer_id)
    # Записи Медиаборда и Финансов ссылаются на оффер без каскада — удалять
    # такой оффер значит потерять строки отчётности.
    used = await db.scalar(
        select(func.count()).select_from(MediaRecord).where(MediaRecord.offer_id == offer.id)
    ) or await db.scalar(
        select(func.count())
        .select_from(FinanceRecord)
        .where(FinanceRecord.offer_id == offer.id)
    )
    if used:
        raise HTTPException(
            status_code=409,
            detail="По офферу есть записи в Медиаборде или Финансах — поставьте «Стоп».",
        )
    name = offer.name
    await db.execute(delete(OfferLead).where(OfferLead.offer_id == offer.id))
    await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id == offer.id))
    await db.delete(offer)
    await audit(
        db,
        current,
        "offer.deleted",
        f"Deleted offer {name}",
        request=request,
        entity_type="offer",
        entity_id=str(offer_id),
    )
    await db.commit()
    return {"deleted": str(offer_id)}


@router.patch("/offers/{offer_id}/status")
async def set_offer_status(
    offer_id: uuid.UUID,
    payload: OfferStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    offer = await db.get(Offer, offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Offer not found")
    offer.status = payload.status
    await audit(
        db,
        current,
        "offer.status_changed",
        f"Changed {offer.name} status to {payload.status.value}",
        request=request,
        entity_type="offer",
        entity_id=str(offer.id),
    )
    await db.commit()
    return {"id": str(offer.id), "status": offer.status.value}


@router.patch("/offers/{offer_id}/star")
async def set_offer_star(
    offer_id: uuid.UUID,
    payload: OfferStarUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    offer = await db.get(Offer, offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Offer not found")
    offer.is_starred = payload.is_starred
    await audit(
        db,
        current,
        "offer.star_changed",
        ("Starred " if payload.is_starred else "Unstarred ") + offer.name,
        request=request,
        entity_type="offer",
        entity_id=str(offer.id),
    )
    await db.commit()
    return {"id": str(offer.id), "is_starred": offer.is_starred}


@router.put("/offers/{offer_id}/leads")
async def assign_leads(
    offer_id: uuid.UUID,
    payload: AssignLeads,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    """Первая ступень: оффер отдают тимлиду и он становится «Активен»."""
    offer = await db.get(Offer, offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Offer not found")
    users = await _valid_users(db, current, payload.lead_ids)
    await db.execute(delete(OfferLead).where(OfferLead.offer_id == offer.id))
    db.add_all([OfferLead(offer_id=offer.id, user_id=user.id) for user in users])
    await db.flush()
    await _refresh_status(db, offer)
    await audit(
        db,
        current,
        "offer.leads_changed",
        f"Assigned {len(users)} team leads to {offer.name}",
        request=request,
        entity_type="offer",
        entity_id=str(offer.id),
    )
    await db.commit()
    return {
        "offer_id": str(offer.id),
        "lead_ids": [str(user.id) for user in users],
        "status": offer.status.value,
    }


@router.put("/offers/{offer_id}/buyers")
async def assign_buyers(
    offer_id: uuid.UUID,
    payload: AssignBuyers,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    """Вторая ступень: тимлид раздаёт оффер баерам и он уходит «В работу»."""
    offer = await db.get(Offer, offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Offer not found")
    users = await _valid_users(db, current, payload.buyer_ids)
    await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id == offer.id))
    db.add_all([OfferBuyer(offer_id=offer.id, user_id=user.id) for user in users])
    await db.flush()
    # Оффер уезжает в Финансы баера сразу: заводить ту же строку руками —
    # лишняя работа и повод разойтись со справочником в названии или ставке.
    # Снятый баер свою строку сохраняет: в ней уже могут быть депозиты.
    pulled = await pull_offer_to_books(db, offer, [user.id for user in users])
    # Статус идёт следом за назначениями: снятый последний баер возвращает
    # оффер тимлиду, снятый последний тимлид — в «Не занят». Холд и Стоп
    # выставлены руками и здесь не сбрасываются.
    await _refresh_status(db, offer)
    await audit(
        db,
        current,
        "offer.buyers_changed",
        f"Assigned {len(users)} buyers to {offer.name}",
        request=request,
        entity_type="offer",
        entity_id=str(offer.id),
    )
    await db.commit()
    return {
        "offer_id": str(offer.id),
        "buyer_ids": [str(user.id) for user in users],
        "status": offer.status.value,
        "pulled_to_finance": [str(buyer_id) for buyer_id in pulled],
    }


@router.get("/country-tiers")
async def country_tiers(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.view")),
) -> dict:
    """Две таблицы стран: Tier1 и всё остальное.

    Tier2/3 не хранится строками — это «все прочие». Считать его на клиенте
    было бы дублированием правила, а разъезжаются такие дубли молча.
    """
    rows = await db.execute(
        select(CountryTier.code).where(
            CountryTier.workspace_id == current.workspace_id,
            CountryTier.tier == TIER_1,
        )
    )
    tier_one = {code for (code,) in rows}
    known = countries()
    return {
        "tier1": [row for row in known if row["code"] in tier_one],
        "tier23": [row for row in known if row["code"] not in tier_one],
        # Коды, которых нет в нашем справочнике стран, всё равно показываем:
        # они уже проставлены офферам и молча пропасть не должны.
        "unknown": sorted(tier_one - {row["code"] for row in known}),
    }


@router.put("/country-tiers")
async def save_country_tiers(
    payload: CountryTiersIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> dict:
    """Переписать список Tier1 целиком.

    Приходит весь набор, а не «добавь одну»: список правят пачкой, и разбор
    того, что именно изменилось, ничего бы здесь не сэкономил.
    """
    codes = sorted(
        {code for code in (normalize_geo(item) for item in payload.tier1) if code}
    )
    await db.execute(
        delete(CountryTier).where(CountryTier.workspace_id == current.workspace_id)
    )
    for code in codes:
        db.add(CountryTier(workspace_id=current.workspace_id, code=code, tier=TIER_1))
    await audit(
        db,
        current,
        "settings.country_tiers_saved",
        f"Tier1: {len(codes)} стран",
        request=request,
    )
    await db.commit()
    known = countries()
    tier_one = set(codes)
    return {
        "tier1": [row for row in known if row["code"] in tier_one],
        "tier23": [row for row in known if row["code"] not in tier_one],
        "unknown": sorted(tier_one - {row["code"] for row in known}),
    }
