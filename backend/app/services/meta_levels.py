"""Обзор Meta Ads по уровням — от человека до объявления.

Восемь срезов одной и той же дневной статистики. Разрез задаётся тем, по какому
ключу группируются строки `MetaStatDaily`, а иерархия читается снизу вверх:

    объявление → адсет → кампания → кабинет → БМ → социальный аккаунт
    объявление → фан-пейдж   (реклама крутится от его лица)
    кабинет    → ответственный в CRM

Доход из Keitaro привязан к ID кампании, поэтому ниже кампании его не существует:
у адсетов и объявлений `revenue` остаётся пустым, а не делится поровну.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    MetaAdAccount,
    MetaBusiness,
    MetaEntity,
    MetaFanPage,
    MetaSocialAccount,
    MetaStatDaily,
    User,
)
from app.services.meta_metrics import ZERO, metrics

LEVELS = (
    "users",
    "socials",
    "fanpages",
    "businesses",
    "accounts",
    "campaigns",
    "adsets",
    "ads",
)
LEVEL_LABELS = {
    "users": "Пользователи",
    "socials": "Аккаунты",
    "fanpages": "Фан-пейджи",
    "businesses": "БМы",
    "accounts": "Кабинеты",
    "campaigns": "Кампании",
    "adsets": "Адсеты",
    "ads": "Объявления",
}
# Ниже кампании дохода из трекера нет: ключ атрибуции — ID кампании.
REVENUE_LEVELS = {"users", "socials", "fanpages", "businesses", "accounts", "campaigns"}


@dataclass
class Bucket:
    """Накопитель одной строки отчёта."""

    key: str
    name: str
    spend: Decimal = ZERO
    impressions: int = 0
    clicks: int = 0
    link_clicks: int = 0
    results: int = 0
    campaign_ids: set[str] = field(default_factory=set)
    extra: dict = field(default_factory=dict)

    def add(self, row: MetaStatDaily) -> None:
        self.spend += row.spend or ZERO
        self.impressions += row.impressions or 0
        self.clicks += row.clicks or 0
        self.link_clicks += row.link_clicks or 0
        self.results += (row.pixel_leads or 0) + (row.pixel_purchases or 0)
        if row.campaign_external_id:
            self.campaign_ids.add(row.campaign_external_id)


@dataclass
class Graph:
    """Справочники и связи, общие для всех уровней одного запроса."""

    accounts: dict[uuid.UUID, MetaAdAccount]
    socials: dict[uuid.UUID, MetaSocialAccount]
    businesses: dict[uuid.UUID, MetaBusiness]
    pages: dict[str, MetaFanPage]
    entities: dict[tuple[str, str], MetaEntity]
    # ID объявления → ID фан-пейджа, от лица которого оно крутится.
    ad_pages: dict[str, str]
    owners: dict[uuid.UUID, str]


async def load_graph(
    db: AsyncSession, workspace_id: uuid.UUID, account_ids: list[uuid.UUID]
) -> Graph:
    accounts = {
        row.id: row
        for row in (
            await db.execute(select(MetaAdAccount).where(MetaAdAccount.id.in_(account_ids)))
        ).scalars()
    } if account_ids else {}
    socials = {
        row.id: row
        for row in (
            await db.execute(
                select(MetaSocialAccount).where(
                    MetaSocialAccount.workspace_id == workspace_id
                )
            )
        ).scalars()
    }
    businesses = {
        row.id: row
        for row in (
            await db.execute(
                select(MetaBusiness).where(MetaBusiness.workspace_id == workspace_id)
            )
        ).scalars()
    }
    pages = {
        row.external_id: row
        for row in (
            await db.execute(
                select(MetaFanPage).where(MetaFanPage.workspace_id == workspace_id)
            )
        ).scalars()
    }
    entity_rows = list(
        (
            await db.execute(
                select(MetaEntity).where(MetaEntity.account_id.in_(account_ids))
            )
        ).scalars()
    ) if account_ids else []
    entities = {(row.level, row.external_id): row for row in entity_rows}
    ad_pages = {
        row.external_id: row.page_external_id
        for row in entity_rows
        if row.level == "ad" and row.page_external_id
    }
    owner_ids = {row.owner_id for row in accounts.values() if row.owner_id}
    owners = {}
    if owner_ids:
        owners = dict(
            (
                await db.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
            ).all()
        )
    return Graph(accounts, socials, businesses, pages, entities, ad_pages, owners)


def _bucket(buckets: dict[str, Bucket], key: str, name: str) -> Bucket:
    if key not in buckets:
        buckets[key] = Bucket(key=key, name=name)
    return buckets[key]


def _account_key(level: str, account: MetaAdAccount) -> tuple[str, str] | None:
    """Ключ и подпись строки для уровней, которые группируются по кабинету."""
    if level == "accounts":
        return str(account.id), account.name
    if level == "businesses":
        return (str(account.business_id), "") if account.business_id else None
    if level == "socials":
        return (str(account.social_account_id), "") if account.social_account_id else None
    if level == "users":
        return (str(account.owner_id), "") if account.owner_id else None
    return None


def collect(level: str, stats: list[MetaStatDaily], graph: Graph) -> dict[str, Bucket]:
    """Разложить дневные строки в накопители нужного уровня."""
    buckets: dict[str, Bucket] = {}
    for row in stats:
        if level == "ads":
            if not row.ad_external_id:
                continue
            entity = graph.entities.get(("ad", row.ad_external_id))
            _bucket(
                buckets, row.ad_external_id, entity.name if entity else row.ad_external_id
            ).add(row)
            continue
        if level == "adsets":
            if not row.adset_external_id:
                continue
            entity = graph.entities.get(("adset", row.adset_external_id))
            _bucket(
                buckets,
                row.adset_external_id,
                entity.name if entity else row.adset_external_id,
            ).add(row)
            continue
        if level == "campaigns":
            if not row.campaign_external_id:
                continue
            entity = graph.entities.get(("campaign", row.campaign_external_id))
            _bucket(
                buckets,
                row.campaign_external_id,
                entity.name if entity else row.campaign_external_id,
            ).add(row)
            continue
        if level == "fanpages":
            page_id = graph.ad_pages.get(row.ad_external_id or "")
            if not page_id:
                continue
            page = graph.pages.get(page_id)
            _bucket(buckets, page_id, page.name if page else page_id).add(row)
            continue
        account = graph.accounts.get(row.account_id)
        if not account:
            continue
        identity = _account_key(level, account)
        if not identity:
            continue
        _bucket(buckets, identity[0], identity[1] or identity[0]).add(row)
    return buckets


def _named(level: str, bucket: Bucket, graph: Graph) -> str:
    """Подпись строки. Кабинетные уровни знают только ключ — имя ищем здесь."""
    if level == "businesses":
        row = graph.businesses.get(uuid.UUID(bucket.key))
        return row.name if row else bucket.name
    if level == "socials":
        row = graph.socials.get(uuid.UUID(bucket.key))
        return row.name if row else bucket.name
    if level == "users":
        return graph.owners.get(uuid.UUID(bucket.key), bucket.name)
    return bucket.name


def _structure(level: str, key: str, graph: Graph) -> dict:
    """Структурные колонки уровня — сколько под ним объектов."""
    if level == "socials":
        social = uuid.UUID(key)
        return {
            "businesses": sum(
                1 for row in graph.businesses.values() if row.social_account_id == social
            ),
            "fan_pages": sum(
                1 for row in graph.pages.values() if row.social_account_id == social
            ),
            "ad_accounts": sum(
                1 for row in graph.accounts.values() if row.social_account_id == social
            ),
        }
    if level == "businesses":
        business = uuid.UUID(key)
        return {
            "fan_pages": sum(
                1 for row in graph.pages.values() if row.business_id == business
            ),
            "ad_accounts": sum(
                1 for row in graph.accounts.values() if row.business_id == business
            ),
        }
    if level == "fanpages":
        page = graph.pages.get(key)
        business = graph.businesses.get(page.business_id) if page and page.business_id else None
        return {"category": page.category if page else None,
                "business": business.name if business else None}
    if level == "accounts":
        account = graph.accounts.get(uuid.UUID(key))
        if not account:
            return {}
        business = graph.businesses.get(account.business_id) if account.business_id else None
        return {
            "currency": account.currency,
            "account_status": account.account_status,
            "business": business.name if business else None,
            # Кабинет без БМа Meta называет личным — команде важно их различать.
            "kind": "business" if account.business_id else "personal",
            "owner": graph.owners.get(account.owner_id) if account.owner_id else None,
        }
    if level in {"campaigns", "adsets", "ads"}:
        entity = graph.entities.get((_ENTITY_LEVEL[level], key))
        if not entity:
            return {}
        account = graph.accounts.get(entity.account_id)
        payload = {
            "status": entity.effective_status,
            "objective": entity.objective,
            "account": account.name if account else None,
            "daily_budget": float(entity.daily_budget) if entity.daily_budget else None,
        }
        if level == "ads" and entity.page_external_id:
            page = graph.pages.get(entity.page_external_id)
            payload["fan_page"] = page.name if page else entity.page_external_id
        return payload
    return {}


_ENTITY_LEVEL = {"campaigns": "campaign", "adsets": "adset", "ads": "ad"}


def rows_for(
    level: str,
    stats: list[MetaStatDaily],
    graph: Graph,
    keitaro: dict[str, dict],
    *,
    search: str | None = None,
) -> list[dict]:
    buckets = collect(level, stats, graph)
    rows: list[dict] = []
    for bucket in buckets.values():
        name = _named(level, bucket, graph)
        if search and search.lower() not in name.lower():
            continue
        revenue, leads, sales = None, 0, 0
        if level in REVENUE_LEVELS:
            matched = [keitaro[key] for key in bucket.campaign_ids if key in keitaro]
            if matched:
                revenue = sum((item["revenue"] for item in matched), ZERO)
                leads = sum(item["leads"] for item in matched)
                sales = sum(item["sales"] for item in matched)
        rows.append(
            {
                "id": bucket.key,
                "name": name,
                **_structure(level, bucket.key, graph),
                **metrics(
                    bucket.spend,
                    bucket.impressions,
                    bucket.clicks,
                    revenue,
                    leads,
                    sales,
                    link_clicks=bucket.link_clicks,
                    results=bucket.results,
                ),
            }
        )
    rows.sort(key=lambda row: (-(row["spend"] or 0), row["name"]))
    return rows


def empty_rows(level: str, graph: Graph) -> list[dict]:
    """Объекты, у которых за период не было ни одного показа.

    Без них список выглядит короче, чем есть: кабинет, ничего не открутивший
    вчера, из обзора пропадать не должен.
    """
    blank = metrics(ZERO, 0, 0, None, 0, 0)
    if level == "accounts":
        return [
            {"id": str(row.id), "name": row.name, **_structure(level, str(row.id), graph),
             **blank}
            for row in graph.accounts.values()
        ]
    if level == "businesses":
        return [
            {"id": str(row.id), "name": row.name, **_structure(level, str(row.id), graph),
             **blank}
            for row in graph.businesses.values()
        ]
    if level == "socials":
        return [
            {"id": str(row.id), "name": row.name, **_structure(level, str(row.id), graph),
             **blank}
            for row in graph.socials.values()
        ]
    if level == "fanpages":
        return [
            {"id": row.external_id, "name": row.name,
             **_structure(level, row.external_id, graph), **blank}
            for row in graph.pages.values()
        ]
    return []


def merge(active: list[dict], idle: list[dict], *, search: str | None = None) -> list[dict]:
    """Строки с расходом плюс те, что за период молчали."""
    seen = {row["id"] for row in active}
    extra = [
        row
        for row in idle
        if row["id"] not in seen
        and (not search or search.lower() in row["name"].lower())
    ]
    extra.sort(key=lambda row: row["name"])
    return active + extra
