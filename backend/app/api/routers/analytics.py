import csv
import io
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from redis.asyncio import Redis
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import (
    accessible_user_ids,
    has_full_access,
    has_permission,
    require_permission,
)
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
    OfferLead,
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
    MediaDaySpendIn,
    MediaRecordIn,
    MediaValuesIn,
    Page,
)
from app.services import finance_spend
from app.services.audit import audit
from app.services.country_tiers import tier_for, tier_map
from app.services.formulas import (
    amount_with_commission,
    finance_import_key,
    finance_metrics,
    media_metrics,
    service_cost,
)
from app.services.geo import normalize_geo

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


def _many(value) -> list:
    """Фильтр приходит и одним значением, и списком.

    Медиаборд отдаёт набор («офферы вот этих трёх баеров»), а дашборд и ветки
    дерева — ровно одно значение, поэтому принимать приходится оба вида.
    """
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [item for item in value if item is not None and item != ""]
    return [value] if value != "" else []


def _media_filters(
    workspace_id: uuid.UUID,
    allowed_buyer_ids: set[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
    buyer_id: uuid.UUID | list[uuid.UUID] | None,
    offer_id: uuid.UUID | list[uuid.UUID] | None,
    geo: str | list[str] | None = None,
    partner_id: uuid.UUID | list[uuid.UUID] | None = None,
    exclude_offers_group: bool = False,
) -> list:
    filters = [
        MediaRecord.workspace_id == workspace_id,
        MediaRecord.buyer_id.in_(allowed_buyer_ids),
    ]
    # Группа «Оффера» принадлежит одноимённому модулю — в Медиаборде её не показываем.
    # Флаг обязателен: включать это в общий фильтр нельзя, дашборд считает по
    # MediaRecord без join с Offer.
    if exclude_offers_group:
        filters.append(
            func.lower(func.coalesce(Offer.group_name, ""))
            != settings.keitaro_offers_group.strip().lower()
        )
        # Удалённые или отключённые в Keitaro офферы остаются в базе ради
        # целостности истории, но на рабочей доске больше не показываются.
        filters.append(Offer.keitaro_state == Status.active)
    if date_from:
        filters.append(MediaRecord.record_date >= date_from)
    if date_to:
        filters.append(MediaRecord.record_date <= date_to)
    buyers = _many(buyer_id)
    if buyers:
        filters.append(MediaRecord.buyer_id.in_(buyers))
    offers = _many(offer_id)
    if offers:
        filters.append(MediaRecord.offer_id.in_(offers))
    geos = [str(item).upper() for item in _many(geo)]
    if geos:
        filters.append(Offer.geo.in_(geos))
    partners = _many(partner_id)
    if partners:
        filters.append(Offer.partner_id.in_(partners))
    return filters


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


def _group_dimensions(filtered, by_date: bool = False):
    """Разрезы группы. День добавляется только по запросу доски.

    Строк с днём столько же, сколько дней в периоде: месяц данных превращает
    сотню пар «баер × оффер» в тысячи строк. Платить за это должна только та
    доска, у которой уровень «Дата» включён.
    """
    dimensions = [
        filtered.c.buyer_id,
        filtered.c.buyer,
        filtered.c.geo,
        filtered.c.partner_id,
        filtered.c.partner,
        filtered.c.offer_id,
        filtered.c.offer,
    ]
    if by_date:
        dimensions.append(filtered.c.record_date)
    return dimensions


def _group_identity(row) -> tuple:
    return (row.buyer_id, row.offer_id, getattr(row, "record_date", None))


def _group_head(row, tiers: dict) -> dict:
    day = getattr(row, "record_date", None)
    return {
        "date": day.isoformat() if day else None,
        "buyer_id": str(row.buyer_id),
        "buyer": row.buyer,
        "geo": row.geo,
        # Тир считает сервер по справочнику «Тиры стран»: если бы его выводил
        # клиент, доска и финансы однажды разошлись бы в том, что такое Tier1.
        "tier": tier_for(row.geo, tiers),
        "partner_id": str(row.partner_id) if row.partner_id else None,
        "partner": row.partner,
        "offer_id": str(row.offer_id),
        "offer": row.offer,
    }


@router.get("/media-records/groups")
async def media_record_groups(
    date_from: date | None = None,
    date_to: date | None = None,
    buyer_id: list[uuid.UUID] | None = Query(None),
    offer_id: list[uuid.UUID] | None = Query(None),
    geo: list[str] | None = Query(None),
    partner_id: list[uuid.UUID] | None = Query(None),
    by_date: bool = False,
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
        exclude_offers_group=True,
    )
    filtered = (
        select(
            MediaRecord.id.label("record_id"),
            MediaRecord.record_date.label("record_date"),
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
    dimensions = _group_dimensions(filtered, by_date)
    # Разбивки по сервисам и платёжкам режутся по тем же ключам, что и сама
    # группа: иначе их суммы легли бы не в тот день.
    breakdown_keys = [filtered.c.buyer_id, filtered.c.offer_id]
    if by_date:
        breakdown_keys.append(filtered.c.record_date)

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
            # Порядок строк — это порядок узлов на доске: без дня дни встали бы
            # вперемешку внутри баера.
            .order_by(*([filtered.c.record_date] if by_date else []),
                      filtered.c.buyer, filtered.c.offer)
        )
    ).all()

    tiers = await tier_map(db, current.workspace_id)
    groups: dict[tuple, dict] = {}
    record_count = 0
    for row in base_rows:
        record_count += int(row.records)
        groups[_group_identity(row)] = {
            **_group_head(row, tiers),
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
                *breakdown_keys,
                MediaServiceValue.service_id,
                func.sum(MediaServiceValue.quantity).label("quantity"),
                func.sum(_service_cost_column(MediaServiceValue)).label("cost"),
            )
            .select_from(filtered)
            .join(MediaServiceValue, MediaServiceValue.media_record_id == filtered.c.record_id)
            .join(Service, Service.id == MediaServiceValue.service_id)
            .group_by(*breakdown_keys, MediaServiceValue.service_id)
        )
    ).all()
    for row in service_rows:
        group = groups.get(_group_identity(row))
        if group is not None:
            group["services"][str(row.service_id)] = {
                "quantity": row.quantity,
                "cost": row.cost,
            }

    provider_rows = (
        await db.execute(
            select(
                *breakdown_keys,
                MediaSpendValue.provider_id,
                func.sum(_provider_amount_column(MediaSpendValue)).label("amount"),
            )
            .select_from(filtered)
            .join(MediaSpendValue, MediaSpendValue.media_record_id == filtered.c.record_id)
            .join(SpendProvider, SpendProvider.id == MediaSpendValue.provider_id)
            .group_by(*breakdown_keys, MediaSpendValue.provider_id)
        )
    ).all()
    for row in provider_rows:
        group = groups.get(_group_identity(row))
        if group is not None:
            group["providers"][str(row.provider_id)] = {"amount": row.amount}

    return {
        "groups": list(groups.values()),
        "record_count": record_count,
    }


MEDIA_EXPORT_HEADERS = [
    "Баер", "Тир", "GEO", "Партнёрка", "Оффер", "Записей",
    "INST", "REG", "FTD", "SPEND", "REVENUE", "PROFIT", "ROI, %", "CPD",
]

TIER_TITLES = {"T1": "Tier1", "T23": "Tier2/3", "unassigned": "Без тира"}


def _agent_amount(group: dict, agent_id: str) -> Decimal:
    share = (group.get("providers") or {}).get(agent_id) or {}
    return Decimal(str(share.get("amount") or 0))


def _media_export_rows(
    groups: list[dict], agents: list[tuple[str, str]] = ()
) -> list[list]:
    """Строки выгрузки — те же цифры, что в таблице.

    PROFIT, ROI и CPD считаются здесь, а не берутся из группы: на доске это
    производные колонки, и повторить их формулу в выгрузке — единственный
    способ не получить два разных ответа на один вопрос.
    """
    rows = []
    for group in groups:
        spend = Decimal(str(group.get("spend") or 0))
        revenue = Decimal(str(group.get("revenue") or 0))
        ftd = int(group.get("ftd") or 0)
        rows.append([
            group.get("buyer") or "",
            TIER_TITLES.get(group.get("tier"), group.get("tier") or ""),
            group.get("geo") or "",
            group.get("partner") or "",
            group.get("offer") or "",
            int(group.get("records") or 0),
            int(group.get("installs") or 0),
            int(group.get("registrations") or 0),
            ftd,
            spend,
            revenue,
            revenue - spend,
            round((revenue - spend) / spend * 100, 2) if spend > 0 else "",
            round(spend / ftd, 2) if ftd and spend > 0 else "",
            # Спенд по агентам — в конце строки: колонки агентов у каждой
            # выгрузки свои, а остальные стоят на привычных местах.
            *[_agent_amount(group, agent_id) for agent_id, _name in agents],
        ])
    return rows


def _media_export_total(rows: list[list], agent_count: int = 0) -> list:
    """Итог по выгрузке — та же строка «Общая», что и внизу доски."""
    spend = sum((row[9] for row in rows), Decimal(0))
    revenue = sum((row[10] for row in rows), Decimal(0))
    ftd = sum(row[8] for row in rows)
    return [
        "Общая", "", "", "", "",
        sum(row[5] for row in rows),
        sum(row[6] for row in rows),
        sum(row[7] for row in rows),
        ftd,
        spend,
        revenue,
        revenue - spend,
        round((revenue - spend) / spend * 100, 2) if spend > 0 else "",
        round(spend / ftd, 2) if ftd and spend > 0 else "",
        *[
            sum((row[14 + index] for row in rows), Decimal(0))
            for index in range(agent_count)
        ],
    ]


# Уровни структуры доски — в том же смысле, что в board-ui.js (LEVELS).
EXPORT_LEVELS = {
    "buyer": "Баер", "date": "Дата", "agent": "Агент", "tier": "Тир",
    "partner": "Партнёрка", "geo": "GEO", "offer": "Оффер",
}
EXPORT_METRICS = ["Записей", "INST", "REG", "FTD", "SPEND", "REVENUE", "PROFIT", "ROI, %", "CPD"]


def _level_value(level: str, group: dict) -> tuple[str, str]:
    """(ключ сортировки, подпись) строки на уровне структуры."""
    if level == "buyer":
        name = group.get("buyer") or ""
        return name.lower(), name
    if level == "date":
        raw = group.get("date") or ""
        label = ".".join(reversed(raw.split("-"))) if raw else "Без даты"
        return raw, label
    if level == "agent":
        name = group.get("agent_name") or "Без агента"
        # «Без агента» — последней строкой, как на доске.
        return ("~" if name == "Без агента" else name.lower()), name
    if level == "tier":
        code = group.get("tier") or "unassigned"
        return code, TIER_TITLES.get(code, code)
    if level == "partner":
        name = group.get("partner") or "Без ПП"
        return name.lower(), name
    if level == "geo":
        name = group.get("geo") or "Без GEO"
        return name.lower(), name
    name = group.get("offer") or ""
    return name.lower(), name


def _split_by_agent(groups: list[dict], agents: dict[str, str]) -> list[dict]:
    """Строки на уровне «Агент» — так же, как их раскладывает доска.

    Спенд агента уходит в его строку; воронка и доход к агенту не привязаны и
    остаются в «Без агента» вместе с неразнесённым спендом.
    """
    rows: list[dict] = []
    for group in groups:
        attributed = Decimal(0)
        for agent_id, share in (group.get("providers") or {}).items():
            amount = Decimal(str((share or {}).get("amount") or 0))
            attributed += amount
            rows.append({
                **group, "agent_name": agents.get(agent_id, "Агент"), "spend": amount,
                "installs": 0, "registrations": 0, "ftd": 0, "revenue": 0, "records": 0,
                "providers": {agent_id: share},
            })
        rows.append({
            **group, "agent_name": "Без агента",
            "spend": Decimal(str(group.get("spend") or 0)) - attributed, "providers": {},
        })
    return rows


def _structured_export(
    groups: list[dict], levels: list[str], agents: list[tuple[str, str]]
) -> tuple[list[str], list[list]]:
    """Выгрузка по структуре доски: столбцы — выбранные уровни по порядку.

    Каждая строка — одна «ветка» структуры до последнего уровня; числа
    суммируются так же, как в таблице. Разбивка по агентам столбцами нужна,
    только пока «Агент» не стоит уровнем — иначе она повторяет строки.
    """
    if "agent" in levels:
        groups = _split_by_agent(groups, dict(agents))
        agents = []
    buckets: dict[tuple, dict] = {}
    for group in groups:
        values = [_level_value(level, group) for level in levels]
        key = tuple(sort for sort, _label in values)
        bucket = buckets.setdefault(key, {
            "labels": [label for _sort, label in values],
            "records": 0, "installs": 0, "registrations": 0, "ftd": 0,
            "spend": Decimal(0), "revenue": Decimal(0),
            "agents": [Decimal(0) for _agent in agents],
        })
        bucket["records"] += int(group.get("records") or 0)
        bucket["installs"] += int(group.get("installs") or 0)
        bucket["registrations"] += int(group.get("registrations") or 0)
        bucket["ftd"] += int(group.get("ftd") or 0)
        bucket["spend"] += Decimal(str(group.get("spend") or 0))
        bucket["revenue"] += Decimal(str(group.get("revenue") or 0))
        for index, (agent_id, _name) in enumerate(agents):
            bucket["agents"][index] += _agent_amount(group, agent_id)

    def line(labels: list[str], bucket: dict) -> list:
        spend, revenue, ftd = bucket["spend"], bucket["revenue"], bucket["ftd"]
        return [
            *labels,
            bucket["records"], bucket["installs"], bucket["registrations"], ftd,
            spend, revenue, revenue - spend,
            round((revenue - spend) / spend * 100, 2) if spend > 0 else "",
            round(spend / ftd, 2) if ftd and spend > 0 else "",
            *bucket["agents"],
        ]

    data = []
    for key in sorted(buckets):
        bucket = buckets[key]
        empty = not any(
            bucket[field] for field in ("records", "installs", "registrations", "ftd")
        ) and not bucket["spend"] and not bucket["revenue"]
        # Пустая «Без агента» — это строка без единого числа: на доске её тоже нет.
        if empty and "agent" in levels:
            continue
        data.append(line(bucket["labels"], bucket))
    if data:
        total = {
            "records": 0, "installs": 0, "registrations": 0, "ftd": 0,
            "spend": Decimal(0), "revenue": Decimal(0),
            "agents": [Decimal(0) for _agent in agents],
        }
        for bucket in buckets.values():
            for field in ("records", "installs", "registrations", "ftd", "spend", "revenue"):
                total[field] += bucket[field]
            total["agents"] = [a + b for a, b in zip(total["agents"], bucket["agents"], strict=False)]
        labels = ["Общая"] + [""] * (len(levels) - 1) if levels else []
        data.append(line(labels, total) if levels else line([], total))
    headers = [EXPORT_LEVELS[level] for level in levels] + EXPORT_METRICS + [
        f"SPEND · {name}" for _agent_id, name in agents
    ]
    if not levels:
        headers = ["Итог"] + headers
        data = [["Общая", *row] for row in data]
    return headers, data


async def _export_agents(
    db: AsyncSession, workspace_id: uuid.UUID, groups: list[dict]
) -> list[tuple[str, str]]:
    """Агенты, через которых в выгрузке внесён спенд, — по имени."""
    ids = {
        agent_id for group in groups for agent_id in (group.get("providers") or {})
    }
    if not ids:
        return []
    rows = (
        await db.execute(
            select(SpendProvider.id, SpendProvider.name).where(
                SpendProvider.workspace_id == workspace_id,
                SpendProvider.id.in_([uuid.UUID(value) for value in ids]),
            )
        )
    ).all()
    return sorted(
        ((str(row.id), row.name) for row in rows), key=lambda item: item[1].lower()
    )


@router.get("/exports/media")
async def export_media(
    format: str = "csv",
    date_from: date | None = None,
    date_to: date | None = None,
    buyer_id: list[uuid.UUID] | None = Query(None),
    offer_id: list[uuid.UUID] | None = Query(None),
    geo: list[str] | None = Query(None),
    partner_id: list[uuid.UUID] | None = Query(None),
    levels: str | None = None,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("media.view")),
):
    """Выгрузка доски: те же строки и фильтры, что видит человек.

    `levels` — структура доски через запятую («buyer,date,agent»): выгрузка
    повторяет её столбцами и строками. Без параметра — прежний плоский вид.

    Берём готовую агрегацию доски, а не сырые записи: в выгрузке нужны те же
    числа, что на экране, включая посчитанные сервером тиры.
    """
    wanted = None
    if levels is not None:
        wanted = [
            level for level in dict.fromkeys(part.strip() for part in levels.split(","))
            if level in EXPORT_LEVELS
        ]
    payload = await media_record_groups(
        date_from=date_from,
        date_to=date_to,
        buyer_id=buyer_id,
        offer_id=offer_id,
        geo=geo,
        partner_id=partner_id,
        by_date=bool(wanted and "date" in wanted),
        db=db,
        current=current,
    )
    agents = await _export_agents(db, current.workspace_id, payload["groups"])
    if wanted is not None:
        headers, data = _structured_export(payload["groups"], wanted, agents)
    else:
        headers = MEDIA_EXPORT_HEADERS + [f"SPEND · {name}" for _agent_id, name in agents]
        data = _media_export_rows(payload["groups"], agents)
        data.sort(key=lambda row: (row[0], row[4]))
        if data:
            data.append(_media_export_total(data, len(agents)))
    if format.lower() == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Mediaboard"
        sheet.append(headers)
        for row in data:
            sheet.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        stream.seek(0)
        return StreamingResponse(
            stream,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={"Content-Disposition": 'attachment; filename="mediaboard.xlsx"'},
        )
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=";")
    writer.writerow(headers)
    writer.writerows(data)
    return StreamingResponse(
        # utf-8-sig и точка с запятой: Excel иначе открывает такой файл одной
        # колонкой с «крякозябрами».
        iter([stream.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="mediaboard.csv"'},
    )


@router.get("/media-records", response_model=Page)
async def list_media_records(
    date_from: date | None = None,
    date_to: date | None = None,
    buyer_id: list[uuid.UUID] | None = Query(None),
    offer_id: list[uuid.UUID] | None = Query(None),
    geo: list[str] | None = Query(None),
    partner_id: list[uuid.UUID] | None = Query(None),
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
        exclude_offers_group=True,
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
    tiers = await tier_map(db, current.workspace_id)
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
                # Тир считает сервер по тому же справочнику, что и в группах:
                # без него доска не признаёт записи своими, когда уровень
                # «Тир» включён, и ветка выглядит пустой.
                "tier": tier_for(geo_value, tiers),
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
    await finance_spend.lock_workspace(db, current.workspace_id)
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
    if created:
        buyer = await db.get(User, payload.buyer_id)
        buyer_group = (buyer.keitaro_offer_group or "").strip().lower() if buyer else ""
        offer_group = (offer.group_name or "").strip().lower()
        buyer_assignment = await db.scalar(
            select(func.count()).select_from(OfferBuyer).where(
                OfferBuyer.offer_id == offer.id,
                OfferBuyer.user_id == payload.buyer_id,
            )
        )
        lead_assignment = await db.scalar(
            select(func.count()).select_from(OfferLead).where(
                OfferLead.offer_id == offer.id,
                OfferLead.user_id == payload.buyer_id,
            )
        )
        if not (buyer_group and buyer_group == offer_group) and not (
            buyer_assignment or lead_assignment
        ):
            raise HTTPException(
                status_code=422,
                detail="Оффер не привязан к выбранному баеру",
            )
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
    provided = payload.model_fields_set
    for field in MEDIA_MANUAL_FIELDS:
        # A field the caller left out is a field it does not own. The Медиаборд modal
        # no longer edits the funnel metrics, and omitting them must not wipe what
        # Keitaro synced; sending an explicit null still clears and unpins the field.
        if field not in provided:
            continue
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
    # Спенд книги идёт с доски: правка записи сразу меняет и его, иначе в
    # финансах осталась бы вчерашняя цифра.
    await finance_spend.refresh_for_record(
        db, current.workspace_id, record.buyer_id, record.record_date
    )
    await db.commit()
    await invalidate_dashboard_cache(current.workspace_id)
    return {"id": str(record.id), "created": created}


def _even_split(total: Decimal, parts: int) -> list[Decimal]:
    """Делит сумму на равные доли до копейки, без потери остатка.

    Наивное `total / parts` на трёх записях и сотне долларов даёт 33.3333 в
    каждой и теряет цент: сумма долей должна совпадать с введённой, иначе в
    финансах у баера появится расхождение на ровном месте.
    """
    if parts <= 0:
        return []
    step = Decimal("0.0001")
    share = (total / parts).quantize(step, rounding=ROUND_HALF_UP)
    shares = [share] * parts
    shares[0] += total.quantize(step, rounding=ROUND_HALF_UP) - share * parts
    return shares


TIER_LABELS = {"T1": "Tier1", "T23": "Tier2/3"}


async def _day_spend_records(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer_id: uuid.UUID,
    day: date,
    tier: str | None,
) -> list[MediaRecord]:
    """Записи баера за день — то, между чем делится расход дня.

    `tier` сужает их до офферов одного тира. В финансах у баера две книги —
    Tier1 и Tier2/3, — и расход, размазанный по офферам обоих, приезжал в них
    не тем, чем был на самом деле.
    """
    rows = list(
        (
            await db.execute(
                select(MediaRecord, Offer.geo)
                .join(Offer, Offer.id == MediaRecord.offer_id)
                .where(
                    MediaRecord.workspace_id == workspace_id,
                    MediaRecord.record_date == day,
                    MediaRecord.buyer_id == buyer_id,
                )
                .order_by(MediaRecord.created_at, MediaRecord.id)
            )
        ).all()
    )
    if tier:
        tiers = await tier_map(db, workspace_id)
        rows = [row for row in rows if tier_for(row[1], tiers) == tier]
    return [row[0] for row in rows]


async def _spend_providers(
    db: AsyncSession, workspace_id: uuid.UUID, values: list
) -> dict[uuid.UUID, SpendProvider]:
    """Агенты из запроса — все свои и без повторов, иначе 422."""
    provider_ids = {value.provider_id for value in values}
    providers = {
        row.id: row
        for row in (
            await db.execute(
                select(SpendProvider).where(
                    SpendProvider.workspace_id == workspace_id,
                    SpendProvider.id.in_(provider_ids),
                )
            )
        ).scalars()
    }
    if len(provider_ids) != len(values) or set(providers) != provider_ids:
        raise HTTPException(
            status_code=422, detail="One or more spend providers are invalid"
        )
    return providers


async def _lay_provider_shares(
    db: AsyncSession,
    records: list[MediaRecord],
    amounts: list[tuple[uuid.UUID, Decimal]],
    providers: dict[uuid.UUID, SpendProvider],
) -> None:
    """Сумма каждого агента — поровну между записями дня.

    Прежние суммы агентов на этих записях заменяются целиком: окно правит
    день, а не добавляет к нему.
    """
    await db.execute(
        delete(MediaSpendValue).where(
            MediaSpendValue.media_record_id.in_([record.id for record in records])
        )
    )
    splits = {
        provider_id: _even_split(amount, len(records)) for provider_id, amount in amounts
    }
    for index, record in enumerate(records):
        spend = Decimal("0")
        for provider_id, _amount in amounts:
            share = splits[provider_id][index]
            db.add(
                MediaSpendValue(
                    media_record_id=record.id,
                    provider_id=provider_id,
                    base_amount=share,
                )
            )
            spend += amount_with_commission(share, providers[provider_id].commission_pct)
        record.spend_calculated = spend
        # Ручная фиксация расхода перебивает агентов, и оставить её значило
        # бы показать старую цифру поверх только что введённой.
        record.spend_override = None
        record.manual_fields = sorted(set(record.manual_fields or []) - {"spend_override"})


@router.post("/media-records/day-spend")
async def spread_day_spend(
    payload: MediaDaySpendIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("media.manage")),
) -> dict:
    """Расход дня, разложенный поровну по офферам этого дня.

    Записи создаёт синхронизация Keitaro (или ручной ввод по офферу) — здесь
    только раскладка: если за день у баера ни одной записи, делить не по чему,
    и придумывать оффер за человека сервер не станет.

    `tier` сужает раскладку до офферов одного тира. В финансах у баера две
    книги — Tier1 и Tier2/3, — и расход всего дня, размазанный по офферам обоих,
    приезжал в них не тем, чем был на самом деле.
    """
    if (payload.spend is None) == (payload.providers is None):
        raise HTTPException(
            status_code=422, detail="Pass either a spend total or the provider split"
        )
    await finance_spend.lock_workspace(db, current.workspace_id)
    visible_buyers = await accessible_user_ids(db, current)
    if payload.buyer_id not in visible_buyers:
        raise HTTPException(status_code=403, detail="Buyer is outside your access hierarchy")
    records = await _day_spend_records(
        db, current.workspace_id, payload.buyer_id, payload.record_date, payload.tier
    )
    if not records:
        # Текст уходит прямо в окно Медиаборда: баер выбирает день календарём
        # и должен понять, почему сумму некуда положить.
        where = f" {TIER_LABELS[payload.tier]}" if payload.tier else ""
        raise HTTPException(
            status_code=422,
            detail=f"За этот день у баера нет офферов{where} — делить расход не по чему",
        )

    if payload.providers is not None:
        providers = await _spend_providers(db, current.workspace_id, payload.providers)
        await _lay_provider_shares(
            db,
            records,
            [(value.provider_id, value.base_amount) for value in payload.providers],
            providers,
        )
        total = sum((value.base_amount for value in payload.providers), Decimal("0"))
    else:
        shares = _even_split(payload.spend, len(records))
        for index, record in enumerate(records):
            record.spend_override = shares[index]
            record.manual_fields = sorted(set(record.manual_fields or []) | {"spend_override"})
        total = payload.spend

    await audit(
        db,
        current,
        "media.day_spend",
        f"Spread {total} across {len(records)} offers"
        + (f" of {payload.tier}" if payload.tier else ""),
        request=request,
        entity_type="media_record",
        entity_id=str(payload.buyer_id),
    )
    await finance_spend.refresh_for_record(
        db, current.workspace_id, payload.buyer_id, payload.record_date
    )
    await db.commit()
    await invalidate_dashboard_cache(current.workspace_id)
    return {"records": len(records), "total": total}


async def _media_rent(db: AsyncSession, record_id: uuid.UUID) -> Decimal:
    """RENT the record already carries, for a request that does not touch services."""
    rows = (
        await db.execute(
            select(MediaServiceValue, Service)
            .join(Service, Service.id == MediaServiceValue.service_id)
            .where(MediaServiceValue.media_record_id == record_id)
        )
    ).all()
    return sum(
        (
            value.manual_cost_override
            if value.manual_cost_override is not None
            else service_cost(value.quantity, service.install_cost, service.commission_pct)
        )
        for value, service in rows
    ) or Decimal("0")


@router.put("/media-records/{record_id}/values")
async def replace_media_values(
    record_id: uuid.UUID,
    payload: MediaValuesIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("media.manage")),
) -> dict:
    await finance_spend.lock_workspace(db, current.workspace_id)
    record = await db.get(MediaRecord, record_id)
    visible_buyers = await accessible_user_ids(db, current)
    if (
        not record
        or record.workspace_id != current.workspace_id
        or record.buyer_id not in visible_buyers
    ):
        raise HTTPException(status_code=404, detail="Media record not found")
    if payload.services is not None:
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
        if (
            len(payload.services) != len({value.service_id for value in payload.services})
            or len(services) != len(payload.services)
        ):
            raise HTTPException(status_code=422, detail="One or more services are invalid")
    if payload.spend_providers is not None:
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
            len(payload.spend_providers)
            != len({value.provider_id for value in payload.spend_providers})
            or len(providers) != len(payload.spend_providers)
        ):
            raise HTTPException(
                status_code=422, detail="One or more spend providers are invalid"
            )

    if payload.services is None:
        rent = await _media_rent(db, record.id)
    else:
        await db.execute(
            delete(MediaServiceValue).where(MediaServiceValue.media_record_id == record.id)
        )
        rent = Decimal("0")
        for value in payload.services:
            service = services[value.service_id]
            rent += (
                value.manual_cost_override
                if value.manual_cost_override is not None
                else service_cost(value.quantity, service.install_cost, service.commission_pct)
            )
            db.add(MediaServiceValue(media_record_id=record.id, **value.model_dump()))

    if payload.spend_providers is None:
        spend = record.spend_calculated
    else:
        await db.execute(
            delete(MediaSpendValue).where(MediaSpendValue.media_record_id == record.id)
        )
        spend = Decimal("0")
        for value in payload.spend_providers:
            provider = providers[value.provider_id]
            spend += (
                value.manual_amount_override
                if value.manual_amount_override is not None
                else amount_with_commission(value.base_amount, provider.commission_pct)
            )
            db.add(MediaSpendValue(media_record_id=record.id, **value.model_dump()))
        record.spend_calculated = spend
        # Ручная фиксация расхода перебивает агентов. Она могла остаться от
        # раскладки расхода по дню, и тогда введённые здесь суммы просто не
        # дошли бы до доски: цифра в SPEND не изменилась бы вовсе.
        record.spend_override = None
        record.manual_fields = sorted(set(record.manual_fields or []) - {"spend_override"})
    await audit(
        db,
        current,
        "media.values_changed",
        "Updated service and spend values",
        request=request,
        entity_type="media_record",
        entity_id=str(record.id),
    )
    # Спенд книги идёт с доски: правка записи сразу меняет и его, иначе в
    # финансах осталась бы вчерашняя цифра.
    await finance_spend.refresh_for_record(
        db, current.workspace_id, record.buyer_id, record.record_date
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
        )
    ).all()

    tiers = await tier_map(db, current.workspace_id)
    groups: dict[tuple, dict] = {}
    record_count = 0
    for row in base_rows:
        record_count += int(row.records)
        groups[_group_identity(row)] = {
            **_group_head(row, tiers),
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
    tiers = await tier_map(db, current.workspace_id)
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
                # Тир считает сервер по тому же справочнику, что и в группах:
                # без него доска не признаёт записи своими, когда уровень
                # «Тир» включён, и ветка выглядит пустой.
                "tier": tier_for(geo_value, tiers),
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


async def _offers_widget(
    db: AsyncSession,
    current: User,
    *,
    with_total: bool = False,
) -> dict[uuid.UUID, dict] | tuple[dict[uuid.UUID, dict], int]:
    """Виджет «Ваши оффера» — тот же справочник, что в разделе «Оффера».

    Роль читается по правам, а не по названию: скопированная или переименованная
    роль (ТЗ 7.1) должна вести себя так же.

    * полный доступ и `offers.view_all` — весь справочник;
    * остальные — назначенные им и их людям: оффер сначала отдают тимлиду, а
      тот раздаёт его баерам, поэтому у тимлида в виджете и его собственные
      офферы, и офферы его баеров — ровно как в разделе.

    Статус здесь не фильтр: виджет показывает не «что ждёт действия», а то же,
    что человек увидит, открыв раздел, — иначе на дашборде и в разделе у него
    разные списки. Синхронизированные из Keitaro офферы не в счёт: раздел ведёт
    свой справочник, а трекерные строки в него не входят.
    """
    conditions = [Offer.workspace_id == current.workspace_id, Offer.connection_id.is_(None)]
    if not (await has_full_access(db, current)
            or has_permission(current, "offers.view_all")):
        # Та же область видимости, что у раздела «Оффера»: свои и своей ветки.
        # По одному `current.id` тимлид не видел офферов, которые сам же раздал
        # баерам, — в разделе они есть, а на дашборде их не было.
        visible = await accessible_user_ids(db, current)
        conditions.append(
            or_(
                Offer.id.in_(
                    select(OfferLead.offer_id).where(OfferLead.user_id.in_(visible))
                ),
                Offer.id.in_(
                    select(OfferBuyer.offer_id).where(OfferBuyer.user_id.in_(visible))
                ),
            )
        )
    total = await db.scalar(select(func.count()).select_from(Offer).where(*conditions)) or 0
    rows = (
        await db.execute(
            select(Offer.id, Offer.name, Offer.geo, Partner.name)
            .outerjoin(Partner, Partner.id == Offer.partner_id)
            .where(*conditions)
            .order_by(Offer.is_starred.desc(), Offer.name, Offer.id)
            .limit(50)
        )
    ).all()
    offers = {
        offer_id: {
            "id": str(offer_id),
            "name": name,
            "geo": normalize_geo(geo),
            "partner": partner_name,
            "buyers": [],
        }
        for offer_id, name, geo, partner_name in rows
    }
    if not offers:
        return (offers, total) if with_total else offers
    people = (
        await db.execute(
            select(OfferBuyer.offer_id, User.id, User.name)
            .join(User, User.id == OfferBuyer.user_id)
            .where(OfferBuyer.offer_id.in_(list(offers)))
            .order_by(User.name)
        )
    ).all()
    for offer_id, user_id, user_name in people:
        offers[offer_id]["buyers"].append({"id": str(user_id), "name": user_name})
    return (offers, total) if with_total else offers


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
    working_offer_map, working_offers_total = await _offers_widget(
        db, current, with_total=True
    )
    summary = DashboardSummary(
        leads=leads,
        sales=sales,
        epl=None if leads == 0 else revenue / Decimal(leads),
        revenue=revenue,
        spend=spend,
        profit=profit,
        roi=None if spend == 0 else profit / spend * 100,
        working_offers=list(working_offer_map.values()),
        working_offers_total=working_offers_total,
        series=series,
    )
    if redis:
        try:
            await redis.setex(cache_key, 300, summary.model_dump_json())
            await redis.aclose()
        except Exception:
            pass
    return summary
