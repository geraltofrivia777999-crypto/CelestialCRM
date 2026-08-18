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


# Зарплата берётся от месячного профита целиком, а не по частям: на границе
# ставка меняется вся сразу. Правило заказчика, менять его здесь нельзя.
SALARY_LADDER = (
    (Decimal("10000"), Decimal("10")),
    (Decimal("15000"), Decimal("15")),
    (Decimal("20000"), Decimal("20")),
    (Decimal("40000"), Decimal("25")),
)
SALARY_TOP_PCT = Decimal("30")


def salary_percent(profit: Decimal) -> Decimal:
    """Ставка зарплаты для месячного профита, в процентах."""
    if profit < 0:
        return Decimal("0")
    for ceiling, percent in SALARY_LADDER:
        if profit <= ceiling:
            return percent
    return SALARY_TOP_PCT


def salary_for_profit(profit: Decimal) -> Decimal:
    if profit < 0:
        return Decimal("0")
    return q(profit * salary_percent(profit) / Decimal("100"))


def grid_percent(tiers: list[dict], amount: Decimal) -> Decimal:
    """Ставка сетки для суммы.

    Ставка берётся от суммы целиком, а не по частям: на границе меняется вся
    ставка сразу. Это правило команды, оно же зашито в лестницу выше.
    """
    for tier in tiers:
        if tier.get("up_to") in (None, ""):
            return Decimal(str(tier["percent"]))
        if amount <= Decimal(str(tier["up_to"])):
            return Decimal(str(tier["percent"]))
    return Decimal("0")


def plan_salary(plan: dict, profit: Decimal) -> tuple[Decimal, Decimal]:
    """Зарплата и эффективная ставка по правилу из «Настройки → Расчет ЗП».

    Правило приходит сюда уже разобранным: части, которые считаются от профита
    книги, — как есть, всё остальное (фикс, вычеты, проценты от профита
    команды) — одной посчитанной суммой. Поэтому цифра пересчитывается прямо
    во время правки книги, не спрашивая сервер о том, что за этот месяц уже не
    изменится.

    Отрицательный профит не начисляет процентов: месяц в минусе переносится
    долгом, а не превращается в доплату.
    """
    total = Decimal("0")
    percent = Decimal("0")
    for part in plan.get("parts") or []:
        if part.get("kind") == "flat":
            total += Decimal(str(part.get("amount") or 0))
            continue
        if profit <= 0:
            continue
        rate = (
            grid_percent(part.get("tiers") or [], profit)
            if part.get("kind") == "grid"
            else Decimal(str(part.get("percent") or 0))
        )
        percent += rate
        total += profit * rate / Decimal("100")
    # Зарплата не бывает отрицательной: вычет больше начисления обнуляет её,
    # а не превращается в счёт сотруднику.
    return max(q(total), Decimal("0")), percent


def finance_day_metrics(
    income: Decimal, spend_buyer: Decimal, costs: Decimal
) -> dict[str, Decimal | None]:
    """Показатели одного дня.

    Costs вычитается и здесь, и в месячном итоге. В исходной таблице команды он
    входил только в месячный, из-за чего сумма дней не сходилась с итогом.
    """
    base = spend_buyer + costs
    profit = q(income - base)
    roi = None if base == 0 else (profit / base * 100).quantize(PERCENT)
    return {"income": q(income), "base": q(base), "profit": profit, "roi": roi}


def finance_import_key(
    record_date: date, buyer_id: UUID, offer_id: UUID, link: str | None
) -> str:
    normalized_link = " ".join((link or "").strip().lower().split())
    raw = f"{record_date.isoformat()}|{buyer_id}|{offer_id}|{normalized_link}"
    return hashlib.sha256(raw.encode()).hexdigest()

