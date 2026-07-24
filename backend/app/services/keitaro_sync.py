import asyncio
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.security import decrypt_secret
from app.models import (
    IntegrationConnection,
    KeitaroCampaign,
    KeitaroGroup,
    KeitaroStatDaily,
    MediaRecord,
    Offer,
    Partner,
    Status,
    SyncRun,
    SyncStatus,
    User,
)
from app.services.keitaro import (
    KeitaroClient,
    decimal_value,
    integer_value,
    stat_dimension_key,
)

ClientFactory = Callable[..., KeitaroClient]


class KeitaroSyncEngine:
    """Coordinates reference and statistics synchronization for one connection."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client_factory: ClientFactory = KeitaroClient,
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory

    async def run(self, connection_id: str, run_id: str, mode: str) -> dict:
        connection_uuid = uuid.UUID(connection_id)
        run_uuid = uuid.UUID(run_id)

        async with self.session_factory() as db:
            connection = await db.get(IntegrationConnection, connection_uuid)
            run = await db.get(SyncRun, run_uuid)
            if not connection or not run:
                return {"status": "missing"}
            run.status = SyncStatus.running
            run.started_at = datetime.now(UTC)
            run.details = {"phase": "references"}
            await db.commit()
            api_key = decrypt_secret(connection.api_key_encrypted)
            config = {
                "id": connection.id,
                "workspace_id": connection.workspace_id,
                "base_url": connection.base_url,
                "timezone": connection.timezone or "UTC",
                "buyer_sub_id": max(min(connection.buyer_sub_id or 1, 10), 1),
                "lookback_days": max(connection.lookback_days or 2, 1),
                "checkpoint_at": connection.checkpoint_at,
            }

        client = self.client_factory(config["base_url"], api_key)
        try:
            reference_counts = await self._sync_references(config, client)
            start, end = self._date_window(mode, config)
            days = (end - start).days + 1
            total_rows = sum(reference_counts.values())
            daily_counts: dict[str, int] = {}

            for index in range(days):
                current_day = start + timedelta(days=index)
                processed = await self._sync_day(config, client, current_day)
                daily_counts[current_day.isoformat()] = processed
                total_rows += processed
                async with self.session_factory() as db:
                    run = await db.get(SyncRun, run_uuid)
                    if not run:
                        return {"status": "missing"}
                    run.rows_processed = total_rows
                    run.progress_pct = int(((index + 1) / days) * 100)
                    run.details = {
                        "phase": "statistics",
                        "current_day": current_day.isoformat(),
                        "range_from": start.isoformat(),
                        "range_to": end.isoformat(),
                        "references": reference_counts,
                        "days_completed": index + 1,
                        "days_total": days,
                    }
                    await db.commit()

            now = datetime.now(UTC)
            async with self.session_factory() as db:
                run = await db.get(SyncRun, run_uuid)
                connection = await db.get(IntegrationConnection, connection_uuid)
                if not run or not connection:
                    return {"status": "missing"}
                run.status = SyncStatus.success
                run.progress_pct = 100
                run.rows_processed = total_rows
                run.finished_at = now
                run.error = None
                run.details = {
                    "phase": "complete",
                    "range_from": start.isoformat(),
                    "range_to": end.isoformat(),
                    "references": reference_counts,
                    "daily_rows": daily_counts,
                }
                connection.last_sync_at = now
                connection.checkpoint_at = now
                await db.commit()
            await _invalidate_dashboard_cache(config["workspace_id"])
            return {
                "status": "success",
                "rows_processed": total_rows,
                "range_from": start.isoformat(),
                "range_to": end.isoformat(),
            }
        except Exception as exc:
            async with self.session_factory() as db:
                run = await db.get(SyncRun, run_uuid)
                if run:
                    run.status = SyncStatus.failed
                    run.finished_at = datetime.now(UTC)
                    run.error = _safe_sync_error(exc)
                    details = dict(run.details or {})
                    details["phase"] = "failed"
                    run.details = details
                    await db.commit()
            raise

    @staticmethod
    def _date_window(mode: str, config: dict) -> tuple[date, date]:
        today = datetime.now(UTC).date()
        if mode == "backfill":
            return today - timedelta(days=89), today
        checkpoint = config.get("checkpoint_at")
        if checkpoint:
            return (
                checkpoint.date() - timedelta(days=config["lookback_days"]),
                today,
            )
        return today - timedelta(days=config["lookback_days"]), today

    async def _sync_references(self, config: dict, client: KeitaroClient) -> dict[str, int]:
        networks, offer_groups, campaign_groups, offers, campaigns = await asyncio.gather(
            client.affiliate_networks(),
            client.groups("offers"),
            client.groups("campaigns"),
            client.offers(),
            client.campaigns(),
        )
        async with self.session_factory() as db:
            offer_group_map = await _upsert_groups(
                db, config, "offers", offer_groups
            )
            campaign_group_map = await _upsert_groups(
                db, config, "campaigns", campaign_groups
            )
            partner_map = await _upsert_partners(db, config, networks)
            await _upsert_offers(db, config, offers, partner_map, offer_group_map)
            await _upsert_campaigns(db, config, campaigns, campaign_group_map)
            await db.commit()
        return {
            "affiliate_networks": len(networks),
            "offer_groups": len(offer_groups),
            "campaign_groups": len(campaign_groups),
            "offers": len(offers),
            "campaigns": len(campaigns),
        }

    async def _sync_day(
        self,
        config: dict,
        client: KeitaroClient,
        current_day: date,
    ) -> int:
        rows = await client.report(
            current_day,
            current_day,
            timezone=config["timezone"],
        )
        async with self.session_factory() as db:
            users = list(
                (
                    await db.execute(
                        select(User).where(User.workspace_id == config["workspace_id"])
                    )
                ).scalars()
            )
            offers = list(
                (
                    await db.execute(
                        select(Offer).where(Offer.connection_id == config["id"])
                    )
                ).scalars()
            )
            campaigns = list(
                (
                    await db.execute(
                        select(KeitaroCampaign).where(
                            KeitaroCampaign.connection_id == config["id"]
                        )
                    )
                ).scalars()
            )
            users_by_login = {user.login.strip().lower(): user for user in users}
            users_by_group: dict[str, list[User]] = defaultdict(list)
            for user in users:
                if user.keitaro_company_group:
                    users_by_group[user.keitaro_company_group.strip().lower()].append(user)
            offers_by_external = {offer.external_id: offer for offer in offers}
            campaigns_by_external = {
                campaign.external_id: campaign for campaign in campaigns
            }

            aggregates: dict[tuple[uuid.UUID, uuid.UUID], dict] = {}
            for row in rows:
                await _upsert_stat(db, config, current_day, row)
                buyer = _resolve_buyer(
                    row,
                    config["buyer_sub_id"],
                    users_by_login,
                    users_by_group,
                    campaigns_by_external,
                )
                offer = offers_by_external.get(str(row.get("offer_id") or ""))
                if not buyer or not offer:
                    continue
                key = (buyer.id, offer.id)
                aggregate = aggregates.setdefault(
                    key,
                    {
                        "buyer": buyer,
                        "offer": offer,
                        "clicks": 0,
                        "unique_clicks": 0,
                        "leads": 0,
                        "sales": 0,
                        "rejected": 0,
                        "cost": Decimal("0"),
                        "revenue": Decimal("0"),
                    },
                )
                aggregate["clicks"] += integer_value(row.get("clicks"))
                aggregate["unique_clicks"] += integer_value(
                    row.get("campaign_unique_clicks")
                    if row.get("campaign_unique_clicks") is not None
                    else row.get("unique_clicks")
                )
                aggregate["leads"] += integer_value(
                    row.get("leads")
                    if row.get("leads") is not None
                    else row.get("registrations")
                )
                aggregate["sales"] += integer_value(
                    row.get("sales")
                    if row.get("sales") is not None
                    else row.get("conversions")
                )
                aggregate["rejected"] += integer_value(row.get("rejected"))
                aggregate["cost"] += decimal_value(row.get("cost"))
                aggregate["revenue"] += decimal_value(row.get("revenue"))

            for aggregate in aggregates.values():
                media = await db.scalar(
                    select(MediaRecord).where(
                        MediaRecord.workspace_id == config["workspace_id"],
                        MediaRecord.record_date == current_day,
                        MediaRecord.buyer_id == aggregate["buyer"].id,
                        MediaRecord.offer_id == aggregate["offer"].id,
                    )
                )
                if not media:
                    media = MediaRecord(
                        workspace_id=config["workspace_id"],
                        record_date=current_day,
                        buyer_id=aggregate["buyer"].id,
                        offer_id=aggregate["offer"].id,
                    )
                    db.add(media)
                media.source = "keitaro"
                media.installs = aggregate["unique_clicks"]
                media.registrations = aggregate["leads"]
                media.ftd = aggregate["sales"]
                media.revenue = aggregate["revenue"]
                media.spend_calculated = aggregate["cost"]
                media.external_payload = {
                    "clicks": aggregate["clicks"],
                    "unique_clicks": aggregate["unique_clicks"],
                    "rejected": aggregate["rejected"],
                    "keitaro_cost": str(aggregate["cost"]),
                    "connection_id": str(config["id"]),
                }
            await db.commit()
        return len(rows)


async def _upsert_groups(
    db: AsyncSession,
    config: dict,
    resource_type: str,
    rows: list[dict],
) -> dict[str, str]:
    existing = list(
        (
            await db.execute(
                select(KeitaroGroup).where(
                    KeitaroGroup.connection_id == config["id"],
                    KeitaroGroup.resource_type == resource_type,
                )
            )
        ).scalars()
    )
    by_external = {item.external_id: item for item in existing}
    names: dict[str, str] = {}
    for row in rows:
        external_id = str(row.get("id") or "")
        if not external_id:
            continue
        item = by_external.get(external_id)
        if not item:
            item = KeitaroGroup(
                workspace_id=config["workspace_id"],
                connection_id=config["id"],
                resource_type=resource_type,
                external_id=external_id,
            )
            db.add(item)
        item.name = str(row.get("name") or f"Group {external_id}")
        item.position = integer_value(row.get("position"))
        names[external_id] = item.name
    return names


async def _upsert_partners(
    db: AsyncSession,
    config: dict,
    rows: list[dict],
) -> dict[str, Partner]:
    existing = list(
        (
            await db.execute(
                select(Partner).where(Partner.connection_id == config["id"])
            )
        ).scalars()
    )
    by_external = {item.external_id: item for item in existing}
    seen: set[str] = set()
    for row in rows:
        external_id = str(row.get("id") or "")
        if not external_id:
            continue
        seen.add(external_id)
        item = by_external.get(external_id)
        if not item:
            item = Partner(
                workspace_id=config["workspace_id"],
                connection_id=config["id"],
                external_id=external_id,
            )
            db.add(item)
            by_external[external_id] = item
        item.name = str(row.get("name") or f"Partner {external_id}")
        item.status = _status(row.get("state"))
    for external_id, item in by_external.items():
        if external_id not in seen:
            item.status = Status.inactive
    await db.flush()
    return by_external


async def _upsert_offers(
    db: AsyncSession,
    config: dict,
    rows: list[dict],
    partners: dict[str, Partner],
    groups: dict[str, str],
) -> None:
    existing = list(
        (
            await db.execute(select(Offer).where(Offer.connection_id == config["id"]))
        ).scalars()
    )
    by_external = {item.external_id: item for item in existing}
    seen: set[str] = set()
    for row in rows:
        external_id = str(row.get("id") or "")
        if not external_id:
            continue
        seen.add(external_id)
        item = by_external.get(external_id)
        if not item:
            item = Offer(
                workspace_id=config["workspace_id"],
                connection_id=config["id"],
                external_id=external_id,
            )
            db.add(item)
            by_external[external_id] = item
        partner_id = str(row.get("affiliate_network_id") or "")
        group_id = str(row.get("group_id") or "")
        item.name = str(row.get("name") or f"Offer {external_id}")
        item.partner_id = partners[partner_id].id if partner_id in partners else None
        item.group_name = groups.get(group_id)
        item.geo = _offer_geo(row)
        item.status = _status(row.get("state"))
    for external_id, item in by_external.items():
        if external_id not in seen:
            item.status = Status.inactive


async def _upsert_campaigns(
    db: AsyncSession,
    config: dict,
    rows: list[dict],
    groups: dict[str, str],
) -> None:
    existing = list(
        (
            await db.execute(
                select(KeitaroCampaign).where(
                    KeitaroCampaign.connection_id == config["id"]
                )
            )
        ).scalars()
    )
    by_external = {item.external_id: item for item in existing}
    seen: set[str] = set()
    for row in rows:
        external_id = str(row.get("id") or "")
        if not external_id:
            continue
        seen.add(external_id)
        item = by_external.get(external_id)
        if not item:
            item = KeitaroCampaign(
                workspace_id=config["workspace_id"],
                connection_id=config["id"],
                external_id=external_id,
            )
            db.add(item)
            by_external[external_id] = item
        group_id = str(row.get("group_id") or "")
        item.name = str(row.get("name") or f"Campaign {external_id}")
        item.group_external_id = group_id or None
        item.group_name = groups.get(group_id)
        item.traffic_source_external_id = (
            str(row.get("traffic_source_id")) if row.get("traffic_source_id") else None
        )
        item.cost_type = str(row.get("cost_type") or "") or None
        item.status = _status(row.get("state"))
        item.external_payload = {
            "alias": row.get("alias"),
            "cost_value": row.get("cost_value"),
            "cost_currency": row.get("cost_currency"),
            "cost_auto": row.get("cost_auto"),
        }
    for external_id, item in by_external.items():
        if external_id not in seen:
            item.status = Status.inactive


async def _upsert_stat(
    db: AsyncSession,
    config: dict,
    current_day: date,
    row: dict,
) -> None:
    key = stat_dimension_key(row)
    stat = await db.scalar(
        select(KeitaroStatDaily).where(
            KeitaroStatDaily.connection_id == config["id"],
            KeitaroStatDaily.dimension_key == key,
        )
    )
    if not stat:
        stat = KeitaroStatDaily(
            workspace_id=config["workspace_id"],
            connection_id=config["id"],
            record_date=current_day,
            dimension_key=key,
        )
        db.add(stat)
    stat.offer_external_id = str(row.get("offer_id") or "") or None
    stat.campaign_external_id = str(row.get("campaign_id") or "") or None
    stat.country_code = str(row.get("country_code") or "")[:12] or None
    stat.sub_values = {
        f"sub{index}": row.get(f"sub_id_{index}") for index in range(1, 11)
    }
    stat.clicks = integer_value(row.get("clicks"))
    stat.unique_clicks = integer_value(
        row.get("campaign_unique_clicks")
        if row.get("campaign_unique_clicks") is not None
        else row.get("unique_clicks")
    )
    stat.conversions = integer_value(row.get("conversions"))
    stat.leads = integer_value(
        row.get("leads") if row.get("leads") is not None else row.get("registrations")
    )
    stat.registrations = stat.leads
    stat.sales = integer_value(
        row.get("sales") if row.get("sales") is not None else row.get("conversions")
    )
    stat.rejected = integer_value(row.get("rejected"))
    stat.cost = decimal_value(row.get("cost"))
    stat.revenue = decimal_value(row.get("revenue"))


def _resolve_buyer(
    row: dict,
    buyer_sub_id: int,
    users_by_login: dict[str, User],
    users_by_group: dict[str, list[User]],
    campaigns: dict[str, KeitaroCampaign],
) -> User | None:
    login = str(row.get(f"sub_id_{buyer_sub_id}") or "").strip().lower()
    if login and login in users_by_login:
        return users_by_login[login]
    campaign = campaigns.get(str(row.get("campaign_id") or ""))
    if campaign and campaign.group_name:
        matches = users_by_group.get(campaign.group_name.strip().lower(), [])
        if len(matches) == 1:
            return matches[0]
    return None


def _offer_geo(row: dict) -> str | None:
    country = row.get("country") or row.get("geo")
    if isinstance(country, list):
        country = country[0] if country else None
    value = str(country or "").strip().upper()
    return value[:12] or None


def _status(value: object) -> Status:
    return Status.active if str(value or "active").lower() == "active" else Status.inactive


def _safe_sync_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:500]}"


async def _invalidate_dashboard_cache(workspace_id: uuid.UUID) -> None:
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        async for key in redis.scan_iter(match=f"dashboard:{workspace_id}:*"):
            await redis.delete(key)
        await redis.aclose()
    except Exception:
        return
