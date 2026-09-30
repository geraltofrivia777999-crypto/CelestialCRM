"""GEO-автоправила MetaAds v2: пауза объектов по порогам своего GEO.

Считаются по уже загруженной статистике за сегодня — «сегодня» у каждого
кабинета своё, по его часовому поясу. Объект проверяется отдельно по каждой
стране, где он крутился: сработало правило хотя бы одного GEO — объект встаёт
на паузу целиком, потому что остановить кампанию в одной стране Meta не умеет.

Пороги в долларах; расход кабинета в другой валюте пересчитывается. Депозиты
приходят из Keitaro и привязаны к кампании, поэтому проверки депозитов работают
только на уровне кампаний и только при настроенном sub_id подключения.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import business_timezone
from app.models import (
    IntegrationConnection,
    MetaAdAccount,
    MetaEntity,
    MetaGeoRule,
    MetaGeoRuleEvent,
    MetaGeoRuleSettings,
    MetaStatDaily,
    Status,
)
from app.services.meta_metrics import (
    INSTALL_ACTION_TYPES,
    REGISTRATION_ACTION_TYPES,
    action_count,
    keitaro_by_campaign,
)
from app.services.meta_rules import _convert_money

LEVELS = ("campaign", "adset", "ad")
LEVEL_COLUMNS = {
    "campaign": "campaign_external_id",
    "adset": "adset_external_id",
    "ad": "ad_external_id",
}
# Интервалы делят сутки без остатка — прогон всегда попадает в начало часа.
INTERVALS = (15, 30, 60, 120, 240)
THRESHOLDS = (
    "no_clicks", "no_insts", "no_regs", "no_deps", "max_avg_inst", "max_avg_reg", "max_avg_dep",
)
# (порог, показатель, как назвать в причине)
ZERO_CHECKS = (
    ("no_clicks", "clicks", "кликов"),
    ("no_insts", "insts", "инсталлов"),
    ("no_regs", "regs", "регистраций"),
    ("no_deps", "deps", "депозитов"),
)
AVG_CHECKS = (
    ("max_avg_inst", "insts", "AvgInst"),
    ("max_avg_reg", "regs", "AvgReg"),
    ("max_avg_dep", "deps", "AvgDep"),
)

ClientGetter = Callable[[IntegrationConnection, AsyncSession], Awaitable[object]]


def _usd(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))} $".replace(".", ",")


def check_rule(rule: MetaGeoRule, values: dict) -> list[tuple[str, str]]:
    """Сработавшие проверки правила: [(ключ, причина)]. `values["spend_usd"]` — в $.

    Показатель `None` значит «неизвестен» (депозиты без атрибуции) — такая
    проверка молчит, а не срабатывает на нуле.
    """
    spend = values["spend_usd"]
    hits = []
    for key, metric, label in ZERO_CHECKS:
        limit = getattr(rule, key)
        count = values.get(metric)
        if limit is None or count is None:
            continue
        if count == 0 and spend >= limit:
            hits.append((key, f"Потрачено {_usd(spend)} без {label} (порог {_usd(limit)})"))
    for key, metric, label in AVG_CHECKS:
        limit = getattr(rule, key)
        count = values.get(metric)
        if limit is None or not count:
            continue
        average = spend / count
        if average > limit:
            hits.append((key, f"{label} {_usd(average)} выше {_usd(limit)}"))
    return hits


def _local_today(account: MetaAdAccount, now: datetime) -> date:
    try:
        zone = ZoneInfo(account.timezone_name) if account.timezone_name else business_timezone()
    except ZoneInfoNotFoundError:
        zone = business_timezone()
    return now.astimezone(zone).date()


async def get_settings(db: AsyncSession, workspace_id: uuid.UUID) -> MetaGeoRuleSettings:
    row = await db.get(MetaGeoRuleSettings, workspace_id)
    if row is None:
        row = MetaGeoRuleSettings(
            workspace_id=workspace_id, level="campaign", interval_minutes=30,
            auto_enabled=False, last_run_result={},
        )
        db.add(row)
        await db.flush()
    return row


async def collect(
    db: AsyncSession, workspace_id: uuid.UUID, level: str, rules: dict[str, MetaGeoRule],
    now: datetime,
) -> list[dict]:
    """Строки «объект × GEO» за сегодня по кабинетам — только активные объекты."""
    accounts = {
        account.id: account for account in (await db.execute(
            select(MetaAdAccount).where(
                MetaAdAccount.workspace_id == workspace_id,
                MetaAdAccount.status == Status.active,
            )
        )).scalars()
    }
    if not accounts or not rules:
        return []
    today = {account_id: _local_today(account, now) for account_id, account in accounts.items()}
    column = getattr(MetaStatDaily, LEVEL_COLUMNS[level])
    facts = (await db.execute(
        select(MetaStatDaily).where(
            MetaStatDaily.workspace_id == workspace_id,
            MetaStatDaily.account_id.in_(list(accounts)),
            MetaStatDaily.record_date >= min(today.values()),
            MetaStatDaily.record_date <= max(today.values()),
            column.is_not(None),
        )
    )).scalars()
    slices: dict[tuple, dict] = {}
    for fact in facts:
        geo = (fact.country_code or "").strip().upper()
        if geo not in rules or fact.record_date != today[fact.account_id]:
            continue
        key = (fact.account_id, getattr(fact, LEVEL_COLUMNS[level]), geo)
        row = slices.setdefault(key, {
            "account_id": fact.account_id, "external_id": key[1], "geo": geo,
            "campaign_id": fact.campaign_external_id, "spend": Decimal(0),
            "clicks": 0, "insts": 0, "regs": 0,
        })
        row["spend"] += fact.spend or Decimal(0)
        row["clicks"] += fact.clicks or 0
        row["insts"] += action_count(fact.actions, INSTALL_ACTION_TYPES)
        row["regs"] += action_count(fact.actions, REGISTRATION_ACTION_TYPES)
    if not slices:
        return []

    entities = {
        (entity.account_id, entity.external_id): entity for entity in (await db.execute(
            select(MetaEntity).where(
                MetaEntity.workspace_id == workspace_id,
                MetaEntity.level == level,
                MetaEntity.external_id.in_({key[1] for key in slices}),
            )
        )).scalars()
    }
    deps = await _deposits(db, workspace_id, level, accounts, today, slices)
    rows = []
    for key, row in slices.items():
        entity = entities.get((row["account_id"], row["external_id"]))
        # Паузим только то, что сейчас крутится: остальное уже стоит.
        if not entity or entity.effective_status != "ACTIVE":
            continue
        account = accounts[row["account_id"]]
        row["deps"] = deps.get(key) if level == "campaign" else None
        row["currency"] = account.currency or "USD"
        row["spend_usd"] = _convert_money(row["spend"], row["currency"], "USD")
        row["entity"] = entity
        row["account"] = account
        rows.append(row)
    return rows


async def _deposits(
    db: AsyncSession, workspace_id: uuid.UUID, level: str,
    accounts: dict, today: dict, slices: dict,
) -> dict[tuple, int]:
    """Депозиты Keitaro по «кампания × GEO». У адсетов и объявлений их нет."""
    if level != "campaign":
        return {}
    connections = {
        item.id: item.attribution_sub_id for item in (await db.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.kind == "meta",
            )
        )).scalars()
    }
    cache: dict[tuple, dict] = {}
    result = {}
    for key in slices:
        account_id, campaign_id, geo = key
        sub_id = connections.get(accounts[account_id].connection_id)
        if not sub_id:
            continue
        day = today[account_id]
        lookup = (sub_id, day, geo)
        if lookup not in cache:
            cache[lookup] = await keitaro_by_campaign(db, workspace_id, day, day, sub_id, {geo})
        matched = cache[lookup].get(campaign_id)
        result[key] = int(matched["sales"]) if matched else 0
    return result


async def run_workspace(
    db: AsyncSession, workspace_id: uuid.UUID, client_getter: ClientGetter, *,
    trigger: str = "auto", user_id: uuid.UUID | None = None, now: datetime | None = None,
) -> dict:
    """Один прогон правил воркспейса: проверить, поставить на паузу, записать историю."""
    now = now or datetime.now(UTC)
    config = await get_settings(db, workspace_id)
    rules = {
        rule.country_code: rule for rule in (await db.execute(
            select(MetaGeoRule).where(
                MetaGeoRule.workspace_id == workspace_id, MetaGeoRule.is_enabled.is_(True)
            )
        )).scalars()
    }
    rows = await collect(db, workspace_id, config.level, rules, now)
    clients: dict = {}
    handled: set[tuple] = set()
    summary = {"checked": len(rows), "triggered": 0, "paused": 0, "failed": 0}
    for row in sorted(rows, key=lambda item: item["spend_usd"], reverse=True):
        object_key = (row["account_id"], row["external_id"])
        if object_key in handled:
            continue
        hits = check_rule(rules[row["geo"]], row)
        if not hits:
            continue
        handled.add(object_key)
        summary["triggered"] += 1
        entity, account = row["entity"], row["account"]
        status, error = "paused", None
        try:
            if account.connection_id not in clients:
                connection = await db.get(IntegrationConnection, account.connection_id)
                clients[account.connection_id] = await client_getter(connection, db)
            await clients[account.connection_id].set_status(entity.external_id, "PAUSED")
            entity.effective_status = "PAUSED"
            summary["paused"] += 1
        except Exception as exc:  # noqa: BLE001 — ошибка Meta уходит в историю
            status, error = "failed", " ".join(str(exc).split())[:500]
            summary["failed"] += 1
        db.add(MetaGeoRuleEvent(
            workspace_id=workspace_id, trigger=trigger, user_id=user_id, level=config.level,
            external_id=entity.external_id, name=entity.name or entity.external_id,
            account_external_id=account.external_id, account_name=account.name,
            country_code=row["geo"], checks=[key for key, _ in hits],
            reason="; ".join(text for _, text in hits),
            metrics={
                "spend": float(row["spend"]), "currency": row["currency"],
                "spend_usd": float(row["spend_usd"]), "clicks": row["clicks"],
                "insts": row["insts"], "regs": row["regs"], "deps": row["deps"],
            },
            status=status, error=error,
        ))
    config.last_run_at = now
    config.last_run_result = {**summary, "trigger": trigger}
    await db.commit()
    return summary


def is_due(config: MetaGeoRuleSettings, now: datetime) -> bool:
    if not config.auto_enabled:
        return False
    if config.last_run_at is None:
        return True
    last = config.last_run_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    # Минута допуска: beat срабатывает по часам, а прогон длится секунды.
    return now - last >= timedelta(minutes=config.interval_minutes) - timedelta(minutes=1)


async def run_due(
    session_factory: async_sessionmaker[AsyncSession], client_getter: ClientGetter,
) -> dict:
    """Прогон по расписанию: только воркспейсы с автопрогоном и истёкшим интервалом."""
    now = datetime.now(UTC)
    async with session_factory() as db:
        due = [
            row.workspace_id for row in (await db.execute(select(MetaGeoRuleSettings))).scalars()
            if is_due(row, now)
        ]
    total = {"workspaces": len(due), "triggered": 0, "paused": 0, "failed": 0}
    for workspace_id in due:
        async with session_factory() as db:
            result = await run_workspace(db, workspace_id, client_getter, now=now)
        for key in ("triggered", "paused", "failed"):
            total[key] += result[key]
    return total
