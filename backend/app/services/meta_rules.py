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
from app.services.meta import MetaClient
from app.services.meta_metrics import (
    REVENUE_METRICS,
    ZERO,
    keitaro_by_campaign,
    metrics,
)

ClientFactory = Callable[..., MetaClient]
ACTIONS = {
    "notify": "Уведомить",
    "pause": "Остановить",
    "resume": "Запустить",
    "increase_budget": "Поднять бюджет",
    "decrease_budget": "Снизить бюджет",
}
LEVELS = {"campaign": "Кампания", "adset": "Адсет", "ad": "Объявление"}
# У объявления бюджета нет — он живёт на адсете или на кампании.
BUDGET_LEVELS = {"campaign", "adset"}
ENTITY_STATUSES = {"active": "Активные", "paused": "На паузе", "any": "Любые"}
WINDOWS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "last_3d": "Последние 3 дня",
    "last_7d": "Последние 7 дней",
    "last_30d": "Последние 30 дней",
}
OPERATORS = {
    "lt": "<",
    "lte": "≤",
    "gt": ">",
    "gte": "≥",
    "eq": "=",
}
FREQUENCIES = {
    15: "Каждые 15 минут",
    60: "Каждый час",
    180: "Каждые 3 часа",
    720: "Каждые 12 часов",
    1440: "Раз в сутки",
}
WRITE_ACTIONS = {"pause", "resume", "increase_budget", "decrease_budget"}
BUDGET_ACTIONS = {"increase_budget", "decrease_budget"}
# Ниже этого Meta дневной бюджет не принимает (для дешёвых валют порог свой, но
# меньше единицы он не бывает нигде). Автоправило не должно уводить бюджет туда,
# откуда кампания уже не поднимется.
MIN_DAILY_BUDGET = Decimal("1.00")


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
            candidates = await collect_candidates(db, rule)
            recent = await self._recent_campaigns(db, rule)
            token = await _rule_token(db, rule)

        pending = [
            row
            for row in candidates
            if row["external_id"] not in recent and matches(rule, row["metrics"])
        ]
        if not pending:
            return {"triggered": 0, "applied": 0}

        client = self.client_factory(token) if token and rule.action in WRITE_ACTIONS else None
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
            target = (budget * factor).quantize(MIN_DAILY_BUDGET)
            if target < MIN_DAILY_BUDGET:
                target = MIN_DAILY_BUDGET
            await self._call(
                rule,
                row,
                f"adset_{rule.action}",
                adset_id,
                lambda adset_id=adset_id, target=target: client.set_daily_budget(
                    adset_id, target
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

    only_campaign: str | None = None
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
        only_campaign = launch.campaign_external_id

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
        if only_campaign and stat.campaign_external_id != only_campaign:
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
        spend = sum((stat.spend or ZERO for stat in entity_stats), ZERO)
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
                ),
            }
        )
    return rows


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
    days = {"today": 1, "last_3d": 3, "last_7d": 7, "last_30d": 30}.get(window, 1)
    return today - timedelta(days=days - 1), today


def rule_conditions(rule: MetaRule) -> list[dict]:
    return [item for item in (rule.conditions or []) if isinstance(item, dict)]


def rule_metrics(rule: MetaRule) -> set[str]:
    return {str(item.get("metric") or "") for item in rule_conditions(rule)}


def matches_condition(condition: dict, values: dict) -> bool:
    """Одно условие. None означает «неизвестно», а не ноль.

    Без настроенной атрибуции ROI не посчитан, и правило по ROI обязано
    промолчать, а не остановить кампанию.
    """
    value = values.get(str(condition.get("metric") or ""))
    if value is None:
        return False
    try:
        threshold = float(condition.get("value") or 0)
    except (TypeError, ValueError):
        return False
    operator = str(condition.get("operator") or "lt")
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
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


async def _rule_token(db: AsyncSession, rule: MetaRule) -> str | None:
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
    return decrypt_secret(connection.api_key_encrypted) if connection else None


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
