import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_permission
from app.models import KeitaroCampaign, Offer, OfferBuyer, Partner, Status, User
from app.schemas import AssignBuyers, CatalogStatusUpdate, OfferOut, Page
from app.services.audit import audit

router = APIRouter(tags=["references"])


@router.get("/partners", response_model=Page)
async def list_partners(
    search: str | None = None,
    status: Status | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("partners.view")),
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
                .limit(min(limit, 100))
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
        limit=min(limit, 100),
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
                .limit(min(limit, 100))
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
        limit=min(limit, 100),
        offset=offset,
    )


@router.get("/offers", response_model=Page)
async def list_offers(
    search: str | None = None,
    geo: str | None = None,
    partner_id: uuid.UUID | None = None,
    buyer_id: uuid.UUID | None = None,
    status: Status | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.view")),
) -> Page:
    filters = [Offer.workspace_id == current.workspace_id]
    if search:
        filters.append(Offer.name.ilike(f"%{search}%"))
    if geo:
        filters.append(Offer.geo == geo.upper())
    if partner_id:
        filters.append(Offer.partner_id == partner_id)
    if status:
        filters.append(Offer.status == status)
    stmt = select(Offer).where(*filters)
    if buyer_id:
        stmt = stmt.join(OfferBuyer).where(OfferBuyer.user_id == buyer_id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    offers = list(
        (
            await db.execute(
                stmt.order_by(Offer.name).limit(min(limit, 100)).offset(offset)
            )
        ).scalars()
    )
    buyer_rows = (
        await db.execute(
            select(OfferBuyer.offer_id, User.id, User.name)
            .join(User, User.id == OfferBuyer.user_id)
            .where(OfferBuyer.offer_id.in_([offer.id for offer in offers]))
        )
    ).all() if offers else []
    buyers: dict[uuid.UUID, list[dict]] = {}
    for offer_id, user_id, name in buyer_rows:
        buyers.setdefault(offer_id, []).append({"id": str(user_id), "name": name})
    return Page(
        items=[
            {
                **OfferOut.model_validate(offer).model_dump(mode="json"),
                "partner": offer.partner.name if offer.partner else None,
                "buyers": buyers.get(offer.id, []),
            }
            for offer in offers
        ],
        total=total or 0,
        limit=min(limit, 100),
        offset=offset,
    )


@router.patch("/offers/{offer_id}/status")
async def set_offer_status(
    offer_id: uuid.UUID,
    payload: CatalogStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    offer = await db.get(Offer, offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Offer not found")
    if payload.status not in {Status.active, Status.inactive}:
        raise HTTPException(status_code=422, detail="Unsupported offer status")
    offer.status = payload.status
    offer.status_overridden = True
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
    return {
        "id": str(offer.id),
        "status": offer.status.value,
        "status_overridden": offer.status_overridden,
    }


@router.put("/offers/{offer_id}/buyers")
async def assign_buyers(
    offer_id: uuid.UUID,
    payload: AssignBuyers,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("offers.manage")),
) -> dict:
    offer = await db.get(Offer, offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Offer not found")
    users = list(
        (
            await db.execute(
                select(User).where(
                    User.workspace_id == current.workspace_id,
                    User.id.in_(set(payload.buyer_ids)),
                )
            )
        ).scalars()
    )
    if len(users) != len(set(payload.buyer_ids)):
        raise HTTPException(status_code=422, detail="One or more buyers are invalid")
    await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id == offer.id))
    db.add_all([OfferBuyer(offer_id=offer.id, user_id=user.id) for user in users])
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
    return {"offer_id": str(offer.id), "buyer_ids": [str(user.id) for user in users]}
