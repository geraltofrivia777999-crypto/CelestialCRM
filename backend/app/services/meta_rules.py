"""Автоправила Meta — ТЗ 3.8.

Правила считаются по нашей статистике, а не средствами Meta: ROI и цену лида
Meta не знает, доход приходит из Keitaro. Поэтому и уведомление, и реальное
действие принимает решение на одних и тех же числах, что показаны на странице.

Три вещи, без которых движок был бы опасен:

* `min_spend` — порог, ниже которого правило молчит. Кампания с двумя кликами
  всегда выглядит убыточной, и без порога её выключит первое же правило по ROI.
* Пауза между срабатываниями. Правило «поднять бюджет на 20%» без неё удвоит
  бюджет за час.
* Доходные метрики требуют настроенной атрибуции. Без sub_id доход неизвестен,
  а не равен нулю, и трактовать одно как другое — значит выключать рабочие связки.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import decrypt_secret
from app.models import (
    IntegrationConnection,
    MetaAdAccount,
    MetaEntity,
    MetaLaunch,
    MetaOperation,
    MetaRule,
    MetaRuleEvent,
    MetaStatDaily,
)
from app.services.meta import MetaClient, money_to_minor
from app.services.meta_metrics import (
    REVENUE_METRICS,
    ZERO,
    keitaro_by_campaign,
    metrics,
)
from app.services.meta_session import get_session_manager, open_session_access

ClientFactory = Callable[..., MetaClient]
ACTIONS = {
    "notify": "Уведомить",
    "pause": "Остановить",
    "resume": "Запустить",
    "increase_budget": "Поднять бюджет",
    "decrease_budget": "Снизить бюджет",
    "change_budget": "Изменить бюджет",
    "change_bid": "Изменить ставку",
}
LEVELS = {"campaign": "Кампания", "adset": "Адсет", "ad": "Объявление"}
# У объявления бюджета нет — он живёт на адсете или на кампании.
BUDGET_LEVELS = {"campaign", "adset"}
ENTITY_STATUSES = {"active": "Активные", "paused": "На паузе", "any": "Любые"}
WINDOWS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "last_2d": "Последние 2 дня",
    "last_3d": "Последние 3 дня",
    "last_7d": "Последние 7 дней",
    "last_14d": "Последние 14 дней",
    "last_28d": "Последние 28 дней",
    "last_30d": "Последние 30 дней",
    "month": "Текущий месяц",
}
OPERATORS = {
    "lt": "<",
    "lte": "≤",
    "gt": ">",
    "gte": "≥",
    "eq": "=",
    "ne": "!=",
    "in": "∈",
    "nin": "∉",
}
# Курсы к USD, чтобы конвертировать деньги кабинетов в валюту правила.
CURRENCY_RATES = {
    "USD": 1,
    "EUR": 0.92,
    "GBP": 0.79,
    "PLN": 0.23,
    "UAH": 0.024,
    "KZT": 0.0021,
    "TRY": 0.029,
    "BRL": 0.18,
    "INR": 0.012,
    "IDR": 0.000063,
    "ARS": 0.0011,
    "MXN": 0.052,
    "AED": 0.27,
    "SAR": 0.27,
    "NGN": 0.00065,
    "BDT": 0.0084,
    "VND": 0.000039,
    "THB": 0.028,
    "MYR": 0.21,
    "PHP": 0.017,
    "PKR": 0.0036,
    "EGP": 0.02,
    "COP": 0.00024,
    "CLP": 0.0011,
    "PEN": 0.27,
    "NZD": 0.6,
    "AUD": 0.65,
    "CAD": 0.73,
    "CHF": 1.08,
    "SEK": 0.095,
    "NOK": 0.094,
    "DKK": 0.135,
    "CZK": 0.042,
    "HUF": 0.0026,
    "RON": 0.2,
    "BGN": 0.47,
    "HKD": 0.128,
    "SGD": 0.74,
    "KRW": 0.00073,
    "JPY": 0.0067,
    "CNY": 0.14,
}


def _convert_money(amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
    """Пересчёт суммы из валюты кабинета в валюту правила (через USD)."""
    to = CURRENCY_RATES.get(str(to_currency or "USD").upper())
    frm = CURRENCY_RATES.get(str(from_currency or "USD").upper())
    if not to or not frm:
        return amount
    return (amount / Decimal(str(frm)) * Decimal(str(to))).quantize(Decimal("0.01"))


def schedule_allows(rule: MetaRule, now: datetime) -> bool:
    """Расписание правила: когда ему разрешено смотреть на объекты."""
    if rule.schedule_kind == "custom":
        payload = rule.schedule or {}
        days = set(int(value) for value in (payload.get("days") or []))
        if days and now.isoweekday() not in days:
            return False
        current = f"{now.hour:02d}:{now.minute:02d}"
        intervals = payload.get("intervals") or []
        if intervals:
            return any(
                str(item.get("begin") or "") <= current <= str(item.get("end") or "")
                and bool(str(item.get("end") or ""))
                for item in intervals
            )
        return True
    if rule.schedule_kind == "daily_midnight":
        # «Каждую полночь»: окно первого прогона суток — 00:00–00:09.
        return now.hour == 0 and now.minute < 10
    return True
FREQUENCIES = {
    15: "Каждые 15 минут",
    60: "Каждый час",
    180: "Каждые 3 часа",
    720: "Каждые 12 часов",
    1440: "Раз в сутки",
}
WRITE_ACTIONS = {
    "pause", "resume", "increase_budget", "decrease_budget",
    "change_budget", "change_bid",
}
BUDGET_ACTIONS = {"increase_budget", "decrease_budget", "change_budget"}
BID_ACTIONS = {"change_bid"}
# Ниже этого Meta дневной бюджет не принимает (для дешёвых валют порог свой, но
# меньше единицы он не бывает нигде). Автоправило не должно уводить бюджет туда,
# откуда кампания уже не поднимется.
MIN_DAILY_BUDGET = Decimal("1.00")
MIN_BID_AMOUNT = Decimal("0.01")


class MetaRuleEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client_factory: ClientFactory = MetaClient,
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory

    async def run(self) -> dict:
        async with self.session_factory() as db:
            rules = list(
                (
                    await db.execute(
                        select(MetaRule)
                        .where(MetaRule.is_enabled.is_(True))
                        .order_by(MetaRule.created_at)
                    )
                ).scalars()
            )
        triggered = 0
        applied = 0
        for rule in rules:
            result = await self.evaluate(rule.id)
            triggered += result["triggered"]
            applied += result["applied"]
        return {"rules": len(rules), "triggered": triggered, "applied": applied}

    async def evaluate(self, rule_id: uuid.UUID) -> dict:
        async with self.session_factory() as db:
            rule = await db.get(MetaRule, rule_id)
            if not rule or not rule.is_enabled:
                return {"triggered": 0, "applied": 0}
            if not schedule_allows(rule, datetime.now(UTC)):
                return {"triggered": 0, "applied": 0}
            candidates = await collect_candidates(db, rule)
            recent = await self._recent_campaigns(db, rule)
            access = await _rule_token(db, rule)

        pending = [
            row
            for row in candidates
            if row["external_id"] not in recent and matches(rule, row["metrics"])
        ]
        if not pending:
            return {"triggered": 0, "applied": 0}

        session_access = None
        if access and rule.action in WRITE_ACTIONS:
            if access.get("auth_method") == "session":
                # Запись через токен сессии Meta принимает только из браузерного
                # контекста живой сессии.
                session_access = await open_session_access(
                    self.session_factory,
                    access["connection_id"],
                    proxy_url=access.get("proxy_url"),
                    user_agent=access.get("user_agent"),
                )
        try:
            client = (
                self.client_factory(
                    session_access["token"] if session_access else access["token"],
                    proxy=access["proxy_url"],
                    user_agent=access["user_agent"],
                    transport=session_access["transport"] if session_access else None,
                )
                if access and rule.action in WRITE_ACTIONS
                else None
            )
            applied = 0
            for row in pending:
                error: str | None = None
                done = False
                if rule.action in WRITE_ACTIONS:
                    if not client:
                        error = (
                            "Нет активного подключения Meta с токеном — действие не выполнено, "
                            "правило сработало как уведомление."
                        )
                    else:
                        try:
                            await self._apply(rule, row, client)
                            done = True
                        except Exception as exc:  # noqa: BLE001 - текст ошибки уходит в событие
                            error = " ".join(str(exc).split())[:500]
                await self._record(rule, row, applied=done, error=error)
                applied += int(done)
        finally:
            if session_access and session_access["owned"]:
                # Браузер, восстановленный ради правила, закрываем.
                try:
                    await get_session_manager().close(access["connection_id"])
                except Exception:  # noqa: BLE001 — очистка не маскирует результат
                    pass

        async with self.session_factory() as db:
            stored = await db.get(MetaRule, rule_id)
            if stored:
                stored.last_triggered_at = datetime.now(UTC)
                await db.commit()
        return {"triggered": len(pending), "applied": applied}

    async def _apply(self, rule: MetaRule, row: dict, client: MetaClient) -> None:
        target = row["external_id"]
        level = rule.level if rule.level in LEVELS else "campaign"
        if rule.action == "pause":
            await self._call(rule, row, f"{level}_pause", target,
                             lambda: client.set_status(target, "PAUSED"))
            return
        if rule.action == "resume":
            await self._call(rule, row, f"{level}_resume", target,
                             lambda: client.set_status(target, "ACTIVE"))
            return

        if rule.action == "change_budget":
            await self._apply_budget_change(rule, row, client, target, level)
            return
        if rule.action == "change_bid":
            await self._apply_bid_change(rule, row, client, target)
            return

        if level not in BUDGET_LEVELS:
            raise RuntimeError(
                "У объявления нет собственного бюджета — он живёт на адсете или "
                "на кампании. Поставьте правилу уровень выше."
            )
        adsets = row["adsets"]
        if not adsets:
            raise RuntimeError(
                "Нет групп объявлений с дневным бюджетом — менять нечего. "
                "Возможно, бюджет задан на уровне кампании."
            )
        percent = rule.action_value or ZERO
        factor = (100 + percent) / 100 if rule.action == "increase_budget" else (
            100 - percent
        ) / 100
        for adset_id, budget in adsets:
            target_budget = (budget * factor).quantize(MIN_DAILY_BUDGET)
            if target_budget < MIN_DAILY_BUDGET:
                target_budget = MIN_DAILY_BUDGET
            await self._call(
                rule,
                row,
                f"adset_{rule.action}",
                adset_id,
                lambda adset_id=adset_id, target_budget=target_budget: client.set_daily_budget(
                    adset_id, target_budget
                ),
            )

    async def _apply_budget_change(
        self, rule: MetaRule, row: dict, client: MetaClient, target: str, level: str
    ) -> None:
        """Изменить бюджет кампании или адсета: +/-, % или сумма, с максимумом."""
        base = row["metrics"].get(
            "daily_budget" if rule.budget_kind == "daily" else "lifetime_budget"
        )
        if not base:
            raise RuntimeError(
                "У объекта не задан бюджет нужного вида — менять нечего."
            )
        value = Decimal(str(rule.action_value or 0))
        if rule.action_mode == "pct":
            factor = Decimal("1") + value / Decimal("100")
            if rule.action_sign == "minus":
                factor = Decimal("1") - value / Decimal("100")
            new_base = Decimal(str(base)) * factor
        else:
            new_base = Decimal(str(base)) + (
                value if rule.action_sign == "plus" else -value
            )
        if rule.action_max is not None:
            new_base = min(new_base, Decimal(str(rule.action_max)))
        new_base = max(new_base, MIN_DAILY_BUDGET)
        new_base = new_base.quantize(MIN_DAILY_BUDGET)
        kind = "adset" if level == "adset" else "campaign"
        if rule.budget_kind == "lifetime":
            await self._call(
                rule, row, f"{kind}_lifetime_budget", target,
                lambda target=target, new_base=new_base: client.update_object(
                    target, {"lifetime_budget": money_to_minor(new_base)}
                ),
            )
        else:
            await self._call(
                rule, row, f"{kind}_daily_budget", target,
                lambda target=target, new_base=new_base: client.set_daily_budget(
                    target, new_base
                ),
            )

    async def _apply_bid_change(
        self, rule: MetaRule, row: dict, client: MetaClient, target: str
    ) -> None:
        """Изменить ставку адсета: +/-, % или сумма, с максимумом."""
        fields = await client.object_fields(target, ["bid_amount"])
        current = fields.get("bid_amount")
        if current is None:
            raise RuntimeError("Meta не вернула текущую ставку адсета.")
        value = Decimal(str(rule.action_value or 0))
        base = Decimal(str(current))
        if rule.action_mode == "pct":
            factor = Decimal("1") + value / Decimal("100")
            if rule.action_sign == "minus":
                factor = Decimal("1") - value / Decimal("100")
            new_base = base * factor
        else:
            new_base = base + (value if rule.action_sign == "plus" else -value)
        if rule.action_max is not None:
            new_base = min(new_base, Decimal(str(rule.action_max)))
        new_base = max(new_base, MIN_BID_AMOUNT)
        new_base = new_base.quantize(MIN_DAILY_BUDGET)
        await self._call(
            rule, row, "adset_bid", target,
            lambda target=target, new_base=new_base: client.update_object(
                target, {"bid_amount": money_to_minor(new_base)}
            ),
        )

    async def _call(
        self,
        rule: MetaRule,
        row: dict,
        kind: str,
        target: str,
        action: Callable,
    ) -> None:
        async with self.session_factory() as db:
            operation = MetaOperation(
                workspace_id=rule.workspace_id,
                launch_id=row.get("launch_id"),
                rule_id=rule.id,
                kind=kind,
                target_external_id=target,
                status="pending",
                request={"rule": rule.name, "conditions": rule_conditions(rule)},
            )
            db.add(operation)
            await db.commit()
            operation_id = operation.id
        try:
            response = await action()
        except Exception as exc:
            await self._close(operation_id, "failed", {}, " ".join(str(exc).split())[:500])
            raise
        await self._close(
            operation_id, "success", response if isinstance(response, dict) else {}, None
        )

    async def _close(
        self, operation_id: uuid.UUID, status: str, response: dict, error: str | None
    ) -> None:
        async with self.session_factory() as db:
            operation = await db.get(MetaOperation, operation_id)
            if not operation:
                return
            operation.status = status
            operation.response = response
            operation.error = error
            await db.commit()

    async def _recent_campaigns(self, db: AsyncSession, rule: MetaRule) -> set[str]:
        """Объекты, по которым правило уже срабатывало недавно."""
        if rule.cooldown_minutes <= 0:
            return set()
        since = datetime.now(UTC) - timedelta(minutes=rule.cooldown_minutes)
        rows = await db.execute(
            select(MetaRuleEvent.campaign_external_id).where(
                MetaRuleEvent.rule_id == rule.id,
                MetaRuleEvent.created_at >= since,
            )
        )
        return {value for (value,) in rows if value}

    async def _record(
        self, rule: MetaRule, row: dict, *, applied: bool, error: str | None
    ) -> None:
        async with self.session_factory() as db:
            db.add(
                MetaRuleEvent(
                    workspace_id=rule.workspace_id,
                    rule_id=rule.id,
                    account_id=row["account_id"],
                    # Колонки события названы по кампании исторически; с
                    # появлением уровней в них лежит объект, к которому правило
                    # применилось, — адсет или объявление в том числе.
                    campaign_external_id=row["external_id"],
                    campaign_name=row["entity_name"],
                    metric=_first_metric(rule),
                    metric_value=_as_decimal(row["metrics"].get(_first_metric(rule))),
                    action=rule.action,
                    applied=applied,
                    message=describe(rule, row),
                    error=error,
                )
            )
            await db.commit()


async def collect_candidates(db: AsyncSession, rule: MetaRule) -> list[dict]:
    """Объекты уровня правила с посчитанными метриками за период статы."""
    today = datetime.now(UTC).date()
    start, end = window_range(rule.window, today)
    level = rule.level if rule.level in LEVELS else "campaign"

    account_filters = [MetaAdAccount.workspace_id == rule.workspace_id]
    if rule.account_id:
        account_filters.append(MetaAdAccount.id == rule.account_id)
    accounts = {
        account.id: account
        for account in (
            await db.execute(select(MetaAdAccount).where(*account_filters))
        ).scalars()
    }
    if not accounts:
        return []

    only_campaigns: set[str] = set()
    launch_by_campaign: dict[str, uuid.UUID] = {}
    launches = list(
        (
            await db.execute(
                select(MetaLaunch).where(
                    MetaLaunch.workspace_id == rule.workspace_id,
                    MetaLaunch.campaign_external_id.is_not(None),
                )
            )
        ).scalars()
    )
    for launch in launches:
        launch_by_campaign[launch.campaign_external_id] = launch.id
    if rule.launch_id:
        launch = await db.get(MetaLaunch, rule.launch_id)
        if not launch or not launch.campaign_external_id:
            return []
        only_campaigns = {launch.campaign_external_id}
        # Несколько кампаний залива («Расширенный режим»): правило смотрит
        # на все кампании, созданные его заливом.
        only_campaigns.update(
            str(value)
            for value in ((launch.external_payload or {}).get("campaign_ids") or [])
            if value
        )

    stats = list(
        (
            await db.execute(
                select(MetaStatDaily).where(
                    MetaStatDaily.account_id.in_(list(accounts)),
                    MetaStatDaily.record_date >= start,
                    MetaStatDaily.record_date <= end,
                )
            )
        ).scalars()
    )
    key_column = {
        "campaign": "campaign_external_id",
        "adset": "adset_external_id",
        "ad": "ad_external_id",
    }[level]
    grouped: dict[str, list[MetaStatDaily]] = {}
    for stat in stats:
        if only_campaigns and stat.campaign_external_id not in only_campaigns:
            continue
        key = getattr(stat, key_column)
        if key:
            grouped.setdefault(key, []).append(stat)
    if not grouped:
        return []

    sub_id = await _attribution_sub_id(db, rule.workspace_id)
    # Доход из трекера привязан к ID кампании, поэтому ниже кампании его нет:
    # доходные метрики на адсетах и объявлениях остаются неизвестными, а
    # `matches_condition` на неизвестном значении молчит.
    keitaro = (
        await keitaro_by_campaign(db, rule.workspace_id, start, end, sub_id)
        if level == "campaign" and rule_metrics(rule) & REVENUE_METRICS
        else {}
    )
    entities = {
        entity.external_id: entity
        for entity in (
            await db.execute(
                select(MetaEntity).where(
                    MetaEntity.account_id.in_(list(accounts)),
                    MetaEntity.level == level,
                )
            )
        ).scalars()
    }
    # Бюджет меняется на адсетах: у кампании он либо общий (CBO), либо разложен
    # по группам. Для правила уровня «адсет» цель — он сам.
    adsets: dict[str, list[tuple[str, object]]] = {}
    if rule.action in BUDGET_ACTIONS and level == "campaign":
        for entity in (
            await db.execute(
                select(MetaEntity).where(
                    MetaEntity.account_id.in_(list(accounts)),
                    MetaEntity.level == "adset",
                )
            )
        ).scalars():
            if entity.parent_external_id and entity.daily_budget:
                adsets.setdefault(entity.parent_external_id, []).append(
                    (entity.external_id, entity.daily_budget)
                )

    rows: list[dict] = []
    for external_id, entity_stats in grouped.items():
        entity = entities.get(external_id)
        if not _status_allowed(rule.entity_status, entity):
            continue
        # Scope «только кампания»: для adsets/ads пропускаем чужие кампании.
        if rule.scope_kind == "campaign" and rule.campaign_external_id:
            if str(entity_stats[0].campaign_external_id) != str(
                rule.campaign_external_id
            ):
                continue
        spend = sum((stat.spend or ZERO for stat in entity_stats), ZERO)
        if rule.convert_currency and rule.currency:
            spend = _convert_money(spend, entity_stats[0].currency or "USD", rule.currency)
        if spend < (rule.min_spend or ZERO):
            continue
        campaign_id = entity_stats[0].campaign_external_id
        tracker = keitaro.get(campaign_id) if campaign_id else None
        budget_targets = adsets.get(external_id, [])
        if rule.action in BUDGET_ACTIONS and level == "adset" and entity and entity.daily_budget:
            budget_targets = [(external_id, entity.daily_budget)]
        rows.append(
            {
                "external_id": external_id,
                "entity_name": entity.name if entity else f"{LEVELS[level]} {external_id}",
                "campaign_external_id": campaign_id,
                "account_id": entity_stats[0].account_id,
                "account_name": accounts[entity_stats[0].account_id].name,
                "launch_id": launch_by_campaign.get(campaign_id) if campaign_id else None,
                "adsets": budget_targets,
                "metrics": metrics(
                    spend,
                    sum(stat.impressions or 0 for stat in entity_stats),
                    sum(stat.clicks or 0 for stat in entity_stats),
                    tracker["revenue"] if tracker else None,
                    tracker["leads"] if tracker else 0,
                    tracker["sales"] if tracker else 0,
                    link_clicks=sum(stat.link_clicks or 0 for stat in entity_stats),
                    results=sum(
                        (stat.pixel_leads or 0) + (stat.pixel_purchases or 0)
                        for stat in entity_stats
                    ),
                    reach=sum(stat.reach or 0 for stat in entity_stats),
                    pixel_leads=sum(stat.pixel_leads or 0 for stat in entity_stats),
                    pixel_purchases=sum(
                        stat.pixel_purchases or 0 for stat in entity_stats
                    ),
                    actions=(entity_stats[0].actions or {}) if entity_stats else None,
                    entity_name=(
                        entity.name if entity else f"{LEVELS[level]} {external_id}"
                    ),
                    campaign_name=(
                        entities.get(campaign_id).name if campaign_id and entities.get(campaign_id) else ""
                    ),
                    objective=(
                        entities.get(campaign_id).objective or ""
                        if campaign_id and entities.get(campaign_id)
                        else ""
                    ),
                    buying_type=_buying_type(entities.get(campaign_id)),
                    spend_cap=_entity_money(entities.get(campaign_id), "spend_cap"),
                    bid_amount=(
                        _entity_money(entity, "bid_amount") if level == "adset" else None
                    ),
                    daily_budget=_entity_money(entity, "daily_budget"),
                    lifetime_budget=_entity_money(entity, "lifetime_budget"),
                ),
            }
        )
    return rows


def _buying_type(entity: MetaEntity | None) -> str:
    payload = (entity.external_payload or {}) if entity else {}
    return str(payload.get("buying_type") or "") if isinstance(payload, dict) else ""


def _entity_money(entity: MetaEntity | None, field: str):
    if entity is None:
        return None
    payload = entity.external_payload or {}
    if isinstance(payload, dict) and payload.get(field) is not None:
        return payload[field]
    value = getattr(entity, field, None)
    return value


def _status_allowed(wanted: str, entity: MetaEntity | None) -> bool:
    """Фильтр «какие статусы брать».

    У объекта, которого ещё нет в справочнике (статистика приехала раньше
    синхронизации объектов), статус неизвестен — он проходит только в режиме
    «Любые», иначе правило действовало бы вслепую.
    """
    if wanted == "any":
        return True
    if not entity or not entity.effective_status:
        return False
    status = entity.effective_status.upper()
    if wanted == "active":
        return status == "ACTIVE"
    return status != "ACTIVE"


def window_range(window: str, today: date) -> tuple[date, date]:
    """Период статы пресетом. «Вчера» — именно вчерашний день, а не два дня."""
    if window == "yesterday":
        day = today - timedelta(days=1)
        return day, day
    days = {
        "today": 1,
        "last_2d": 2,
        "last_3d": 3,
        "last_7d": 7,
        "last_14d": 14,
        "last_28d": 28,
        "last_30d": 30,
    }.get(window, 1)
    if window == "month":
        return today.replace(day=1), today
    return today - timedelta(days=days - 1), today


def rule_conditions(rule: MetaRule) -> list[dict]:
    return [item for item in (rule.conditions or []) if isinstance(item, dict)]


def rule_metrics(rule: MetaRule) -> set[str]:
    return {str(item.get("metric") or "") for item in rule_conditions(rule)}


def matches_condition(condition: dict, values: dict) -> bool:
    """Одно условие. None означает «неизвестно», а не ноль.

    Без настроенной атрибуции ROI не посчитан, и правило по ROI обязано
    промолчать, а не остановить кампанию. Строковые значения (название
    кампании, цель и т.п.) сравниваются как строки: =, !=, ∈, ∉.
    """
    value = values.get(str(condition.get("metric") or ""))
    operator = str(condition.get("operator") or "lt")
    raw = condition.get("value")
    if isinstance(value, str):
        threshold = "" if raw is None else str(raw)
        if operator == "eq":
            return value == threshold
        if operator == "ne":
            return value != threshold
        if operator == "in":
            return threshold in value
        if operator == "nin":
            return threshold not in value
        return value == threshold
    if value is None:
        return False
    try:
        threshold = float(raw or 0)
    except (TypeError, ValueError):
        return False
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "ne":
        return value != threshold
    if operator == "in":
        return str(value) == str(threshold) or abs(value - threshold) < 1e-9
    if operator == "nin":
        return str(value) != str(threshold) and abs(value - threshold) >= 1e-9
    return value == threshold


def matches(rule: MetaRule, values: dict) -> bool:
    """Все условия правила разом.

    Правило без условий срабатывает на всём, что попало в область, — это
    осмысленный режим («остановить все активные объявления»), а не ошибка.
    """
    return all(matches_condition(item, values) for item in rule_conditions(rule))


def describe(rule: MetaRule, row: dict) -> str:
    conditions = rule_conditions(rule)
    if conditions:
        parts = []
        for item in conditions:
            metric = str(item.get("metric") or "")
            value = row["metrics"].get(metric)
            formatted = "—" if value is None else f"{value:g}"
            sign = OPERATORS.get(str(item.get("operator") or "lt"), "<")
            parts.append(f"{metric} = {formatted} {sign} {item.get('value')}")
        summary = ", ".join(parts)
    else:
        summary = "без условий"
    level = LEVELS.get(rule.level, "Объект").lower()
    return (
        f"«{rule.name}»: {summary} у {level} «{row['entity_name']}» "
        f"({row['account_name']}), расход {row['metrics']['spend']:g}"
    )


def _first_metric(rule: MetaRule) -> str:
    """Метрика для колонки события. У правила без условий её нет."""
    conditions = rule_conditions(rule)
    return str(conditions[0].get("metric") or "") if conditions else "—"


async def _attribution_sub_id(db: AsyncSession, workspace_id: uuid.UUID) -> int | None:
    return await db.scalar(
        select(IntegrationConnection.attribution_sub_id)
        .where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.kind == "meta",
            IntegrationConnection.attribution_sub_id.is_not(None),
        )
        .limit(1)
    )


async def _rule_token(db: AsyncSession, rule: MetaRule) -> dict | None:
    """Токен кабинета, к которому относится правило.

    У правила без кабинета берётся первое активное подключение: несколько
    Business Manager в одном воркспейсе — редкость, а падать из-за этого молча
    правило не должно.
    """
    connection_id: uuid.UUID | None = None
    if rule.account_id:
        account = await db.get(MetaAdAccount, rule.account_id)
        connection_id = account.connection_id if account else None
    connection = (
        await db.get(IntegrationConnection, connection_id)
        if connection_id
        else await db.scalar(
            select(IntegrationConnection)
            .where(
                IntegrationConnection.workspace_id == rule.workspace_id,
                IntegrationConnection.kind == "meta",
            )
            .order_by(IntegrationConnection.created_at)
            .limit(1)
        )
    )
    if not connection:
        return None
    return {
        "token": decrypt_secret(connection.api_key_encrypted),
        "connection_id": str(connection.id),
        "auth_method": connection.auth_method,
        "proxy_url": connection.proxy_url,
        "user_agent": connection.user_agent,
    }


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
