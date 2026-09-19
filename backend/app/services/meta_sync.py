"""Синхронизация одного подключения Meta: кабинеты, объекты и статистика.

Повторяет устройство KeitaroSyncEngine — та же таблица запусков, те же фазы и
тот же принцип идемпотентности: строка статистики опознаётся по хэшу измерений,
поэтому повторный прогон за тот же день переписывает её, а не задваивает.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import business_today
from app.core.config import settings
from app.core.security import decrypt_secret, encrypt_secret
from app.models import (
    IntegrationConnection,
    LaunchStatus,
    MetaAdAccount,
    MetaBusiness,
    MetaEntity,
    MetaFanPage,
    MetaLaunch,
    MetaSocialAccount,
    MetaStatDaily,
    Status,
    SyncRun,
    SyncStatus,
)
from app.services.meta import (
    MetaClient,
    MetaError,
    account_status_label,
    action_counts,
    apply_entity_row,
    decimal_value,
    integer_value,
    money_from_minor,
    stat_dimension_key,
)
from app.services.meta_session import (
    MetaSessionError,
    get_session_manager,
    open_session_access,
)

logger = logging.getLogger("meta_sync")

ClientFactory = Callable[..., MetaClient]
LEVELS = ("campaign", "adset", "ad")
BACKFILL_DAYS = 89
# Стартовое окно для несистемных токенов и пауза между кабинетами. Это не
# осторожность ради осторожности: шквал запросов сразу после выпуска токена —
# самый заметный признак того, что сессией пользуется не человек.
GENTLE_DAYS = 3
GENTLE_PAUSE_SECONDS = 1.5
# Meta досчитывает атрибуцию несколько дней, поэтому окно всегда перекрывает уже
# загруженные дни: вчерашний расход завтра будет другим.
MIN_LOOKBACK_DAYS = 3
# Коды, по которым токен сессии можно обновить через браузерную сессию: 190 —
# токен отозван/истёк, 102 — сессия закрыта. Для системного пользователя обновлять
# нечего — новый токен выдаёт только человек в Business Manager.
TOKEN_DEAD_CODES = {190, 102}
SESSION_RENEW_POLL_SECONDS = 3
# Как статус кампании в Meta ложится на статус залива в CRM. Неизвестные значения
# статус не меняют: лучше оставить прежний, чем соврать.
LAUNCH_STATUS_BY_META = {
    "ACTIVE": LaunchStatus.active,
    "PAUSED": LaunchStatus.paused,
    "CAMPAIGN_PAUSED": LaunchStatus.paused,
    "ADSET_PAUSED": LaunchStatus.paused,
    "DELETED": LaunchStatus.stopped,
    "ARCHIVED": LaunchStatus.stopped,
    "DISAPPROVED": LaunchStatus.failed,
    "WITH_ISSUES": LaunchStatus.failed,
}


class MetaSyncEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client_factory: ClientFactory = MetaClient,
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
            run.details = {"phase": "accounts"}
            await db.commit()
            access_token = decrypt_secret(connection.api_key_encrypted)
            config = {
                "id": connection.id,
                "workspace_id": connection.workspace_id,
                "owner_id": connection.owner_id,
                "business_id": (connection.external_account_id or "").strip() or None,
                "lookback_days": max(connection.lookback_days or 2, MIN_LOOKBACK_DAYS),
                "checkpoint_at": connection.checkpoint_at,
                "auth_method": connection.auth_method,
                "proxy_url": connection.proxy_url,
                "user_agent": connection.user_agent,
            }

        transport = None
        session_owned = False
        try:
            if config.get("auth_method") == "session":
                # Токен сессии Meta принимает только из браузерного контекста
                # (см. SessionHTTPTransport): без живой сессии любой httpx-запрос
                # получит «Invalid request», сколько ни обновляй токен.
                access = await open_session_access(
                    self.session_factory,
                    str(config["id"]),
                    proxy_url=config.get("proxy_url"),
                    user_agent=config.get("user_agent"),
                )
                transport = access["transport"]
                session_owned = access["owned"]
                if access.get("token"):
                    access_token = access["token"]
        except MetaSessionError as exc:
            await self._fail_run(run_uuid, exc)
            raise

        client = self.client_factory(
            access_token,
            proxy=config["proxy_url"],
            user_agent=config["user_agent"],
            transport=transport,
        )
        # Токен сессии (EAAB) живёт, пока жива сессия браузера. Если Meta отбила
        # его (190/102), из живой сессии извлекается новый EAAB — ровно тем
        # маршрутом (прокси/cookies), которым токен и был получен. Обновление
        # пробуем один раз за прогон.
        renewed = False
        try:
            while True:
                try:
                    social = await self._sync_social_graph(config, client)
                    accounts = await self._sync_accounts(config, client, social)
                    start, end = self._date_window(mode, config)
                    totals = {"accounts": len(accounts), "entities": 0, "stat_rows": 0}
                    per_account: dict[str, dict] = {}

                    gentle = config.get("auth_method") != "system_user"
                    for index, account in enumerate(accounts):
                        if gentle and index:
                            # Пауза между кабинетами: двадцать кабинетов подряд без
                            # передышки — это не поведение человека, открывшего кабинет
                            # посмотреть статистику.
                            await asyncio.sleep(GENTLE_PAUSE_SECONDS)
                        entity_counts = await self._sync_entities(config, client, account)
                        stat_rows = await self._sync_insights(config, client, account, start, end)
                        totals["entities"] += sum(entity_counts.values())
                        totals["stat_rows"] += stat_rows
                        per_account[account["external_id"]] = {
                            "name": account["name"],
                            **entity_counts,
                            "stat_rows": stat_rows,
                        }
                        async with self.session_factory() as db:
                            run = await db.get(SyncRun, run_uuid)
                            if not run:
                                return {"status": "missing"}
                            run.rows_processed = totals["entities"] + totals["stat_rows"]
                            run.progress_pct = int(((index + 1) / max(len(accounts), 1)) * 100)
                            run.details = {
                                "phase": "insights",
                                "current_account": account["name"],
                                "range_from": start.isoformat(),
                                "range_to": end.isoformat(),
                                "accounts_completed": index + 1,
                                "accounts_total": len(accounts),
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
                        run.rows_processed = totals["entities"] + totals["stat_rows"]
                        run.finished_at = now
                        run.error = None
                        run.details = {
                            "phase": "complete",
                            "range_from": start.isoformat(),
                            "range_to": end.isoformat(),
                            "totals": totals,
                            "accounts": per_account,
                        }
                        connection.last_sync_at = now
                        connection.checkpoint_at = now
                        await db.commit()
                    return {
                        "status": "success",
                        "rows_processed": totals["entities"] + totals["stat_rows"],
                        "range_from": start.isoformat(),
                        "range_to": end.isoformat(),
                    }
                except MetaError as exc:
                    if (
                        not renewed
                        and exc.error_code in TOKEN_DEAD_CODES
                        and config.get("auth_method") == "session"
                    ):
                        token = await self._renew_session_token(config)
                        if token:
                            renewed = True
                            access_token = token
                            client = self.client_factory(
                                access_token,
                                proxy=config["proxy_url"],
                                user_agent=config["user_agent"],
                                transport=transport,
                            )
                            continue
                    await self._fail_run(run_uuid, exc)
                    raise
                except Exception as exc:
                    await self._fail_run(run_uuid, exc)
                    raise
        finally:
            if session_owned:
                # Браузер, восстановленный ради этого прогона, закрываем. Чужую
                # живую сессию (мастер подключения) не трогаем.
                try:
                    await get_session_manager().close(str(config["id"]))
                except Exception:  # noqa: BLE001 — очистка не должна маскировать результат
                    pass

    async def _fail_run(self, run_uuid: uuid.UUID, exc: Exception) -> None:
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

    async def _renew_session_token(self, config: dict) -> str | None:
        """Извлекает новый EAAB-токен из живой сессии и сохраняет его в подключение.

        Возвращает новый токен или None, если обновить нельзя. Сессию НЕ закрывает:
        повторный запрос пойдёт через тот же браузерный контекст — закрывается она
        в конце прогона в run().
        """
        manager = get_session_manager()
        session_id = str(config["id"])
        state = manager.get(session_id)
        if not state or not state.context or not state.page:
            logger.info("session renewal skipped: no live session for %s", config["id"])
            return None
        deadline = time.time() + settings.meta_login_timeout_sec
        while time.time() < deadline:
            if state.status == "error":
                logger.warning("session renewal failed: %s", state.error)
                return None
            if state.token:
                token = state.token
                async with self.session_factory() as db:
                    connection = await db.get(IntegrationConnection, config["id"])
                    if connection:
                        connection.api_key_encrypted = encrypt_secret(token)
                        await db.commit()
                logger.info("session renewal succeeded for connection %s", config["id"])
                return token
            try:
                result = await manager.extract_token(session_id)
                if result.get("token"):
                    continue
            except Exception as exc:
                logger.warning("token extraction during renewal failed: %s", exc)
            await asyncio.sleep(SESSION_RENEW_POLL_SECONDS)
        logger.warning("session renewal timed out for connection %s", config["id"])
        return None

    @staticmethod
    def _date_window(mode: str, config: dict) -> tuple[date, date]:
        today = business_today()
        if mode == "backfill":
            # Полная выкачка за три месяца — это сотни запросов подряд. Токену
            # системного пользователя это нормально: он для того и сделан. Токен
            # из сессии аккаунта такой всплеск выдаёт за угон, и Meta режет не
            # токен, а аккаунт — поэтому ему даём короткое окно.
            days = BACKFILL_DAYS if config.get("auth_method") == "system_user" else GENTLE_DAYS
            return today - timedelta(days=days), today
        checkpoint = config.get("checkpoint_at")
        if checkpoint:
            return checkpoint.date() - timedelta(days=config["lookback_days"]), today
        return today - timedelta(days=config["lookback_days"]), today

    async def _sync_social_graph(self, config: dict, client: MetaClient) -> dict:
        """Владелец токена, его бизнес-менеджеры и фан-пейджи.

        Эти три ручки требуют `business_management` и `pages_show_list`. Токен
        может их не иметь — тогда верхние уровни обзора останутся пустыми, но
        кабинеты и статистика продолжат синхронизироваться как раньше. Поэтому
        каждый шаг здесь не роняет весь прогон.
        """
        me: dict = {}
        try:
            me = await client.check()
        except Exception:
            me = {}
        external_id = str(me.get("id") or "").strip()
        if not external_id:
            return {"social_id": None, "business_ids": {}, "business_uuids": {}}

        businesses: list[dict] = []
        try:
            businesses = await client.businesses()
        except Exception:
            businesses = []

        # Фан-пейджи собираются в один словарь: у страницы, отданной и БМом, и
        # аккаунтом, побеждает БМ — по нему её и ищут в обзоре.
        pages: dict[str, dict] = {}
        try:
            for row in await client.pages():
                page_id = str(row.get("id") or "").strip()
                if page_id:
                    pages[page_id] = {"row": row, "business": None}
        except Exception:
            pass

        async with self.session_factory() as db:
            social = await db.scalar(
                select(MetaSocialAccount).where(
                    MetaSocialAccount.connection_id == config["id"],
                    MetaSocialAccount.external_id == external_id,
                )
            )
            if not social:
                social = MetaSocialAccount(
                    workspace_id=config["workspace_id"],
                    connection_id=config["id"],
                    external_id=external_id,
                )
                db.add(social)
            social.name = str(me.get("name") or external_id)[:240]
            social.external_payload = me
            await db.flush()
            social_uuid = social.id

            stored_businesses = {
                row.external_id: row
                for row in (
                    await db.execute(
                        select(MetaBusiness).where(MetaBusiness.connection_id == config["id"])
                    )
                ).scalars()
            }
            business_uuids: dict[str, uuid.UUID] = {}
            for row in businesses:
                business_external = str(row.get("id") or "").strip()
                if not business_external:
                    continue
                business = stored_businesses.get(business_external)
                if not business:
                    business = MetaBusiness(
                        workspace_id=config["workspace_id"],
                        connection_id=config["id"],
                        external_id=business_external,
                    )
                    db.add(business)
                business.social_account_id = social_uuid
                business.name = str(row.get("name") or business_external)[:240]
                business.verification_status = (
                    str(row.get("verification_status") or "") or None
                )
                business.external_payload = row
                await db.flush()
                business_uuids[business_external] = business.id
            await db.commit()

        for business_external in list(business_uuids):
            try:
                owned = await client.pages(business_external)
            except Exception:
                continue
            for row in owned:
                page_id = str(row.get("id") or "").strip()
                if page_id:
                    pages[page_id] = {"row": row, "business": business_external}

        async with self.session_factory() as db:
            stored_pages = {
                row.external_id: row
                for row in (
                    await db.execute(
                        select(MetaFanPage).where(MetaFanPage.connection_id == config["id"])
                    )
                ).scalars()
            }
            for page_id, entry in pages.items():
                page = stored_pages.get(page_id)
                if not page:
                    page = MetaFanPage(
                        workspace_id=config["workspace_id"],
                        connection_id=config["id"],
                        external_id=page_id,
                    )
                    db.add(page)
                row = entry["row"]
                page.social_account_id = social_uuid
                page.business_id = business_uuids.get(entry["business"] or "")
                page.name = str(row.get("name") or page_id)[:240]
                page.category = (str(row.get("category") or "") or None)
                page.external_payload = row
            await db.commit()

        return {
            "social_id": social_uuid,
            "business_ids": {key: key for key in business_uuids},
            "business_uuids": business_uuids,
        }

    async def _sync_accounts(
        self, config: dict, client: MetaClient, social: dict | None = None
    ) -> list[dict]:
        social = social or {"social_id": None, "business_uuids": {}}
        # Кабинеты берутся по каждому БМу отдельно — иначе непонятно, чей кабинет.
        # `/me/adaccounts` остаётся источником для личных, без БМа.
        owner_by_account: dict[str, uuid.UUID] = {}
        rows: list[dict] = []
        seen: set[str] = set()
        for business_external, business_uuid in (social.get("business_uuids") or {}).items():
            try:
                owned = await client.ad_accounts(business_external)
            except Exception:
                continue
            for row in owned:
                external_id = str(row.get("id") or "").strip()
                if external_id and external_id not in seen:
                    seen.add(external_id)
                    rows.append(row)
                    owner_by_account[external_id] = business_uuid
        personal = await client.ad_accounts(
            config["business_id"] if not social.get("business_uuids") else None
        )
        for row in personal:
            external_id = str(row.get("id") or "").strip()
            if external_id and external_id not in seen:
                seen.add(external_id)
                rows.append(row)
        stored: list[dict] = []
        async with self.session_factory() as db:
            existing = {
                account.external_id: account
                for account in (
                    await db.execute(
                        select(MetaAdAccount).where(
                            MetaAdAccount.connection_id == config["id"]
                        )
                    )
                ).scalars()
            }
            for row in rows:
                external_id = str(row.get("id") or "").strip()
                if not external_id:
                    continue
                account = existing.get(external_id)
                if not account:
                    account = MetaAdAccount(
                        workspace_id=config["workspace_id"],
                        connection_id=config["id"],
                        external_id=external_id,
                        owner_id=config["owner_id"],
                    )
                    db.add(account)
                elif account.owner_id is None and config["owner_id"] is not None:
                    # Старые и впервые найденные кабинеты наследуют владельца
                    # подключения. Ручное назначение другому человеку не трогаем.
                    account.owner_id = config["owner_id"]
                account.business_id = owner_by_account.get(external_id)
                account.social_account_id = social.get("social_id")
                account.name = str(row.get("name") or external_id)[:240]
                account.account_status = account_status_label(row.get("account_status"))
                account.currency = str(row.get("currency") or "USD")[:8]
                account.timezone_name = (str(row.get("timezone_name") or "") or None)
                account.spend_cap = money_from_minor(row.get("spend_cap"))
                account.amount_spent = money_from_minor(row.get("amount_spent")) or 0
                account.balance = money_from_minor(row.get("balance"))
                account.external_payload = row
                stored.append(account)
            await db.commit()
            # Карточка выключенного кабинета обновляется (баланс, статус в Meta),
            # но кампании и статистику по нему не тянем: администратор снял его
            # на шаге импорта именно чтобы он не занимал лимит запросов.
            return [
                {
                    "id": account.id,
                    "external_id": account.external_id,
                    "name": account.name,
                    "currency": account.currency,
                }
                for account in stored
                if account.status == Status.active
            ]

    async def _sync_entities(
        self,
        config: dict,
        client: MetaClient,
        account: dict,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for level in LEVELS:
            rows = await client.entities(account["external_id"], level)
            counts[level] = len(rows)
            async with self.session_factory() as db:
                existing = {
                    entity.external_id: entity
                    for entity in (
                        await db.execute(
                            select(MetaEntity).where(
                                MetaEntity.account_id == account["id"],
                                MetaEntity.level == level,
                            )
                        )
                    ).scalars()
                }
                for row in rows:
                    external_id = str(row.get("id") or "").strip()
                    if not external_id:
                        continue
                    entity = existing.get(external_id)
                    if not entity:
                        entity = MetaEntity(
                            workspace_id=config["workspace_id"],
                            connection_id=config["id"],
                            account_id=account["id"],
                            level=level,
                            external_id=external_id,
                        )
                        db.add(entity)
                    apply_entity_row(entity, level, row)
                await db.commit()
            if level == "campaign":
                await self._sync_launch_statuses(config, rows)
        return counts

    async def _sync_launch_statuses(self, config: dict, rows: list[dict]) -> None:
        """Подтянуть в заливы фактический статус кампании из кабинета.

        Кампанию могли остановить руками в Ads Manager или её могла отклонить
        модерация. Пока CRM показывает «активен», человек не поймёт, почему
        встал трафик.
        """
        statuses = {
            str(row.get("id") or ""): str(row.get("effective_status") or row.get("status") or "")
            for row in rows
            if row.get("id")
        }
        if not statuses:
            return
        async with self.session_factory() as db:
            launches = list(
                (
                    await db.execute(
                        select(MetaLaunch).where(
                            MetaLaunch.workspace_id == config["workspace_id"],
                            MetaLaunch.campaign_external_id.in_(list(statuses)),
                        )
                    )
                ).scalars()
            )
            for launch in launches:
                if launch.status in (LaunchStatus.draft, LaunchStatus.publishing):
                    continue
                remote = statuses.get(launch.campaign_external_id or "", "")
                launch.status = LAUNCH_STATUS_BY_META.get(remote, launch.status)
            await db.commit()

    async def _sync_insights(
        self,
        config: dict,
        client: MetaClient,
        account: dict,
        start: date,
        end: date,
    ) -> int:
        rows = await client.insights(account["external_id"], start, end)
        if not rows:
            return 0
        async with self.session_factory() as db:
            keys = [stat_dimension_key(account["external_id"], row) for row in rows]
            existing = {
                stat.dimension_key: stat
                for stat in (
                    await db.execute(
                        select(MetaStatDaily).where(
                            MetaStatDaily.connection_id == config["id"],
                            MetaStatDaily.dimension_key.in_(keys),
                        )
                    )
                ).scalars()
            }
            for row, key in zip(rows, keys, strict=True):
                record_date = _parse_day(row.get("date_start"))
                if not record_date:
                    continue
                stat = existing.get(key)
                if not stat:
                    stat = MetaStatDaily(
                        workspace_id=config["workspace_id"],
                        connection_id=config["id"],
                        account_id=account["id"],
                        dimension_key=key,
                    )
                    db.add(stat)
                    existing[key] = stat
                leads, purchases, totals = action_counts(row.get("actions"))
                stat.record_date = record_date
                stat.campaign_external_id = str(row.get("campaign_id") or "") or None
                stat.adset_external_id = str(row.get("adset_id") or "") or None
                stat.ad_external_id = str(row.get("ad_id") or "") or None
                stat.country_code = str(row.get("country") or "")[:12] or None
                stat.impressions = integer_value(row.get("impressions"))
                stat.clicks = integer_value(row.get("clicks"))
                stat.link_clicks = integer_value(row.get("inline_link_clicks"))
                stat.reach = integer_value(row.get("reach"))
                stat.spend = decimal_value(row.get("spend"))
                stat.currency = account["currency"]
                stat.pixel_leads = leads
                stat.pixel_purchases = purchases
                stat.actions = totals
            await db.commit()
        return len(rows)


async def active_meta_connections(db: AsyncSession, workspace_id: uuid.UUID) -> list[
    IntegrationConnection
]:
    return list(
        (
            await db.execute(
                select(IntegrationConnection)
                .where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.kind == "meta",
                    IntegrationConnection.status == Status.active,
                )
                .order_by(IntegrationConnection.name)
            )
        ).scalars()
    )


def _parse_day(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _safe_sync_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {message}"[:500] if message else type(exc).__name__
