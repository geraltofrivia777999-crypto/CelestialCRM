import csv
import io
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import accessible_user_ids, require_permission
from app.models import (
    FinanceRecord,
    FinanceServiceValue,
    FinanceSpendValue,
    IdempotencyRecord,
    MediaRecord,
    MediaServiceValue,
    MediaSpendValue,
    Offer,
    OfferBuyer,
    Partner,
    Service,
    SpendProvider,
    Status,
    User,
)
from app.schemas import (
    MEDIA_MANUAL_FIELDS,
    DashboardSummary,
    FinanceRecordIn,
    FinanceValuesIn,
    MediaRecordIn,
    MediaValuesIn,
    Page,
)
from app.services.audit import audit
from app.services.formulas import (
    amount_with_commission,
    finance_import_key,
    finance_metrics,
    media_metrics,
    service_cost,
)

router = APIRouter(tags=["analytics"])


async def invalidate_dashboard_cache(workspace_id: uuid.UUID) -> None:
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        keys = await redis.keys(f"dashboard:{workspace_id}:*")
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
    except Exception:
        pass


def _media_filters(
    workspace_id: uuid.UUID,
    allowed_buyer_ids: set[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
    buyer_id: uuid.UUID | None,
    offer_id: uuid.UUID | None,
    geo: str | None = None,
    partner_id: uuid.UUID | None = None,
) -> list:
    filters = [
        MediaRecord.workspace_id == workspace_id,
        MediaRecord.buyer_id.in_(allowed_buyer_ids),
    ]
    if date_from:
        filters.append(MediaRecord.record_date >= date_from)
    if date_to:
        filters.append(MediaRecord.record_date <= date_to)
    if buyer_id:
        filters.append(MediaRecord.buyer_id == buyer_id)
    if offer_id:
        filters.append(MediaRecord.offer_id == offer_id)
    if geo:
        filters.append(Offer.geo == geo.upper())
    if partner_id:
        filters.append(Offer.partner_id == partner_id)
    return filters


# A board never shows more distinct buyer×offer combinations than this. The cap keeps a
# careless filter from turning the grouped response back into a full table dump.
GROUP_LIMIT = 5000


def _service_cost_column(value_model, service_model=Service):
    """SQL twin of `service_cost()` — same two-step rounding, so sums match the API."""
    return func.round(
        func.coalesce(
            value_model.manual_cost_override,
            func.round(value_model.quantity * service_model.install_cost, 4)
            * (service_model.commission_pct / 100 + 1),
        ),
        4,
    )


def _provider_amount_column(value_model, provider_model=SpendProvider):
    """SQL twin of `amount_with_commission()`."""
    return func.round(
        func.coalesce(
            value_model.manual_amount_override,
            func.round(
                value_model.base_amount * (provider_model.commission_pct / 100 + 1), 4
            ),
        ),
        4,
    )


def _group_dimensions(filtered):
    return [
        filtered.c.buyer_id,
        filtered.c.buyer,
        filtered.c.geo,
        filtered.c.partner_id,
        filtered.c.partner,
        filtered.c.offer_id,
        filtered.c.offer,
    ]


def _group_identity(row) -> tuple:
    return (row.buyer_id, row.offer_id)


def _group_head(row) -> dict:
    return {
        "buyer_id": str(row.buyer_id),
        "buyer": row.buyer,
        "geo": row.geo,
        "partner_id": str(row.partner_id) if row.partner_id else None,
        "partner": row.partner,
        "offer_id": str(row.offer_id),
        "offer": row.offer,
    }


@router.get("/media-records/groups")
async def media_record_groups(
    date_from: date | None = None,
    date_to: date | None = None,
    buyer_id: uuid.UUID | None = None,
    offer_id: uuid.UUID | None = None,
    geo: str | None = None,
    partner_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("media.view")),
) -> dict:
    """Pre-aggregated board rows, one per buyer × offer.

    The board groups by buyer / GEO / partner / offer in any order the user picks, and
    every one of those dimensions is a function of this pair — so the client can build
    each arrangement from this single response without re-fetching, and without ever
    holding the raw records.
    """
    visible_buyers = await accessible_user_ids(db, current)
    filters = _media_filters(
        current.workspace_id,
        visible_buyers,
        date_from,
        date_to,
        buyer_id,
        offer_id,
        geo,
        partner_id,
    )
    filtered = (
        select(
            MediaRecord.id.label("record_id"),
            MediaRecord.buyer_id.label("buyer_id"),
            User.name.label("buyer"),
            MediaRecord.offer_id.label("offer_id"),
            Offer.name.label("offer"),
            Offer.geo.label("geo"),
            Offer.partner_id.label("partner_id"),
            Partner.name.label("partner"),
            MediaRecord.installs.label("installs"),
            MediaRecord.registrations.label("registrations"),
            MediaRecord.ftd.label("ftd"),
            MediaRecord.revenue.label("revenue"),
            MediaRecord.spend_override.label("spend_override"),
            MediaRecord.spend_calculated.label("spend_calculated"),
        )
        .join(User, User.id == MediaRecord.buyer_id)
        .join(Offer, Offer.id == MediaRecord.offer_id)
        .outerjoin(Partner, Partner.id == Offer.partner_id)
        .where(*filters)
        .subquery("filtered_media")
    )
    dimensions = _group_dimensions(filtered)

    rent_per_record = (
        select(
            MediaServiceValue.media_record_id.label("record_id"),
            func.sum(_service_cost_column(MediaServiceValue)).label("rent"),
        )
        .join(Service, Service.id == MediaServiceValue.service_id)
        .join(filtered, filtered.c.record_id == MediaServiceValue.media_record_id)
        .group_by(MediaServiceValue.media_record_id)
        .subquery("rent_per_record")
    )
    spend_per_record = (
        select(
            MediaSpendValue.media_record_id.label("record_id"),
            func.sum(_provider_amount_column(MediaSpendValue)).label("spend"),
        )
        .join(SpendProvider, SpendProvider.id == MediaSpendValue.provider_id)
        .join(filtered, filtered.c.record_id == MediaSpendValue.media_record_id)
        .group_by(MediaSpendValue.media_record_id)
        .subquery("spend_per_record")
    )
    # Mirrors `media_metrics()`: a manual override wins, otherwise the providers decide,
    # and the stored value is the fallback for records that have no provider split yet.
    effective_spend = func.coalesce(
        filtered.c.spend_override,
        spend_per_record.c.spend,
        filtered.c.spend_calculated,
    )
    base_rows = (
        await db.execute(
            select(
                *dimensions,
                func.count().label("records"),
                func.sum(filtered.c.installs).label("installs"),
                func.sum(filtered.c.registrations).label("registrations"),
                func.sum(filtered.c.ftd).label("ftd"),
                func.sum(filtered.c.revenue).label("revenue"),
                func.sum(func.coalesce(rent_per_record.c.rent, 0)).label("rent"),
                func.sum(effective_spend).label("spend"),
            )
            .select_from(filtered)
            .outerjoin(rent_per_record, rent_per_record.c.record_id == filtered.c.record_id)
            .outerjoin(
                spend_per_record, spend_per_record.c.record_id == filtered.c.record_id
            )
            .group_by(*dimensions)
            .order_by(filtered.c.buyer, filtered.c.offer)
            .limit(GROUP_LIMIT + 1)
        )
    ).all()
    truncated = len(base_rows) > GROUP_LIMIT
    base_rows = base_rows[:GROUP_LIMIT]

    groups: dict[tuple, dict] = {}
    record_count = 0
    for row in base_rows:
        record_count += int(row.records)
        groups[_group_identity(row)] = {
            **_group_head(row),
            "records": int(row.records),
            "installs": row.installs,
            "registrations": row.registrations,
            "ftd": row.ftd,
            "revenue": row.revenue,
            "rent": row.rent,
            "spend": row.spend,
            "services": {},
            "providers": {},
        }

    service_rows = (
        await db.execute(
            select(
                filtered.c.buyer_id,
                filtered.c.offer_id,
                MediaServiceValue.service_id,
                func.sum(MediaServiceValue.quantity).label("quantity"),
                func.sum(_service_cost_column(MediaServiceValue)).label("cost"),
            )
            .select_from(filtered)
            .join(MediaServiceValue, MediaServiceValue.media_record_id == filtered.c.record_id)
            .join(Service, Service.id == MediaServiceValue.service_id)
            .group_by(filtered.c.buyer_id, filtered.c.offer_id, MediaServiceValue.service_id)
        )
    ).all()
    for row in service_rows:
        group = groups.get((row.buyer_id, row.offer_id))
        if group is not None:
            group["services"][str(row.service_id)] = {
                "quantity": row.quantity,
                "cost": row.cost,
            }

    provider_rows = (
        await db.execute(
            select(
                filtered.c.buyer_id,
                filtered.c.offer_id,
                MediaSpendValue.provider_id,
                func.sum(_provider_amount_column(MediaSpendValue)).label("amount"),
            )
            .select_from(filtered)
            .join(MediaSpendValue, MediaSpendValue.media_record_id == filtered.c.record_id)
            .join(SpendProvider, SpendProvider.id == MediaSpendValue.provider_id)
            .group_by(filtered.c.buyer_id, filtered.c.offer_id, MediaSpendValue.provider_id)
        )
    ).all()
    for row in provider_rows:
        group = groups.get((row.buyer_id, row.offer_id))
        if group is not None:
            group["providers"][str(row.provider_id)] = {"amount": row.amount}

    return {
        "groups": list(groups.values()),
        "record_count": record_count,
        "truncated": truncated,
    }


@router.get("/media-records", response_model=Page)
async def list_media_records(
    date_from: date | None = None,
    date_to: date | None = None,
    buyer_id: uuid.UUID | None = None,
    offer_id: uuid.UUID | None = None,
    geo: str | None = None,
    partner_id: uuid.UUID | None = None,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("media.view")),
) -> Page:
    visible_buyers = await accessible_user_ids(db, current)
    filters = _media_filters(
        current.workspace_id,
        visible_buyers,
        date_from,
        date_to,
        buyer_id,
        offer_id,
        geo,
        partner_id,
    )
    total = await db.scalar(
        select(func.count())
        .select_from(MediaRecord)
        .join(Offer, Offer.id == MediaRecord.offer_id)
        .where(*filters)
    )
    rows = (
        await db.execute(
            select(MediaRecord, User.name, Offer.name, Offer.geo, Partner.name, Offer.partner_id)
            .join(User, User.id == MediaRecord.buyer_id)
            .join(Offer, Offer.id == MediaRecord.offer_id)
            .outerjoin(Partner, Partner.id == Offer.partner_id)
            .where(*filters)
            .order_by(MediaRecord.record_date.desc(), User.name, Offer.name, MediaRecord.id)
            .limit(min(limit, 1000))
            .offset(offset)
        )
    ).all()
    record_ids = [record.id for record, *_ in rows]
    rent_by_record: dict[uuid.UUID, Decimal] = {}
    spend_by_record: dict[uuid.UUID, Decimal] = {}
    services_by_record: dict[uuid.UUID, dict[str, dict]] = {}
    providers_by_record: dict[uuid.UUID, dict[str, dict]] = {}
    if record_ids:
        service_rows = (
            await db.execute(
                select(MediaServiceValue, Service)
                .join(Service, Service.id == MediaServiceValue.service_id)
                .where(MediaServiceValue.media_record_id.in_(record_ids))
            )
        ).all()
        for value, service in service_rows:
            cost = (
                value.manual_cost_override
                if value.manual_cost_override is not None
                else service_cost(value.quantity, service.install_cost, service.commission_pct)
            )
            rent_by_record[value.media_record_id] = (
                rent_by_record.get(value.media_record_id, Decimal("0")) + cost
            )
            services_by_record.setdefault(value.media_record_id, {})[
                str(value.service_id)
            ] = {
                "quantity": value.quantity,
                "cost": cost,
                "manual_cost_override": value.manual_cost_override,
            }
        spend_rows = (
            await db.execute(
                select(MediaSpendValue, SpendProvider)
                .join(SpendProvider, SpendProvider.id == MediaSpendValue.provider_id)
                .where(MediaSpendValue.media_record_id.in_(record_ids))
            )
        ).all()
        for value, provider in spend_rows:
            amount = (
                value.manual_amount_override
                if value.manual_amount_override is not None
                else amount_with_commission(value.base_amount, provider.commission_pct)
            )
            spend_by_record[value.media_record_id] = (
                spend_by_record.get(value.media_record_id, Decimal("0")) + amount
            )
            providers_by_record.setdefault(value.media_record_id, {})[
                str(value.provider_id)
            ] = {
                "base_amount": value.base_amount,
                "amount": amount,
                "manual_amount_override": value.manual_amount_override,
            }
    items = []
    for record, buyer_name, offer_name, geo_value, partner_name, partner_id_value in rows:
        calculated_spend = spend_by_record.get(record.id, record.spend_calculated)
        calculated = media_metrics(
            record.revenue or Decimal("0"),
            calculated_spend,
            record.spend_override,
            record.ftd,
        )
        items.append(
            {
                "id": str(record.id),
                "record_date": record.record_date,
                "buyer_id": str(record.buyer_id),
                "buyer": buyer_name,
                "offer_id": str(record.offer_id),
                "offer": offer_name,
                "geo": geo_value,
                "partner": partner_name,
                "partner_id": str(partner_id_value) if partner_id_value else None,
                "installs": record.installs,
                "registrations": record.registrations,
                "ftd": record.ftd,
                "revenue": record.revenue,
                "rent": rent_by_record.get(record.id, Decimal("0")),
                "spend_override": record.spend_override,
                "services": services_by_record.get(record.id, {}),
                "providers": providers_by_record.get(record.id, {}),
                "source": record.source,
                **calculated,
            }
        )
    return Page(items=items, total=total or 0, limit=min(limit, 1000), offset=offset)


@router.post("/media-records")
async def upsert_media_record(
    payload: MediaRecordIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("media.manage")),
) -> dict:
    visible_buyers = await accessible_user_ids(db, current)
    if payload.buyer_id not in visible_buyers:
        raise HTTPException(status_code=403, detail="Buyer is outside your access hierarchy")
    offer = await db.get(Offer, payload.offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=422, detail="Offer is invalid")
    record = await db.scalar(
        select(MediaRecord).where(
            MediaRecord.workspace_id == current.workspace_id,
            MediaRecord.record_date == payload.record_date,
            MediaRecord.buyer_id == payload.buyer_id,
            MediaRecord.offer_id == payload.offer_id,
        )
    )
    created = record is None
    if not record:
        record = MediaRecord(
            workspace_id=current.workspace_id,
            record_date=payload.record_date,
            buyer_id=payload.buyer_id,
            offer_id=payload.offer_id,
        )
        db.add(record)
    # Only the manual metrics are writable here. `spend_calculated` belongs to the
    # "Агенты и платёжки" block, and a filled field is pinned against Keitaro sync
    # while clearing it hands the field back to Keitaro.
    manual = set(record.manual_fields or [])
    for field in MEDIA_MANUAL_FIELDS:
        value = getattr(payload, field)
        setattr(record, field, value)
        if value is None:
            manual.discard(field)
        else:
            manual.add(field)
    record.manual_fields = sorted(manual)
    if created:
        record.source = "manual"
    await audit(
        db,
        current,
        "media.created" if created else "media.updated",
        f"{'Created' if created else 'Updated'} media record",
        request=request,
        entity_type="media_record",
        entity_id=str(record.id),
    )
    await db.commit()
    await invalidate_dashboard_cache(current.workspace_id)
    return {"id": str(record.id), "created": created}


@router.put("/media-records/{record_id}/values")
async def replace_media_values(
    record_id: uuid.UUID,
    payload: MediaValuesIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("media.manage")),
) -> dict:
    record = await db.get(MediaRecord, record_id)
    visible_buyers = await accessible_user_ids(db, current)
    if (
        not record
        or record.workspace_id != current.workspace_id
        or record.buyer_id not in visible_buyers
    ):
        raise HTTPException(status_code=404, detail="Media record not found")
    services = {
        row.id: row
        for row in (
            await db.execute(
                select(Service).where(
                    Service.workspace_id == current.workspace_id,
                    Service.id.in_({value.service_id for value in payload.services}),
                )
            )
        ).scalars()
    }
    providers = {
        row.id: row
        for row in (
            await db.execute(
                select(SpendProvider).where(
                    SpendProvider.workspace_id == current.workspace_id,
                    SpendProvider.id.in_(
                        {value.provider_id for value in payload.spend_providers}
                    ),
                )
            )
        ).scalars()
    }
    if (
        len(payload.services) != len({value.service_id for value in payload.services})
        or len(services) != len(payload.services)
    ):
        raise HTTPException(status_code=422, detail="One or more services are invalid")
    if (
        len(payload.spend_providers)
        != len({value.provider_id for value in payload.spend_providers})
        or len(providers) != len(payload.spend_providers)
    ):
        raise HTTPException(status_code=422, detail="One or more spend providers are invalid")
    await db.execute(
        delete(MediaServiceValue).where(MediaServiceValue.media_record_id == record.id)
    )
    await db.execute(
        delete(MediaSpendValue).where(MediaSpendValue.media_record_id == record.id)
    )
    rent = Decimal("0")
    spend = Decimal("0")
    for value in payload.services:
        service = services[value.service_id]
        rent += (
            value.manual_cost_override
            if value.manual_cost_override is not None
            else service_cost(value.quantity, service.install_cost, service.commission_pct)
        )
        db.add(MediaServiceValue(media_record_id=record.id, **value.model_dump()))
    for value in payload.spend_providers:
        provider = providers[value.provider_id]
        spend += (
            value.manual_amount_override
            if value.manual_amount_override is not None
            else amount_with_commission(value.base_amount, provider.commission_pct)
        )
        db.add(MediaSpendValue(media_record_id=record.id, **value.model_dump()))
    record.spend_calculated = spend
    await audit(
        db,
        current,
        "media.values_changed",
        "Updated service and spend values",
        request=request,
        entity_type="media_record",
        entity_id=str(record.id),
    )
    await db.commit()
    await invalidate_dashboard_cache(current.workspace_id)
    return {"id": str(record.id), "rent": rent, "spend": spend}


def _finance_filters(
    workspace_id: uuid.UUID,
    allowed_buyer_ids: set[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
    buyer_id: uuid.UUID | None,
    offer_id: uuid.UUID | None,
    geo: str | None = None,
    partner_id: uuid.UUID | None = None,
    link: str | None = None,
) -> list:
    filters = [
        FinanceRecord.workspace_id == workspace_id,
        FinanceRecord.buyer_id.in_(allowed_buyer_ids),
    ]
    if date_from:
        filters.append(FinanceRecord.record_date >= date_from)
    if date_to:
        filters.append(FinanceRecord.record_date <= date_to)
    if buyer_id:
        filters.append(FinanceRecord.buyer_id == buyer_id)
    if offer_id:
        filters.append(FinanceRecord.offer_id == offer_id)
    if geo:
        filters.append(Offer.geo == geo.upper())
    if partner_id:
        filters.append(Offer.partner_id == partner_id)
    if link:
        filters.append(FinanceRecord.link.ilike(f"%{link}%"))
    return filters


@router.get("/finance-records/groups")
async def finance_record_groups(
    date_from: date | None = None,
    date_to: date | None = None,
    buyer_id: uuid.UUID | None = None,
    offer_id: uuid.UUID | None = None,
    geo: str | None = None,
    partner_id: uuid.UUID | None = None,
    link: str | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.view")),
) -> dict:
    """Pre-aggregated finance rows, one per buyer × offer. See `media_record_groups()`."""
    visible_buyers = await accessible_user_ids(db, current)
    filters = _finance_filters(
        current.workspace_id,
        visible_buyers,
        date_from,
        date_to,
        buyer_id,
        offer_id,
        geo,
        partner_id,
        link,
    )
    filtered = (
        select(
            FinanceRecord.id.label("record_id"),
            FinanceRecord.buyer_id.label("buyer_id"),
            User.name.label("buyer"),
            FinanceRecord.offer_id.label("offer_id"),
            Offer.name.label("offer"),
            Offer.geo.label("geo"),
            Offer.partner_id.label("partner_id"),
            Partner.name.label("partner"),
            FinanceRecord.qual.label("qual"),
            FinanceRecord.rent.label("rent"),
            FinanceRecord.spend.label("spend"),
            FinanceRecord.spend_override.label("spend_override"),
            FinanceRecord.revenue.label("revenue"),
            FinanceRecord.salary.label("salary"),
        )
        .join(User, User.id == FinanceRecord.buyer_id)
        .join(Offer, Offer.id == FinanceRecord.offer_id)
        .outerjoin(Partner, Partner.id == Offer.partner_id)
        .where(*filters)
        .subquery("filtered_finance")
    )
    dimensions = _group_dimensions(filtered)
    effective_spend = func.coalesce(filtered.c.spend_override, filtered.c.spend)

    base_rows = (
        await db.execute(
            select(
                *dimensions,
                func.count().label("records"),
                func.sum(filtered.c.qual).label("qual"),
                func.sum(filtered.c.rent).label("rent"),
                func.sum(effective_spend).label("spend"),
                func.sum(filtered.c.revenue).label("revenue"),
                func.sum(filtered.c.salary).label("salary"),
            )
            .select_from(filtered)
            .group_by(*dimensions)
            .order_by(filtered.c.buyer, filtered.c.offer)
            .limit(GROUP_LIMIT + 1)
        )
    ).all()
    truncated = len(base_rows) > GROUP_LIMIT
    base_rows = base_rows[:GROUP_LIMIT]

    groups: dict[tuple, dict] = {}
    record_count = 0
    for row in base_rows:
        record_count += int(row.records)
        groups[_group_identity(row)] = {
            **_group_head(row),
            "records": int(row.records),
            "qual": row.qual,
            "rent": row.rent,
            "spend": row.spend,
            "revenue": row.revenue,
            "salary": row.salary,
            "services": {},
            "providers": {},
        }

    service_rows = (
        await db.execute(
            select(
                filtered.c.buyer_id,
                filtered.c.offer_id,
                FinanceServiceValue.service_id,
                func.sum(FinanceServiceValue.quantity).label("quantity"),
                func.sum(_service_cost_column(FinanceServiceValue)).label("cost"),
            )
            .select_from(filtered)
            .join(
                FinanceServiceValue,
                FinanceServiceValue.finance_record_id == filtered.c.record_id,
            )
            .join(Service, Service.id == FinanceServiceValue.service_id)
            .group_by(
                filtered.c.buyer_id, filtered.c.offer_id, FinanceServiceValue.service_id
            )
        )
    ).all()
    for row in service_rows:
        group = groups.get((row.buyer_id, row.offer_id))
        if group is not None:
            group["services"][str(row.service_id)] = {
                "quantity": row.quantity,
                "cost": row.cost,
            }

    provider_rows = (
        await db.execute(
            select(
                filtered.c.buyer_id,
                filtered.c.offer_id,
                FinanceSpendValue.provider_id,
                func.sum(_provider_amount_column(FinanceSpendValue)).label("amount"),
            )
            .select_from(filtered)
            .join(
                FinanceSpendValue,
                FinanceSpendValue.finance_record_id == filtered.c.record_id,
            )
            .join(SpendProvider, SpendProvider.id == FinanceSpendValue.provider_id)
            .group_by(
                filtered.c.buyer_id, filtered.c.offer_id, FinanceSpendValue.provider_id
            )
        )
    ).all()
    for row in provider_rows:
        group = groups.get((row.buyer_id, row.offer_id))
        if group is not None:
            group["providers"][str(row.provider_id)] = {"amount": row.amount}

    return {
        "groups": list(groups.values()),
        "record_count": record_count,
        "truncated": truncated,
    }


@router.get("/finance-records", response_model=Page)
async def list_finance_records(
    date_from: date | None = None,
    date_to: date | None = None,
    buyer_id: uuid.UUID | None = None,
    offer_id: uuid.UUID | None = None,
    geo: str | None = None,
    partner_id: uuid.UUID | None = None,
    link: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.view")),
) -> Page:
    visible_buyers = await accessible_user_ids(db, current)
    filters = _finance_filters(
        current.workspace_id,
        visible_buyers,
        date_from,
        date_to,
        buyer_id,
        offer_id,
        geo,
        partner_id,
        link,
    )
    total = await db.scalar(
        select(func.count())
        .select_from(FinanceRecord)
        .join(Offer, Offer.id == FinanceRecord.offer_id)
        .where(*filters)
    )
    rows = (
        await db.execute(
            select(
                FinanceRecord, User.name, Offer.name, Offer.geo, Partner.name, Offer.partner_id
            )
            .join(User, User.id == FinanceRecord.buyer_id)
            .join(Offer, Offer.id == FinanceRecord.offer_id)
            .outerjoin(Partner, Partner.id == Offer.partner_id)
            .where(*filters)
            # `id` is the tie-breaker: date+buyer is not unique for finance, and without
            # a total order LIMIT/OFFSET pages overlap — rows repeat and others vanish.
            .order_by(FinanceRecord.record_date.desc(), User.name, FinanceRecord.id)
            .limit(min(limit, 1000))
            .offset(offset)
        )
    ).all()
    record_ids = [record.id for record, *_ in rows]
    services_by_record: dict[uuid.UUID, dict[str, dict]] = {}
    providers_by_record: dict[uuid.UUID, dict[str, dict]] = {}
    if record_ids:
        service_rows = (
            await db.execute(
                select(FinanceServiceValue, Service)
                .join(Service, Service.id == FinanceServiceValue.service_id)
                .where(FinanceServiceValue.finance_record_id.in_(record_ids))
            )
        ).all()
        for value, service in service_rows:
            cost = (
                value.manual_cost_override
                if value.manual_cost_override is not None
                else service_cost(value.quantity, service.install_cost, service.commission_pct)
            )
            services_by_record.setdefault(value.finance_record_id, {})[
                str(value.service_id)
            ] = {
                "quantity": value.quantity,
                "cost": cost,
                "manual_cost_override": value.manual_cost_override,
            }
        spend_rows = (
            await db.execute(
                select(FinanceSpendValue, SpendProvider)
                .join(SpendProvider, SpendProvider.id == FinanceSpendValue.provider_id)
                .where(FinanceSpendValue.finance_record_id.in_(record_ids))
            )
        ).all()
        for value, provider in spend_rows:
            amount = (
                value.manual_amount_override
                if value.manual_amount_override is not None
                else amount_with_commission(value.base_amount, provider.commission_pct)
            )
            providers_by_record.setdefault(value.finance_record_id, {})[
                str(value.provider_id)
            ] = {
                "base_amount": value.base_amount,
                "amount": amount,
                "manual_amount_override": value.manual_amount_override,
            }
    items = []
    for record, buyer_name, offer_name, geo_value, partner_name, partner_id_value in rows:
        effective_spend = (
            record.spend_override if record.spend_override is not None else record.spend
        )
        items.append(
            {
                "id": str(record.id),
                "record_date": record.record_date,
                "buyer_id": str(record.buyer_id),
                "buyer": buyer_name,
                "offer_id": str(record.offer_id),
                "offer": offer_name,
                "geo": geo_value,
                "partner": partner_name,
                "partner_id": str(partner_id_value) if partner_id_value else None,
                "link": record.link,
                "rent": record.rent,
                "spend": effective_spend,
                "spend_override": record.spend_override,
                "qual": record.qual,
                "revenue": record.revenue,
                "salary": record.salary,
                "services": services_by_record.get(record.id, {}),
                "providers": providers_by_record.get(record.id, {}),
                "source": record.source,
                **finance_metrics(record.revenue, record.rent, effective_spend),
            }
        )
    return Page(items=items, total=total or 0, limit=min(limit, 1000), offset=offset)


@router.put("/finance-records/{record_id}/values")
async def replace_finance_values(
    record_id: uuid.UUID,
    payload: FinanceValuesIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.manage")),
) -> dict:
    record = await db.get(FinanceRecord, record_id)
    visible_buyers = await accessible_user_ids(db, current)
    if (
        not record
        or record.workspace_id != current.workspace_id
        or record.buyer_id not in visible_buyers
    ):
        raise HTTPException(status_code=404, detail="Finance record not found")
    services = {
        row.id: row
        for row in (
            await db.execute(
                select(Service).where(
                    Service.workspace_id == current.workspace_id,
                    Service.id.in_({value.service_id for value in payload.services}),
                )
            )
        ).scalars()
    }
    providers = {
        row.id: row
        for row in (
            await db.execute(
                select(SpendProvider).where(
                    SpendProvider.workspace_id == current.workspace_id,
                    SpendProvider.id.in_(
                        {value.provider_id for value in payload.spend_providers}
                    ),
                )
            )
        ).scalars()
    }
    if (
        len(payload.services) != len({value.service_id for value in payload.services})
        or len(services) != len(payload.services)
    ):
        raise HTTPException(status_code=422, detail="One or more services are invalid")
    if (
        len(payload.spend_providers)
        != len({value.provider_id for value in payload.spend_providers})
        or len(providers) != len(payload.spend_providers)
    ):
        raise HTTPException(status_code=422, detail="One or more spend providers are invalid")
    await db.execute(
        delete(FinanceServiceValue).where(
            FinanceServiceValue.finance_record_id == record.id
        )
    )
    await db.execute(
        delete(FinanceSpendValue).where(FinanceSpendValue.finance_record_id == record.id)
    )
    rent = Decimal("0")
    spend = Decimal("0")
    for value in payload.services:
        service = services[value.service_id]
        rent += (
            value.manual_cost_override
            if value.manual_cost_override is not None
            else service_cost(value.quantity, service.install_cost, service.commission_pct)
        )
        db.add(FinanceServiceValue(finance_record_id=record.id, **value.model_dump()))
    for value in payload.spend_providers:
        provider = providers[value.provider_id]
        spend += (
            value.manual_amount_override
            if value.manual_amount_override is not None
            else amount_with_commission(value.base_amount, provider.commission_pct)
        )
        db.add(FinanceSpendValue(finance_record_id=record.id, **value.model_dump()))
    record.rent = rent
    record.spend = spend
    record.spend_override = payload.spend_override
    if payload.qual is not None:
        record.qual = payload.qual
    await audit(
        db,
        current,
        "finance.values_changed",
        "Updated finance service and spend values",
        request=request,
        entity_type="finance_record",
        entity_id=str(record.id),
    )
    await db.commit()
    await invalidate_dashboard_cache(current.workspace_id)
    return {"id": str(record.id), "rent": rent, "spend": spend}


@router.post("/finance-records")
async def upsert_finance_record(
    payload: FinanceRecordIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.manage")),
) -> dict:
    visible_buyers = await accessible_user_ids(db, current)
    if payload.buyer_id not in visible_buyers:
        raise HTTPException(status_code=403, detail="Buyer is outside your access hierarchy")
    offer = await db.get(Offer, payload.offer_id)
    if not offer or offer.workspace_id != current.workspace_id:
        raise HTTPException(status_code=422, detail="Offer is invalid")
    key = finance_import_key(
        payload.record_date, payload.buyer_id, payload.offer_id, payload.link
    )
    record = await db.scalar(
        select(FinanceRecord).where(
            FinanceRecord.workspace_id == current.workspace_id,
            FinanceRecord.import_key == key,
        )
    )
    created = record is None
    if not record:
        record = FinanceRecord(
            workspace_id=current.workspace_id, import_key=key, **payload.model_dump()
        )
        db.add(record)
    else:
        for field, value in payload.model_dump().items():
            setattr(record, field, value)
    await audit(
        db,
        current,
        "finance.created" if created else "finance.updated",
        f"{'Created' if created else 'Updated'} finance record",
        request=request,
        entity_type="finance_record",
        entity_id=str(record.id),
    )
    await db.commit()
    await invalidate_dashboard_cache(current.workspace_id)
    return {"id": str(record.id), "created": created}


async def _resolve_import_rows(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    allowed_buyer_ids: set[uuid.UUID],
    rows: list[dict],
) -> tuple[list[FinanceRecordIn], list[dict]]:
    users = {
        user.login.lower(): user
        for user in (
            await db.execute(select(User).where(User.workspace_id == workspace_id))
        ).scalars()
    }
    offers = {
        offer.external_id: offer
        for offer in (
            await db.execute(select(Offer).where(Offer.workspace_id == workspace_id))
        ).scalars()
    }
    resolved, errors = [], []
    for index, row in enumerate(rows, start=2):
        try:
            buyer = users[str(row.get("buyer_login", "")).strip().lower()]
            if buyer.id not in allowed_buyer_ids:
                raise KeyError("buyer_access")
            offer = offers[str(row.get("offer_external_id", "")).strip()]
            resolved.append(
                FinanceRecordIn(
                    record_date=date.fromisoformat(str(row["date"])),
                    buyer_id=buyer.id,
                    offer_id=offer.id,
                    link=str(row.get("link") or "") or None,
                    rent=Decimal(str(row.get("rent") or 0)),
                    spend=Decimal(str(row.get("spend") or 0)),
                    qual=Decimal(str(row.get("qual") or 0)),
                    revenue=Decimal(str(row.get("revenue") or 0)),
                    salary=Decimal(str(row.get("salary") or 0)),
                    source="import",
                )
            )
        except (KeyError, ValueError, ArithmeticError) as exc:
            errors.append({"row": index, "message": f"Invalid row: {type(exc).__name__}"})
    return resolved, errors


async def _read_upload(file: UploadFile) -> list[dict]:
    content = await file.read()
    if file.filename and file.filename.lower().endswith(".xlsx"):
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        values = list(sheet.values)
        if not values:
            return []
        headers = [str(value or "").strip() for value in values[0]]
        return [dict(zip(headers, row, strict=False)) for row in values[1:] if any(row)]
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1251")
    return list(csv.DictReader(io.StringIO(text)))


@router.post("/imports/finance/preview")
async def preview_finance_import(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.manage")),
) -> dict:
    raw = await _read_upload(file)
    visible_buyers = await accessible_user_ids(db, current)
    resolved, errors = await _resolve_import_rows(
        db, current.workspace_id, visible_buyers, raw
    )
    return {
        "total_rows": len(raw),
        "valid_rows": len(resolved),
        "errors": errors[:100],
        "preview": [row.model_dump(mode="json") for row in resolved[:20]],
    }


@router.post("/imports/finance")
async def import_finance(
    file: UploadFile,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.manage")),
) -> dict:
    existing = await db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.workspace_id == current.workspace_id,
            IdempotencyRecord.key == idempotency_key,
        )
    )
    if existing:
        return existing.response
    raw = await _read_upload(file)
    visible_buyers = await accessible_user_ids(db, current)
    resolved, errors = await _resolve_import_rows(
        db, current.workspace_id, visible_buyers, raw
    )
    created = updated = 0
    for payload in resolved:
        key = finance_import_key(
            payload.record_date, payload.buyer_id, payload.offer_id, payload.link
        )
        record = await db.scalar(
            select(FinanceRecord).where(
                FinanceRecord.workspace_id == current.workspace_id,
                FinanceRecord.import_key == key,
            )
        )
        if record:
            for field, value in payload.model_dump().items():
                setattr(record, field, value)
            updated += 1
        else:
            db.add(
                FinanceRecord(
                    workspace_id=current.workspace_id,
                    import_key=key,
                    **payload.model_dump(),
                )
            )
            created += 1
    response = {"created": created, "updated": updated, "errors": errors}
    db.add(
        IdempotencyRecord(
            workspace_id=current.workspace_id,
            key=idempotency_key,
            operation="finance.import",
            response=response,
        )
    )
    await audit(
        db,
        current,
        "finance.imported",
        f"Imported finance rows: {created} created, {updated} updated",
        request=request,
        data=response,
    )
    await db.commit()
    await invalidate_dashboard_cache(current.workspace_id)
    return response


@router.get("/exports/finance")
async def export_finance(
    format: str = "csv",
    date_from: date | None = None,
    date_to: date | None = None,
    buyer_id: uuid.UUID | None = None,
    offer_id: uuid.UUID | None = None,
    geo: str | None = None,
    partner_id: uuid.UUID | None = None,
    link: str | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.export")),
):
    visible_buyers = await accessible_user_ids(db, current)
    filters = _finance_filters(
        current.workspace_id,
        visible_buyers,
        date_from,
        date_to,
        buyer_id,
        offer_id,
        geo,
        partner_id,
        link,
    )
    rows = (
        await db.execute(
            select(FinanceRecord, User.login, Offer.external_id)
            .join(User, User.id == FinanceRecord.buyer_id)
            .join(Offer, Offer.id == FinanceRecord.offer_id)
            .where(*filters)
            .order_by(FinanceRecord.record_date)
        )
    ).all()
    headers = [
        "date",
        "buyer_login",
        "offer_external_id",
        "link",
        "rent",
        "spend",
        "qual",
        "revenue",
        "salary",
    ]
    data = [
        [
            record.record_date.isoformat(),
            login,
            external_id,
            record.link or "",
            record.rent,
            # Match what the table shows: a manual override wins over the calculation.
            record.spend_override if record.spend_override is not None else record.spend,
            record.qual,
            record.revenue,
            record.salary,
        ]
        for record, login, external_id in rows
    ]
    if format.lower() == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Finance"
        sheet.append(headers)
        for row in data:
            sheet.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        stream.seek(0)
        return StreamingResponse(
            stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="finance.xlsx"'},
        )
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(data)
    return StreamingResponse(
        iter([stream.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="finance.csv"'},
    )


@router.get("/dashboard", response_model=DashboardSummary)
async def dashboard(
    date_from: date | None = None,
    date_to: date | None = None,
    buyer_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("dashboard.view")),
) -> DashboardSummary:
    cache_key = (
        f"dashboard:{current.workspace_id}:{current.id}:{date_from}:{date_to}:{buyer_id}"
    )
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        cached = await redis.get(cache_key)
        if cached:
            await redis.aclose()
            return DashboardSummary.model_validate_json(cached)
    except Exception:
        redis = None
    visible_buyers = await accessible_user_ids(db, current)
    media_filters = _media_filters(
        current.workspace_id, visible_buyers, date_from, date_to, buyer_id, None
    )
    media = (
        await db.execute(
            select(
                func.coalesce(func.sum(MediaRecord.registrations), 0),
                func.coalesce(func.sum(MediaRecord.ftd), 0),
                func.coalesce(func.sum(MediaRecord.revenue), 0),
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            MediaRecord.spend_override, MediaRecord.spend_calculated
                        )
                    ),
                    0,
                ),
            ).where(*media_filters)
        )
    ).one()
    leads, sales = int(media[0]), int(media[1])
    revenue, spend = Decimal(media[2]), Decimal(media[3])
    profit = revenue - spend
    series_rows = (
        await db.execute(
            select(
                MediaRecord.record_date,
                func.coalesce(func.sum(MediaRecord.revenue), 0),
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            MediaRecord.spend_override, MediaRecord.spend_calculated
                        )
                    ),
                    0,
                ),
            )
            .where(*media_filters)
            .group_by(MediaRecord.record_date)
            .order_by(MediaRecord.record_date)
        )
    ).all()
    series = [
        {
            "date": row_date.isoformat(),
            "revenue": Decimal(row_revenue),
            "spend": Decimal(row_spend),
            "profit": Decimal(row_revenue) - Decimal(row_spend),
        }
        for row_date, row_revenue, row_spend in series_rows
    ]
    working_rows = (
        await db.execute(
            select(Offer.id, Offer.name, Offer.geo, Partner.name, User.id, User.name)
            .join(OfferBuyer, OfferBuyer.offer_id == Offer.id)
            .join(User, User.id == OfferBuyer.user_id)
            .outerjoin(Partner, Partner.id == Offer.partner_id)
            .where(
                Offer.workspace_id == current.workspace_id,
                Offer.status == Status.active,
                User.id.in_(visible_buyers),
            )
            .order_by(Offer.name, User.name)
        )
    ).all()
    working_offer_map: dict[uuid.UUID, dict] = {}
    for offer_id, offer_name, geo, partner_name, user_id, user_name in working_rows:
        item = working_offer_map.setdefault(
            offer_id,
            {
                "id": str(offer_id),
                "name": offer_name,
                "geo": geo,
                "partner": partner_name,
                "buyers": [],
            },
        )
        item["buyers"].append({"id": str(user_id), "name": user_name})
    summary = DashboardSummary(
        leads=leads,
        sales=sales,
        epl=None if leads == 0 else revenue / Decimal(leads),
        revenue=revenue,
        spend=spend,
        profit=profit,
        roi=None if spend == 0 else profit / spend * 100,
        working_offers=list(working_offer_map.values()),
        series=series,
    )
    if redis:
        try:
            await redis.setex(cache_key, 300, summary.model_dump_json())
            await redis.aclose()
        except Exception:
            pass
    return summary
