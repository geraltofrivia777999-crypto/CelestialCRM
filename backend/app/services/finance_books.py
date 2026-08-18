"""Книга баера: загрузка из базы и расчёт показателей месяца.

Вынесено из роутера, потому что читателей у этих цифр стало двое. Зарплата по
правилам («Настройки → Расчет ЗП») считается от профита Финансов, и если бы она
складывала свои суммы отдельным запросом, две формулы профита рано или поздно
разошлись бы — а расходятся такие вещи молча и обнаруживаются после выплаты.
"""

import calendar
import uuid
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FinanceBook,
    FinanceBookDay,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
)
from app.services.formulas import finance_day_metrics, plan_salary, q, salary_for_profit
from app.services.formulas import salary_percent as ladder_percent

ZERO = Decimal("0")
ONE = Decimal("1")


def q6(value: Decimal) -> Decimal:
    """Курс округляем до шести знаков — этого хватает любой котировке."""
    return Decimal(value).quantize(Decimal("0.000001"))


def offer_rate_in_usd(offer: dict, eur_usd_rate: Decimal) -> Decimal:
    """Ставка в долларах: книга считается только в них.

    Евровая ставка переводится по курсу самой книги, поэтому закрытый месяц не
    пересчитывается задним числом при изменении курса.
    """
    rate = offer["rate"] or ZERO
    if (offer.get("rate_currency") or "USD") == "EUR":
        return rate * (eur_usd_rate or ONE)
    return rate


def before_period(year: int, month: int):
    """SQL-условие для книг, которые идут раньше выбранного месяца."""
    return or_(
        FinanceBook.year < year,
        and_(FinanceBook.year == year, FinanceBook.month < month),
    )


async def load_many(db: AsyncSession, books: list[FinanceBook]) -> dict[uuid.UUID, dict]:
    """Загрузить книги пакетно, независимо от количества месяцев.

    Перенос пересчитывает всю историю баера. Четыре общих запроса не дают этому
    превратиться в 4 × N запросов по мере накопления месяцев.
    """
    if not books:
        return {}
    book_ids = [book.id for book in books]
    day_rows = list(
        (
            await db.execute(
                select(FinanceBookDay).where(FinanceBookDay.book_id.in_(book_ids))
            )
        ).scalars()
    )
    offer_rows = list(
        (
            await db.execute(
                select(FinanceBookOffer)
                .where(FinanceBookOffer.book_id.in_(book_ids))
                .order_by(
                    FinanceBookOffer.book_id,
                    FinanceBookOffer.position,
                    FinanceBookOffer.name,
                )
            )
        ).scalars()
    )
    tag_rows = []
    if offer_rows:
        tag_rows = list(
            (
                await db.execute(
                    select(FinanceOfferTag)
                    .where(FinanceOfferTag.offer_id.in_([row.id for row in offer_rows]))
                    .order_by(
                        FinanceOfferTag.offer_id,
                        FinanceOfferTag.position,
                        FinanceOfferTag.name,
                    )
                )
            ).scalars()
        )
    value_rows = []
    if tag_rows:
        value_rows = list(
            (
                await db.execute(
                    select(FinanceTagDay).where(
                        FinanceTagDay.tag_id.in_([row.id for row in tag_rows])
                    )
                )
            ).scalars()
        )
    by_tag: dict[uuid.UUID, dict[int, Decimal]] = {}
    for row in value_rows:
        by_tag.setdefault(row.tag_id, {})[row.day] = row.deposits
    by_offer: dict[uuid.UUID, list[dict]] = {}
    for tag in tag_rows:
        by_offer.setdefault(tag.offer_id, []).append(
            {"name": tag.name, "values": by_tag.get(tag.id, {})}
        )
    by_book_days: dict[uuid.UUID, dict[int, dict]] = {}
    for row in day_rows:
        by_book_days.setdefault(row.book_id, {})[row.day] = {
            "spend_buyer": row.spend_buyer,
            "spend_agent": row.spend_agent,
            "costs": row.costs,
        }
    by_book_offers: dict[uuid.UUID, list[dict]] = {}
    for row in offer_rows:
        by_book_offers.setdefault(row.book_id, []).append(
            {
                "id": str(row.id),
                "name": row.name,
                "partner": row.partner,
                "geo": row.geo,
                "source_offer_id": str(row.source_offer_id) if row.source_offer_id else None,
                "rate": row.rate,
                "rate_currency": row.rate_currency or "USD",
                "tags": by_offer.get(row.id, []),
            }
        )
    return {
        book.id: {
            "prev_minus": book.prev_minus,
            "eur_usd_rate": book.eur_usd_rate or Decimal("1"),
            "days": by_book_days.get(book.id, {}),
            "offers": by_book_offers.get(book.id, []),
        }
        for book in books
    }


async def load(db: AsyncSession, book: FinanceBook | None) -> dict:
    if book is None:
        return {"prev_minus": ZERO, "eur_usd_rate": Decimal("1"), "days": {}, "offers": []}
    return (await load_many(db, [book]))[book.id]


def totals(payload: dict, days_in_month: int, plan: dict | None = None) -> dict:
    """Дневные и месячные показатели одной книги.

    `plan` — правило зарплаты этого баера. Без него зарплата считается прежней
    лестницей: пока правил не завели, ничего не меняется.

    Тира здесь нет: книга целиком принадлежит своему тиру, и разрез по ним —
    это группировка книг, а не деление чисел внутри одной.
    """
    daily = []
    income_total = spend_total = agent_total = costs_total = ZERO
    eur_usd_rate = payload.get("eur_usd_rate") or ONE
    rates = {id(offer): offer_rate_in_usd(offer, eur_usd_rate) for offer in payload["offers"]}
    for day in range(1, days_in_month + 1):
        entry = payload["days"].get(day) or {}
        spend_buyer = entry.get("spend_buyer") or ZERO
        spend_agent = entry.get("spend_agent") or ZERO
        costs = entry.get("costs") or ZERO
        income = sum(
            sum(tag["values"].get(day) or ZERO for tag in offer["tags"]) * rates[id(offer)]
            for offer in payload["offers"]
        ) or ZERO
        metrics = metrics_for(Decimal(income), spend_buyer, costs)
        income_total += metrics["income"]
        spend_total += spend_buyer
        agent_total += spend_agent
        costs_total += costs
        daily.append({"day": day, **metrics})

    month_metrics = metrics_for(income_total, spend_total, costs_total)
    profit_month = month_metrics["profit"]
    # Долг прошлого месяца входит в профит строкой ниже нуля, а не отдельным
    # вычетом из зарплаты: иначе он вычитался бы дважды — сначала из профита,
    # потом из начисленного. Поэтому ступень и зарплата берутся уже от профита
    # с переносом, а к выплате идёт вся начисленная зарплата.
    prev_minus = max(q(payload["prev_minus"] or ZERO), ZERO)
    profit = q(profit_month - prev_minus)
    if plan:
        salary, percent = plan_salary(plan, profit)
    else:
        salary, percent = salary_for_profit(profit), ladder_percent(profit)
    next_minus = max(-profit, ZERO)
    return {
        "daily": daily,
        "total": {
            "income": month_metrics["income"],
            "spend_buyer": month_metrics["spend_buyer"],
            "spend_agent": q(agent_total),
            "costs": month_metrics["costs"],
            "profit": profit,
            "profit_month": profit_month,
            "roi": month_metrics["roi"],
            "salary_percent": percent,
            "salary": salary,
            "prev_minus": q(prev_minus),
            "next_minus": q(next_minus),
            "payout": salary,
        },
    }


def metrics_for(income: Decimal, spend_buyer: Decimal, costs: Decimal) -> dict:
    """Одна формула для дня, месяца и строки тира."""
    calculated = finance_day_metrics(income, spend_buyer, costs)
    return {
        "income": calculated["income"],
        "spend_buyer": q(spend_buyer),
        "costs": q(costs),
        "base": calculated["base"],
        "profit": calculated["profit"],
        "roi": calculated["roi"],
    }


async def profits(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer_ids: set[uuid.UUID],
    year: int,
    month: int,
) -> dict[uuid.UUID, list[dict]]:
    """Профит каждой книги баера за месяц — те же числа, что видно в Финансах.

    Список, а не одна сумма: у баера две книги, Tier1 и Tier2/3, и зарплата
    считается по каждой отдельно. Сложить их в одно число здесь значило бы
    незаметно поменять ступень сетки. Тир едет рядом с профитом — по нему
    сводка раскладывает фонд ЗП.

    Входящий долг берётся сохранённый, а не пересчитанный по всей истории:
    цепочку переносов поддерживает сама запись книги, и повторять её здесь
    значило бы читать все месяцы каждого человека ради одной суммы.
    """
    if not buyer_ids:
        return {}
    books = list(
        (
            await db.execute(
                select(FinanceBook)
                .where(
                    FinanceBook.workspace_id == workspace_id,
                    FinanceBook.buyer_id.in_(buyer_ids),
                    FinanceBook.year == year,
                    FinanceBook.month == month,
                )
                .order_by(FinanceBook.tier)
            )
        ).scalars()
    )
    loaded = await load_many(db, books)
    days_in_month = calendar.monthrange(year, month)[1]
    result: dict[uuid.UUID, list[dict]] = {}
    for book in books:
        result.setdefault(book.buyer_id, []).append(
            {
                "tier": book.tier,
                "profit": totals(loaded[book.id], days_in_month)["total"]["profit"],
            }
        )
    return result


async def workspace_profits(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    year: int,
    month: int,
) -> list[dict]:
    """Профит каждой книги воркспейса за месяц — весь профит компании частями.

    От него считается зарплата CMO: он ведёт не свою книгу и не одну ветку, а
    все команды сразу. Части те же, что и везде: по книге на тир, чтобы процент
    и ступень считались так же, как в самих Финансах.
    """
    books = list(
        (
            await db.execute(
                select(FinanceBook)
                .where(
                    FinanceBook.workspace_id == workspace_id,
                    FinanceBook.year == year,
                    FinanceBook.month == month,
                )
                .order_by(FinanceBook.tier)
            )
        ).scalars()
    )
    if not books:
        return []
    loaded = await load_many(db, books)
    days_in_month = calendar.monthrange(year, month)[1]
    return [
        {
            "tier": book.tier,
            "profit": totals(loaded[book.id], days_in_month)["total"]["profit"],
        }
        for book in books
    ]
