import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import business_today
from app.core.config import settings
from app.core.security import decrypt_secret
from app.models import (
    AlertRule,
    IntegrationConnection,
    KeitaroCampaign,
    KeitaroConversion,
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
from app.services import finance_spend
from app.services.geo import normalize_geo
from app.services.keitaro import (
    CLICK_LOOKBACK_DAYS,
    KeitaroClient,
    decimal_value,
    integer_value,
    stat_dimension_key,
)

ClientFactory = Callable[..., KeitaroClient]
logger = logging.getLogger(__name__)


class KeitaroSyncEngine:
    """Coordinates reference and statistics synchronization for one connection."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client_factory: ClientFactory = KeitaroClient,
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory

    async def run(
        self, connection_id: str, run_id: str, mode: str, days: int | None = None
    ) -> dict:
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
                "timezone": connection.timezone or "Europe/Moscow",
                "buyer_sub_id": max(min(connection.buyer_sub_id or 1, 10), 1),
                "lookback_days": max(connection.lookback_days or 2, 1),
                "checkpoint_at": connection.checkpoint_at,
            }

        client = self.client_factory(config["base_url"], api_key)
        try:
            reference_counts = await self._sync_references(config, client)
            start, end = self._date_window(mode, config, days)
            days = (end - start).days + 1
            total_rows = sum(reference_counts.values())
            daily_counts: dict[str, int] = {}

            for index in range(days):
                current_day = start + timedelta(days=index)
                # Пересинхронизация заменяет день целиком: то, чего в свежем
                # отчёте уже нет, уходит из статистики и Медиаборда.
                processed = await self._sync_day(
                    config, client, current_day, purge=mode == "resync"
                )
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

            # Журнал конверсий — отдельным шагом и в самом конце: он нужен
            # только уведомлениям о депозитах, и его отказ не должен рушить
            # уже собранную статистику.
            conversions = await self._sync_conversions(config, client, start, end)
            total_rows += conversions

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
                    "conversions": conversions,
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
    def _date_window(
        mode: str, config: dict, days: int | None = None
    ) -> tuple[date, date]:
        today = business_today()
        if mode == "resync":
            # Период выбирают в настройках подключения; сегодня входит в него.
            return today - timedelta(days=max(days or 30, 1) - 1), today
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
            await finance_spend.lock_workspace(db, config["workspace_id"])
            offer_group_map = await _upsert_groups(
                db, config, "offers", offer_groups
            )
            campaign_group_map = await _upsert_groups(
                db, config, "campaigns", campaign_groups
            )
            partner_map = await _upsert_partners(db, config, networks)
            geo_changed = await _upsert_offers(db, config, offers, partner_map, offer_group_map)
            await _upsert_campaigns(db, config, campaigns, campaign_group_map)
            if geo_changed:
                # A directory edit applies to historical media records too,
                # including months outside the statistics lookback window.
                await finance_spend.refresh_workspace(db, config["workspace_id"])
            await db.commit()
        return {
            "affiliate_networks": len(networks),
            "offer_groups": len(offer_groups),
            "campaign_groups": len(campaign_groups),
            "offers": len(offers),
            "campaigns": len(campaigns),
        }

    async def refresh_offer_catalog(self, connection_id: str) -> dict[str, int]:
        """Быстро обновить офферы и необходимые им справочники без статистики."""
        connection_uuid = uuid.UUID(connection_id)
        async with self.session_factory() as db:
            connection = await db.get(IntegrationConnection, connection_uuid)
            if not connection or connection.kind != "keitaro":
                return {"offer_groups": 0, "affiliate_networks": 0, "offers": 0}
            config = {
                "id": connection.id,
                "workspace_id": connection.workspace_id,
                "base_url": connection.base_url,
                "timezone": connection.timezone or "Europe/Moscow",
            }
            api_key = decrypt_secret(connection.api_key_encrypted)

        client = self.client_factory(config["base_url"], api_key)
        networks, offer_groups, offers = await asyncio.gather(
            client.affiliate_networks(),
            client.groups("offers"),
            client.offers(),
        )
        async with self.session_factory() as db:
            await finance_spend.lock_workspace(db, config["workspace_id"])
            offer_group_map = await _upsert_groups(db, config, "offers", offer_groups)
            partner_map = await _upsert_partners(db, config, networks)
            geo_changed = await _upsert_offers(
                db, config, offers, partner_map, offer_group_map
            )
            if geo_changed:
                await finance_spend.refresh_workspace(db, config["workspace_id"])
            await db.commit()
        await _invalidate_dashboard_cache(config["workspace_id"])
        return {
            "offer_groups": len(offer_groups),
            "affiliate_networks": len(networks),
            "offers": len(offers),
        }

    async def poll_conversions(self) -> int:
        """Только журнал конверсий, коротким окном и часто.

        Полная синхронизация тянет справочники и отчёт по дням — гонять её раз
        в минуту нельзя. А уведомление о депозите должно приходить сразу, и
        ждать общего круга в четверть часа для него бессмысленно. Поэтому
        журнал забирается отдельной лёгкой задачей: один запрос на подключение.

        Окно — вчера и сегодня: конверсия приходит с временем трекера, и сразу
        после полуночи свежий депозит ещё числится вчерашним днём.

        Ошибки не поднимаем: этот опрос вспомогательный, и его отказ не должен
        ронять задачу целиком — следующая минута попробует снова.
        """
        async with self.session_factory() as db:
            connections = list(
                (
                    await db.execute(
                        select(IntegrationConnection).where(
                            IntegrationConnection.kind == "keitaro",
                            IntegrationConnection.status == Status.active,
                        )
                    )
                ).scalars()
            )
            targets = [
                (
                    {
                        "id": connection.id,
                        "workspace_id": connection.workspace_id,
                        "base_url": connection.base_url,
                        "timezone": connection.timezone or "Europe/Moscow",
                    },
                    decrypt_secret(connection.api_key_encrypted),
                )
                for connection in connections
            ]

        saved = 0
        for config, api_key in targets:
            try:
                today = datetime.now(ZoneInfo(config["timezone"])).date()
            except Exception:
                today = business_today()
            client = self.client_factory(config["base_url"], api_key)
            try:
                saved += await self._sync_conversions(
                    config, client, today - timedelta(days=1), today
                )
            except Exception as exc:
                logger.warning(
                    "Keitaro conversion polling failed for connection %s: %s",
                    config["id"],
                    _safe_sync_error(exc),
                )
                continue
        return saved

    async def _sync_conversions(
        self, config: dict, client: KeitaroClient, start: date, end: date
    ) -> int:
        """Журнал конверсий за окно синхронизации.

        Ошибка журнала не роняет полную синхронизацию: Медиаборд и Финансы
        живут на дневном отчёте и без него. Но ошибку обязательно пишем в лог,
        иначе фоновая задача выглядит успешной, хотя алерты не работают.

        Окно берём не длиннее недели: журнал построчный, и backfill за девяносто
        дней вытянул бы сотни тысяч строк ради уведомлений, которые всё равно
        относятся к сегодняшнему дню.
        """
        first = max(start, end - timedelta(days=6))
        try:
            rows = await client.conversions(first, end, timezone=config["timezone"])
        except Exception as exc:
            logger.warning(
                "Keitaro conversion log failed for connection %s: %s",
                config["id"],
                _safe_sync_error(exc),
            )
            return 0
        if not rows:
            return 0
        async with self.session_factory() as db:
            # Полная синхронизация и минутный опрос конверсий могут совпасть.
            # Блокируем обработку журнала по подключению, чтобы оба процесса
            # не увидели одну новую конверсию отсутствующей и не попытались
            # одновременно вставить её под уникальным ключом.
            await db.execute(
                select(IntegrationConnection.id)
                .where(IntegrationConnection.id == config["id"])
                .with_for_update()
            )
            had_conversions = bool(
                await db.scalar(
                    select(func.count(KeitaroConversion.id)).where(
                        KeitaroConversion.connection_id == config["id"]
                    )
                )
            )
            campaigns = {
                row.external_id: row
                for row in (
                    await db.execute(
                        select(KeitaroCampaign).where(
                            KeitaroCampaign.connection_id == config["id"]
                        )
                    )
                ).scalars()
            }
            existing = {
                row.external_id: row
                for row in (
                    await db.execute(
                        select(KeitaroConversion).where(
                            KeitaroConversion.connection_id == config["id"]
                        )
                    )
                ).scalars()
            }
            click_candidates: list[str] = []
            for row in rows:
                if str(row.get("status") or "").strip().lower() != "sale":
                    continue
                external_id = _conversion_external_id(row)
                stored = existing.get(external_id)
                if stored is not None and stored.click_at is not None:
                    continue
                sub_id = str(row.get("sub_id") or "").strip()
                if sub_id:
                    click_candidates.append(sub_id)
            if click_candidates:
                try:
                    click_times = await client.click_times(
                        click_candidates,
                        # Депозит может прийти через недели после клика. Сам
                        # фильтр идёт по точным sub_id, поэтому широкое окно не
                        # превращает запрос в выгрузку всего журнала.
                        start=first - timedelta(days=CLICK_LOOKBACK_DAYS),
                        end=end,
                        timezone=config["timezone"],
                    )
                except Exception as exc:
                    # Время клика улучшает сообщение, но временный отказ
                    # clicks/log не должен задержать сам депозитный алерт.
                    logger.warning(
                        "Keitaro click lookup failed for connection %s: %s",
                        config["id"],
                        _safe_sync_error(exc),
                    )
                    click_times = {}
                for row in rows:
                    if not row.get("click_datetime"):
                        row["click_datetime"] = click_times.get(
                            str(row.get("sub_id") or "").strip()
                        )
            observed_at = datetime.now(UTC)
            saved = await _upsert_conversions(
                db, config, rows, campaigns, observed_at=observed_at
            )
            # Первый исправный опрос импортирует историю за несколько дней.
            # Она нужна для дедупликации, но не должна высыпаться в Telegram
            # как сотни "новых" депозитов. С этого момента правила увидят
            # только новые строки и переходы lead -> sale.
            if not had_conversions and saved:
                rules = list(
                    (
                        await db.execute(
                            select(AlertRule).where(
                                AlertRule.workspace_id == config["workspace_id"],
                                AlertRule.kind == "deposit",
                                AlertRule.status == Status.active,
                            )
                        )
                    ).scalars()
                )
                for rule in rules:
                    rule.cursor_at = observed_at
            await db.commit()
        return saved

    async def _sync_day(
        self,
        config: dict,
        client: KeitaroClient,
        current_day: date,
        purge: bool = False,
    ) -> int:
        rows = await client.report(
            current_day,
            current_day,
            timezone=config["timezone"],
        )
        async with self.session_factory() as db:
            await finance_spend.lock_workspace(db, config["workspace_id"])
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

            # Keitaro не возвращает в отчёте оффер, если у него за день все
            # метрики равны нулю. Но сам оффер уже есть в актуальном справочнике
            # и нужен баеру в Медиаборде, чтобы внести расход вручную. Дополняем
            # отчёт нулевыми строками по группе оффера — только когда группа
            # однозначно соответствует одному активному пользователю.
            offers_group = settings.keitaro_offers_group.strip().lower()
            for offer in offers:
                group_name = (offer.group_name or "").strip().lower()
                if (
                    offer.keitaro_state != Status.active
                    or not group_name
                    or group_name == offers_group
                    or offer.created_at.date() > current_day
                ):
                    continue
                buyers = [
                    user
                    for user in users_by_group.get(group_name, [])
                    if user.status == Status.active
                ]
                if len(buyers) != 1:
                    continue
                key = (buyers[0].id, offer.id)
                aggregates.setdefault(
                    key,
                    {
                        "buyer": buyers[0],
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
                # Fields a user filled in by hand stay pinned (ТЗ 2.5).
                pinned = set(media.manual_fields or [])
                if "installs" not in pinned:
                    media.installs = aggregate["unique_clicks"]
                if "registrations" not in pinned:
                    media.registrations = aggregate["leads"]
                if "ftd" not in pinned:
                    media.ftd = aggregate["sales"]
                if "revenue" not in pinned:
                    media.revenue = aggregate["revenue"]
                # SPEND is owned by the "Агенты и платёжки" block (ТЗ 2.4.4), so the
                # Keitaro cost is kept for reference only and never overwrites it.
                media.external_payload = {
                    "clicks": aggregate["clicks"],
                    "unique_clicks": aggregate["unique_clicks"],
                    "rejected": aggregate["rejected"],
                    "keitaro_cost": str(aggregate["cost"]),
                    "connection_id": str(config["id"]),
                }
            # Спенд книги баера собирается из этих же записей: после синка он
            # обновляется сам, чтобы в финансах не осталась вчерашняя цифра.
            touched = {item["buyer"].id for item in aggregates.values()}
            if purge:
                touched |= await _clear_stale_day(db, config, current_day, rows, aggregates)
            for buyer_id in touched:
                await finance_spend.refresh_for_record(
                    db, config["workspace_id"], buyer_id, current_day
                )
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
    seen: set[str] = set()
    for row in rows:
        external_id = str(row.get("id") or "")
        if not external_id:
            continue
        seen.add(external_id)
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
    # Группа — актуальный справочник, а не историческая сущность. Кампании и
    # офферы сохраняют прежнее название сами, поэтому отсутствующие в свежем
    # ответе Keitaro строки здесь только возвращали удалённые группы в формы.
    stale_ids = [item.id for key, item in by_external.items() if key not in seen]
    if stale_ids:
        await db.execute(delete(KeitaroGroup).where(KeitaroGroup.id.in_(stale_ids)))
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
        if not item.status_overridden:
            item.status = _status(row.get("state"))
    for external_id, item in by_external.items():
        if external_id not in seen and not item.status_overridden:
            item.status = Status.inactive
    await db.flush()
    return by_external


async def _upsert_offers(
    db: AsyncSession,
    config: dict,
    rows: list[dict],
    partners: dict[str, Partner],
    groups: dict[str, str],
) -> bool:
    existing = list(
        (
            await db.execute(select(Offer).where(Offer.connection_id == config["id"]))
        ).scalars()
    )
    by_external = {item.external_id: item for item in existing}
    seen: set[str] = set()
    geo_changed = False
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
        group_id = str(row.get("group_id") or "")
        item.name = str(row.get("name") or f"Offer {external_id}")
        item.partner_id = await _resolve_offer_partner(db, config, row, partners)
        item.group_name = groups.get(group_id)
        geo = normalize_geo(_offer_country(row))
        geo_changed = geo_changed or item.geo != geo
        item.geo = geo
        # `status` is our own workflow column and Keitaro never touches it.
        item.keitaro_state = _status(row.get("state"))
    for external_id, item in by_external.items():
        if external_id not in seen:
            item.keitaro_state = Status.inactive
    return geo_changed


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


async def _clear_stale_day(
    db: AsyncSession,
    config: dict,
    current_day: date,
    rows: list[dict],
    aggregates: dict,
) -> set[uuid.UUID]:
    """Убрать за день то, чего в свежем отчёте Keitaro уже нет.

    Обычная синхронизация только дописывает: строка, пропавшая из отчёта
    (трекер пересчитал день, кампанию перенесли, конверсию отклонили), так и
    оставалась в статистике и Медиаборде со старыми цифрами. Ради таких
    расхождений пересинхронизацию и запускают.

    Строки статистики этого подключения за день, которых нет в отчёте,
    удаляются. У записей Медиаборда из этого трекера обнуляются только метрики
    Keitaro: ручные поля и расход агентов — данные человека, их пересинхронизация
    не трогает. Возвращает баеров, чьи книги нужно пересчитать.
    """
    fresh = {stat_dimension_key(row) for row in rows}
    stats = (
        await db.execute(
            select(KeitaroStatDaily).where(
                KeitaroStatDaily.connection_id == config["id"],
                KeitaroStatDaily.record_date == current_day,
            )
        )
    ).scalars()
    for stat in list(stats):
        if stat.dimension_key not in fresh:
            await db.delete(stat)

    kept = {(item["buyer"].id, item["offer"].id) for item in aggregates.values()}
    records = (
        await db.execute(
            select(MediaRecord).where(
                MediaRecord.workspace_id == config["workspace_id"],
                MediaRecord.record_date == current_day,
                MediaRecord.source == "keitaro",
            )
        )
    ).scalars()
    touched: set[uuid.UUID] = set()
    for media in list(records):
        payload = dict(media.external_payload or {})
        if payload.get("connection_id") != str(config["id"]):
            continue
        if (media.buyer_id, media.offer_id) in kept:
            continue
        pinned = set(media.manual_fields or [])
        for field in ("installs", "registrations", "ftd"):
            if field not in pinned:
                setattr(media, field, 0)
        if "revenue" not in pinned:
            media.revenue = Decimal("0")
        payload.update({"clicks": 0, "unique_clicks": 0, "rejected": 0, "keitaro_cost": "0"})
        media.external_payload = payload
        touched.add(media.buyer_id)
    return touched


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


def _offer_country(row: dict) -> object:
    return row.get("country") or row.get("geo") or row.get("country_code")


def _network_external_id(row: dict) -> str:
    nested = row.get("affiliate_network")
    if isinstance(nested, dict):
        return str(nested.get("id") or "")
    return str(row.get("affiliate_network_id") or "")


def _network_name(row: dict) -> str:
    nested = row.get("affiliate_network")
    if isinstance(nested, dict):
        candidate = nested.get("name")
    elif isinstance(nested, str):
        candidate = nested
    else:
        candidate = row.get("affiliate_network_name") or row.get("network_name")
    return " ".join(str(candidate or "").split())[:200]


async def _resolve_offer_partner(
    db: AsyncSession,
    config: dict,
    row: dict,
    partners: dict[str, Partner],
) -> uuid.UUID | None:
    """Find the affiliate network a Keitaro offer belongs to.

    The tracker exposes it in more than one shape depending on version and on
    what the API key may read, so an offer can name its network without that
    network appearing in `/affiliate_networks`. Falling back to the name keeps
    the Партнёрка column filled instead of showing a dash.
    """
    external_id = _network_external_id(row)
    if external_id and external_id in partners:
        return partners[external_id].id
    name = _network_name(row)
    if not name:
        return None
    partner = await db.scalar(
        select(Partner).where(
            Partner.connection_id == config["id"],
            func.lower(Partner.name) == name.lower(),
        )
    )
    if not partner:
        partner = Partner(
            workspace_id=config["workspace_id"],
            connection_id=config["id"],
            external_id=f"name:{name.lower()[:94]}",
            name=name,
        )
        db.add(partner)
        await db.flush()
        partners[partner.external_id] = partner
    return partner.id


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


def _conversion_external_id(row: dict) -> str:
    return str(
        row.get("conversion_id")
        or row.get("event_id")
        or row.get("id")
        or row.get("subid")
        or ""
    ).strip()


def _conversion_time(
    value: object, timezone_name: str = "Europe/Moscow"
) -> datetime | None:
    """Время из журнала конверсий.

    Трекер отдаёт строку без зоны, уже пересчитанную в timezone запроса.
    Привязываем её к этой зоне и только затем переводим в UTC для хранения.
    """
    if not value:
        return None
    text = str(value).strip().replace(" ", "T").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo:
        return parsed.astimezone(UTC)
    try:
        zone = ZoneInfo(timezone_name or "Europe/Moscow")
    except Exception:
        zone = ZoneInfo("Europe/Moscow")
    return parsed.replace(tzinfo=zone).astimezone(UTC)


async def _upsert_conversions(
    db: AsyncSession,
    config: dict,
    rows: list[dict],
    campaigns: dict,
    *,
    observed_at: datetime | None = None,
) -> int:
    """Сохранить новые конверсии, у известных — подхватить смену статуса.

    Строку целиком не переписываем: уведомление о депозите уходит один раз, и
    перезапись сдвинула бы `seen_at`, из-за чего то же самое ушло бы в чат ещё
    раз. Но статус у конверсии меняется: Keitaro заводит её лидом на
    регистрации и переводит в `sale`, когда игрок пополнил счёт, — тем же
    `conversion_id`. Пропуская известные строки целиком, мы навсегда оставляли
    такой депозит лидом, и уведомление по нему не приходило никогда.

    Поэтому переход в `sale` обрабатывается отдельно: обновляем статус и суммы
    и двигаем `seen_at`, чтобы правило увидело депозит как новый. Строка, уже
    бывшая `sale`, не трогается.
    """
    seen_at = observed_at or datetime.now(UTC)
    existing = {
        row.external_id: row
        for row in (
            await db.execute(
                select(KeitaroConversion).where(
                    KeitaroConversion.connection_id == config["id"]
                )
            )
        ).scalars()
    }
    saved = 0
    for row in rows:
        external_id = _conversion_external_id(row)
        if not external_id:
            continue
        campaign_id = str(row.get("campaign_id") or "").strip() or None
        campaign = campaigns.get(campaign_id) if campaign_id else None
        status = str(row.get("status") or "").strip().lower()[:30]
        seen = existing.get(external_id)
        if seen is not None:
            changed = False
            if not seen.click_at and row.get("click_datetime"):
                seen.click_at = _conversion_time(
                    row.get("click_datetime"), config.get("timezone")
                )
                changed = True
            if seen.status != "sale" and status == "sale":
                seen.status = status
                seen.revenue = decimal_value(row.get("revenue"))
                seen.payout = decimal_value(row.get("payout") or row.get("revenue"))
                # Группу могло не быть в справочнике на момент лида — кампанию
                # заводят и тут же льют. Без неё условие «группа в списке» не
                # сработает, поэтому дозаполняем, раз уж строка в руках.
                if campaign and not seen.campaign_group_id:
                    seen.campaign_group_id = campaign.group_external_id
                    seen.campaign_group_name = campaign.group_name
                seen.seen_at = seen_at
                changed = True
            if changed:
                saved += 1
            continue
        conversion = KeitaroConversion(
            workspace_id=config["workspace_id"],
            connection_id=config["id"],
            external_id=external_id[:120],
            status=status,
            conversion_at=_conversion_time(
                row.get("postback_datetime")
                or row.get("conversion_datetime")
                or row.get("datetime"),
                config.get("timezone"),
            ),
            click_at=_conversion_time(
                row.get("click_datetime"), config.get("timezone")
            ),
            campaign_external_id=campaign_id,
            campaign_name=(
                str(row.get("campaign") or (campaign.name if campaign else "")) or None
            ),
            campaign_group_id=campaign.group_external_id if campaign else None,
            campaign_group_name=campaign.group_name if campaign else None,
            offer_external_id=str(row.get("offer_id") or "").strip() or None,
            offer_name=str(row.get("offer") or "") or None,
            country_code=normalize_geo(row.get("country")),
            revenue=decimal_value(row.get("revenue")),
            payout=decimal_value(row.get("payout") or row.get("revenue")),
            sub_values={
                f"sub_id_{index}": str(row.get(f"sub_id_{index}") or "")
                for index in range(1, 11)
            },
            seen_at=seen_at,
        )
        existing[external_id] = conversion
        db.add(conversion)
        saved += 1
    return saved
