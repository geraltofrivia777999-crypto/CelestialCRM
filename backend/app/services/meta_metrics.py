"""Общая арифметика Meta Ads — ТЗ 3.7.

Один и тот же ROI считают отчёт на странице и движок автоправил. Если бы формула
жила в двух местах, рано или поздно правило останавливало бы кампанию, которая на
экране выглядит прибыльной. Поэтому она здесь одна.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KeitaroStatDaily, MetaStatDaily

ZERO = Decimal("0")
# Метрики, по которым можно построить автоправило. Доходные (roi, profit, cpl по
# лидам Keitaro) работают только при настроенной атрибуции — движок это проверяет.
METRIC_LABELS = {
    "spend": "Расход",
    "roi": "ROI, %",
    "profit": "Профит",
    "revenue": "Доход",
    "cpl": "Цена лида",
    "cpc": "Цена клика",
    "ctr": "CTR, %",
    "leads": "Лиды",
    "clicks": "Клики",
    "impressions": "Показы",
    "link_clicks": "Клики по ссылке",
    "link_ctr": "CTR по ссылке, %",
    "cpm": "CPM",
    "results": "Результаты",
    "cpa": "Цена результата",
    "result_cr": "Клик-конверсия, %",
    "sales": "Продажи (Keitaro)",
    "reach": "Охват",
    "pixel_leads": "Лиды (пиксель)",
    "pixel_purchases": "Покупки (пиксель)",
    "actions_total": "Действия",
    "entity_name": "Название объекта",
    "campaign_name": "Название кампании",
    "objective": "Цель",
    "buying_type": "Закупочный тип",
    "spend_cap": "Предел затрат",
    "bid_amount": "Сумма ставки",
    "daily_budget": "Дневной бюджет",
    "lifetime_budget": "Бюджет на весь срок",
}
REVENUE_METRICS = {"roi", "profit", "revenue", "cpl", "leads"}


def q2(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def metrics(
    spend: Decimal,
    impressions: int,
    clicks: int,
    revenue: Decimal | None,
    leads: int,
    sales: int,
    *,
    link_clicks: int = 0,
    results: int = 0,
    reach: int = 0,
    pixel_leads: int = 0,
    pixel_purchases: int = 0,
    actions: dict | None = None,
    entity_name: str = "",
    campaign_name: str = "",
    objective: str = "",
    buying_type: str = "",
    spend_cap: Decimal | None = None,
    bid_amount: Decimal | None = None,
    daily_budget: Decimal | None = None,
    lifetime_budget: Decimal | None = None,
) -> dict:
    """Общий набор чисел строки отчёта.

    `link_clicks` и `results` — то, чем меряет закупку сам кабинет: клики
    именно по ссылке (без лайков и разворотов текста) и конверсии пикселя.
    Они приходят из Meta и живут отдельно от лидов Keitaro, которые считаются
    по постбекам партнёрки: сходиться эти два числа не обязаны.
    """
    action_values = [value for value in (actions or {}).values() if isinstance(value, int | float)]
    payload = {
        "spend": float(q2(spend)),
        "impressions": impressions,
        "clicks": clicks,
        "link_clicks": link_clicks,
        "ctr": float(q2(Decimal(clicks) / impressions * 100)) if impressions else None,
        # CR считаем от кликов по ссылке: это доля показов, доведённая до
        # перехода, и именно её показывает столбец «Клики по ссылке, CR».
        "link_ctr": (
            float(q2(Decimal(link_clicks) / impressions * 100)) if impressions else None
        ),
        "cpc": float(q2(spend / clicks)) if clicks else None,
        "cpm": float(q2(spend / impressions * 1000)) if impressions else None,
        "results": results,
        "cpa": float(q2(spend / results)) if results else None,
        "result_cr": (
            float(q2(Decimal(results) / link_clicks * 100)) if link_clicks else None
        ),
        "leads": leads,
        "sales": sales,
        "cpl": float(q2(spend / leads)) if leads else None,
        "revenue": None,
        "profit": None,
        "roi": None,
        "reach": reach,
        "pixel_leads": pixel_leads,
        "pixel_purchases": pixel_purchases,
        "actions_total": sum(action_values),
        "entity_name": entity_name,
        "campaign_name": campaign_name,
        "objective": objective,
        "buying_type": buying_type,
        "spend_cap": float(q2(spend_cap)) if spend_cap is not None else None,
        "bid_amount": float(q2(bid_amount)) if bid_amount is not None else None,
        "daily_budget": float(q2(daily_budget)) if daily_budget is not None else None,
        "lifetime_budget": (
            float(q2(lifetime_budget)) if lifetime_budget is not None else None
        ),
    }
    if revenue is not None:
        profit = revenue - spend
        payload["revenue"] = float(q2(revenue))
        payload["profit"] = float(q2(profit))
        payload["roi"] = float(q2(profit / spend * 100)) if spend else None
    return payload


def totals(stats: list[MetaStatDaily], keitaro: dict[str, dict]) -> dict:
    spend = sum((row.spend or ZERO for row in stats), ZERO)
    impressions = sum(row.impressions or 0 for row in stats)
    clicks = sum(row.clicks or 0 for row in stats)
    link_clicks = sum(row.link_clicks or 0 for row in stats)
    results = sum((row.pixel_leads or 0) + (row.pixel_purchases or 0) for row in stats)
    # В доход попадают только кампании, которые реально есть в этом наборе:
    # иначе итог включал бы трафик, к Meta отношения не имеющий.
    campaign_ids = {row.campaign_external_id for row in stats if row.campaign_external_id}
    matched = [keitaro[key] for key in campaign_ids if key in keitaro]
    revenue = sum((item["revenue"] for item in matched), ZERO) if matched else None
    leads = sum(item["leads"] for item in matched)
    sales = sum(item["sales"] for item in matched)
    return metrics(
        spend,
        impressions,
        clicks,
        revenue,
        leads,
        sales,
        link_clicks=link_clicks,
        results=results,
    )


async def keitaro_by_campaign(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    start: date,
    end: date,
    sub_id: int | None,
) -> dict[str, dict]:
    """Лиды, продажи и доход Keitaro, разложенные по ID кампании Meta.

    Ключ берётся из sub_id ссылки — того самого, куда Meta подставляет
    {{campaign.id}}. Пока он не настроен, дохода у кампаний нет и мы этого
    не скрываем.
    """
    if not sub_id:
        return {}
    key = f"sub{sub_id}"
    rows = list(
        (
            await db.execute(
                select(KeitaroStatDaily).where(
                    KeitaroStatDaily.workspace_id == workspace_id,
                    KeitaroStatDaily.record_date >= start,
                    KeitaroStatDaily.record_date <= end,
                )
            )
        ).scalars()
    )
    grouped: dict[str, dict] = {}
    for row in rows:
        campaign_id = str((row.sub_values or {}).get(key) or "").strip()
        if not campaign_id:
            continue
        bucket = grouped.setdefault(
            campaign_id, {"leads": 0, "sales": 0, "revenue": ZERO, "clicks": 0}
        )
        bucket["leads"] += row.leads or 0
        bucket["sales"] += row.sales or 0
        bucket["clicks"] += row.clicks or 0
        bucket["revenue"] += row.revenue or ZERO
    return grouped
