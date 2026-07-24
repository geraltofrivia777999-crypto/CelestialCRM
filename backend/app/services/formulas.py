import hashlib
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

MONEY = Decimal("0.0001")
PERCENT = Decimal("0.01")


def q(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def media_metrics(
    revenue: Decimal, spend_calculated: Decimal, spend_override: Decimal | None, ftd: int | None
) -> dict[str, Decimal | None]:
    spend = spend_override if spend_override is not None else spend_calculated
    profit = q(revenue - spend)
    roi = None if spend == 0 else (profit / spend * 100).quantize(PERCENT)
    cpd = None if not ftd else q(spend / Decimal(ftd))
    return {"spend": q(spend), "profit": profit, "roi": roi, "cpd": cpd}


def finance_metrics(
    revenue: Decimal, rent: Decimal, spend: Decimal
) -> dict[str, Decimal | None]:
    costs = rent + spend
    profit = q(revenue - costs)
    roi = None if costs == 0 else (profit / costs * 100).quantize(PERCENT)
    return {"costs": q(costs), "profit": profit, "roi": roi}


def rent_cost(quantity: Decimal, install_cost: Decimal) -> Decimal:
    return q(quantity * install_cost)


def amount_with_commission(amount: Decimal, commission_pct: Decimal) -> Decimal:
    return q(amount * (Decimal("1") + commission_pct / Decimal("100")))


def service_cost(
    quantity: Decimal, install_cost: Decimal, commission_pct: Decimal
) -> Decimal:
    """RENT for one service: installs × install cost, plus its commission (ТЗ 2.4.1)."""
    return amount_with_commission(rent_cost(quantity, install_cost), commission_pct)


def finance_import_key(
    record_date: date, buyer_id: UUID, offer_id: UUID, link: str | None
) -> str:
    normalized_link = " ".join((link or "").strip().lower().split())
    raw = f"{record_date.isoformat()}|{buyer_id}|{offer_id}|{normalized_link}"
    return hashlib.sha256(raw.encode()).hexdigest()

