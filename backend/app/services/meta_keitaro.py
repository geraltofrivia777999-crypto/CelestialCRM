"""Attribute Keitaro sub2/sub3/sub4 name macros to unambiguous Meta objects.

The tracker has no FB account identifier in these parameters. A name path that
exists in more than one account/object is deliberately left unattributed.
"""

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decrypt_secret
from app.models import IntegrationConnection, MetaAdAccount, MetaEntity, Status
from app.services.keitaro import KeitaroClient, integer_value

logger = logging.getLogger(__name__)
METRICS = ("clicks", "insts", "regs", "deps")


def _timezone(account: MetaAdAccount) -> str | None:
    try:
        name = account.timezone_name
        return ZoneInfo(name).key if name else None
    except ZoneInfoNotFoundError:
        return None


def _name(value: object) -> str:
    return str(value or "").strip()


def attribute(
    accounts: list[MetaAdAccount], entities: list[MetaEntity],
    reports: dict[str, list[dict]], *, geos: set[str] | None = None,
) -> tuple[dict[tuple, dict], set[str], dict]:
    """Return metrics/geos per account and object, plus attribution diagnostics."""
    account_by_id = {account.id: account for account in accounts}
    campaigns = {(e.account_id, e.external_id): e for e in entities if e.level == "campaign"}
    adsets = {(e.account_id, e.external_id): e for e in entities if e.level == "adset"}
    paths: dict[tuple[str, ...], set[tuple]] = defaultdict(set)
    ads = {(e.account_id, e.external_id): e for e in entities if e.level == "ad"}
    for (account_id, campaign_id), campaign in campaigns.items():
        paths[(_name(campaign.name),)].add((account_id, "campaign", campaign_id))
    for (account_id, adset_id), adset in adsets.items():
        campaign = campaigns.get((account_id, adset.parent_external_id))
        if campaign:
            paths[(_name(campaign.name), _name(adset.name))].add((account_id, "adset", adset_id))
    for ad in ads.values():
        adset = adsets.get((ad.account_id, ad.parent_external_id))
        campaign = campaigns.get((ad.account_id, adset.parent_external_id)) if adset else None
        if campaign:
            paths[(_name(campaign.name), _name(adset.name), _name(ad.name))].add(
                (ad.account_id, "ad", ad.external_id)
            )

    metrics: dict[tuple, dict] = {}
    all_geos: set[str] = set()
    counts = {"matched": 0, "ambiguous": 0, "unmatched": 0}
    for timezone, rows in reports.items():
        for row in rows:
            geo = _name(row.get("country_code")).upper()
            names = tuple(_name(row.get(f"sub_id_{index}")) for index in (2, 3, 4))
            if not names[0]:
                counts["unmatched"] += 1
                continue
            if names[2] and not names[1]:
                counts["unmatched"] += 1
                continue
            path = names[:3 if names[2] else 2 if names[1] else 1]
            candidates = paths.get(path, set())
            # Disambiguate globally, not just inside this timezone. Otherwise
            # the same tracker row could be counted once per FB timezone.
            if len(candidates) != 1:
                counts["ambiguous" if candidates else "unmatched"] += 1
                continue
            leaf = next(iter(candidates))
            account = account_by_id.get(leaf[0])
            if not account or _timezone(account) != timezone:
                continue
            if len(geo) == 2 and geo.isalpha():
                all_geos.add(geo)
            if geos and geo not in geos:
                continue
            chain = [(account.id, "account", "")]
            if len(path) == 3:
                ad = ads[(leaf[0], leaf[2])]
                adset = adsets[(leaf[0], ad.parent_external_id)]
                campaign = campaigns[(leaf[0], adset.parent_external_id)]
                chain.extend([(leaf[0], "campaign", campaign.external_id),
                              (leaf[0], "adset", adset.external_id), leaf])
            elif len(path) == 2:
                adset = adsets[(leaf[0], leaf[2])]
                campaign = campaigns[(leaf[0], adset.parent_external_id)]
                chain.extend([(leaf[0], "campaign", campaign.external_id), leaf])
            else:
                chain.append(leaf)
            values = {
                "clicks": integer_value(row.get("clicks")),
                "insts": integer_value(row.get("campaign_unique_clicks")),
                "regs": integer_value(row.get("leads")),
                "deps": integer_value(row.get("sales")),
            }
            geo_keys = [(*key, geo) for key in chain if len(geo) == 2 and geo.isalpha()]
            for key in [*chain, *geo_keys]:
                target = metrics.setdefault(key, {**{metric: 0 for metric in METRICS}, "geos": set()})
                for metric in METRICS:
                    target[metric] += values[metric]
                if len(geo) == 2 and geo.isalpha():
                    target["geos"].add(geo)
            counts["matched"] += 1
    return metrics, all_geos, counts


async def _report(connection: IntegrationConnection, start: date, end: date, timezone: str) -> list[dict]:
    key = f"meta:name-report:{connection.id}:{start}:{end}:{timezone}"
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        try:
            cached = await redis.get(key)
            if cached is not None:
                return json.loads(cached)
        except Exception:
            logger.warning("Keitaro Meta report cache unavailable", exc_info=True)
        client = KeitaroClient(connection.base_url, decrypt_secret(connection.api_key_encrypted),
                               timeout=30, max_attempts=2)
        rows = await client.meta_name_report(start, end, timezone=timezone)
        try:
            # Current-day tracker metrics should appear almost immediately;
            # historical reports are stable and can be cached longer.
            ttl = 15 if end >= datetime.now(ZoneInfo(timezone)).date() else 900
            await redis.set(key, json.dumps(rows), ex=ttl)
        except Exception:
            logger.warning("Keitaro Meta report cache write failed", exc_info=True)
        return rows
    finally:
        await redis.aclose()


async def reports_for_accounts(
    db: AsyncSession, workspace_id: uuid.UUID, accounts: list[MetaAdAccount],
    start: date, end: date, *, days_by_account: dict[uuid.UUID, date] | None = None,
) -> tuple[dict[str, list[dict]], list[str]]:
    timezones = {_timezone(account) for account in accounts}
    missing = [str(account.id) for account in accounts if _timezone(account) is None]
    timezones.discard(None)
    if not timezones:
        return {}, missing
    connections = list((await db.execute(select(IntegrationConnection).where(
        IntegrationConnection.workspace_id == workspace_id,
        IntegrationConnection.kind == "keitaro",
        IntegrationConnection.status == Status.active,
    ))).scalars())
    if not connections:
        return {}, missing
    tasks = [(connection, timezone) for connection in connections for timezone in sorted(timezones)]
    day_by_timezone = {_timezone(account): days_by_account[account.id]
                       for account in accounts if days_by_account and account.id in days_by_account}
    results = await asyncio.gather(*(_report(connection,
                                             day_by_timezone.get(timezone, start),
                                             day_by_timezone.get(timezone, end), timezone)
                                     for connection, timezone in tasks))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for (_, timezone), rows in zip(tasks, results, strict=True):
        grouped[timezone].extend(rows)
    return dict(grouped), missing
