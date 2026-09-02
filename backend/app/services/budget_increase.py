"""Запланированное увеличение бюджета («Расширенный режим»).

Период с окном [start_at, end_at] применяется ровно один раз, когда
планировщик застал `now` внутри окна: текущий бюджет (итоговый после
предыдущих увеличений) поднимается на сумму или на процент, и результат
пишется в `external_payload` — повторно период не срабатывает.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import LaunchStatus, MetaLaunch
from app.services.meta import MetaClient
from app.services.meta_launch import MetaError, _launch_session_access, load_launch_context

logger = logging.getLogger(__name__)


def _moment(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def _current_budget(launch: MetaLaunch) -> Decimal:
    payload = launch.external_payload or {}
    return Decimal(str(payload.get("current_budget") or launch.daily_budget or 0))


async def apply_due_budget_increases(session_factory, client_factory) -> dict:
    """Поднять бюджеты кампаний у заливов с наступившими периодами."""
    reported = {"increased": 0, "skipped": 0, "errors": 0}
    async with session_factory() as db:
        launches = list(
            (
                await db.execute(
                    select(MetaLaunch).where(
                        MetaLaunch.status.in_(
                            [LaunchStatus.active, LaunchStatus.paused]
                        ),
                        MetaLaunch.budget_increases.is_not(None),
                    )
                )
            ).scalars()
        )
    now = datetime.now(UTC)
    for launch in launches:
        periods = list(launch.budget_increases or [])
        if not periods:
            continue
        touched = False
        new_budget = None
        try:
            for period in periods:
                if period.get("applied"):
                    continue
                start = _moment(period.get("start_at"))
                end = _moment(period.get("end_at"))
                if not start or start > now or (end and end < now):
                    continue
                amount = Decimal(str(period.get("amount") or 0))
                kind = str(period.get("kind") or "sum")
                if amount <= 0:
                    period["applied"] = True
                    touched = True
                    continue
                campaign_ids = [launch.campaign_external_id] + list(
                    (launch.external_payload or {}).get("campaign_ids") or []
                )
                campaign_ids = [value for value in campaign_ids if value]
                if not campaign_ids:
                    continue
                async with session_factory() as db:
                    context = await load_launch_context(db, launch.id)
                if not context:
                    continue
                access = await _launch_session_access(context, session_factory)
                try:
                    client: MetaClient = client_factory(
                        access["token"] or context["access_token"],
                        proxy=context.get("proxy_url"),
                        user_agent=context.get("user_agent"),
                        transport=access["transport"],
                    )
                    base = _current_budget(launch)
                    new = base * (Decimal("1") + amount / Decimal("100")) \
                        if kind == "pct" else base + amount
                    new = new.quantize(Decimal("0.01"))
                    for campaign_id in campaign_ids:
                        try:
                            await client.set_daily_budget(campaign_id, new)
                        except MetaError:
                            # Один кабинет с недоступным токеном не отменяет
                            # остальных: период всё равно помечается выполненным.
                            logger.warning(
                                "budget increase: Meta отклонила кампанию %s", campaign_id
                            )
                    period["applied"] = True
                    touched = True
                    new_budget = new
                    reported["increased"] += 1
                finally:
                    if access.get("owned"):
                        from app.services.meta_session import get_session_manager

                        try:
                            await get_session_manager().close(
                                str(context["connection_id"])
                            )
                        except Exception:  # noqa: BLE001
                            pass
                break
            async with session_factory() as db:
                stored = await db.get(MetaLaunch, launch.id)
                if not stored:
                    continue
                if touched:
                    payload = dict(stored.external_payload or {})
                    if new_budget is not None:
                        payload["current_budget"] = str(new_budget)
                    stored.external_payload = payload
                    stored.budget_increases = periods
                    await db.commit()
                else:
                    reported["skipped"] += 1
        except Exception:  # noqa: BLE001 — один залив не должен ронять задачу
            logger.exception("budget increase failed for launch %s", launch.id)
            reported["errors"] += 1
    return reported
