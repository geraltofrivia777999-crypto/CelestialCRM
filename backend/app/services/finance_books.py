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
    Offer,
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
            "media_spend": row.media_spend or ZERO,
            "manual_spend": row.manual_spend if row.media_spend is not None else (
                row.manual_spend if row.manual_spend is not None else (row.spend_buyer or None)
            ),
            "spend_agent": row.spend_agent,
            "costs": row.costs,
        }
    # ID оффера у партнёрки: в книге его не правят, но по нему сверяют строку с
    # кабинетом ПП и понимают, почему депозиты приехали или не приехали.
    source_ids = {row.source_offer_id for row in offer_rows if row.source_offer_id}
    externals: dict[uuid.UUID, str | None] = {}
    if source_ids:
        externals = {
            row.id: row.external_id
            for row in (
                await db.execute(select(Offer).where(Offer.id.in_(source_ids)))
            ).scalars()
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
                "external_id": externals.get(row.source_offer_id),
                "rate": row.rate,
                "rate_currency": row.rate_currency or "USD",
                "locked_fields": row.locked_fields or [],
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
    profit = month_metrics["profit"]
    # Профит месяца — это доход минус спенд и costs, и больше ничего: долг
    # прошлого месяца к работе этого месяца отношения не имеет. Считается он
    # только в зарплате — ступень и начисление берутся от профита за вычетом
    # долга, и он же решает, что перейдёт в следующий месяц.
    prev_minus = max(q(payload["prev_minus"] or ZERO), ZERO)
    profit_after_debt = q(profit - prev_minus)
    if plan:
        salary, percent = plan_salary(plan, profit_after_debt)
    else:
        salary, percent = (
            salary_for_profit(profit_after_debt),
            ladder_percent(profit_after_debt),
        )
    next_minus = max(-profit_after_debt, ZERO)
    return {
        "daily": daily,
        "total": {
            "income": month_metrics["income"],
            "spend_buyer": month_metrics["spend_buyer"],
            "spend_agent": q(agent_total),
            "costs": month_metrics["costs"],
            "profit": profit,
            # Профит с погашенным долгом: от него считается зарплата и перенос.
            "profit_after_debt": profit_after_debt,
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
    from app.services.finance_spend import refresh_period

    await refresh_period(db, workspace_id, buyer_ids, year, month)
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
                # Для зарплаты профит берётся с погашенным долгом — и у самого
                # баера, и у тимлида с CMO, которые считают от тех же частей.
                "profit": totals(loaded[book.id], days_in_month)["total"][
                    "profit_after_debt"
                ],
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
    from app.models import User
    from app.services.finance_spend import refresh_period

    buyers = set(await db.scalars(select(User.id).where(User.workspace_id == workspace_id)))
    await refresh_period(db, workspace_id, buyers, year, month)
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
            "profit": totals(loaded[book.id], days_in_month)["total"][
                "profit_after_debt"
            ],
        }
        for book in books
    ]


async def recalculate_carry_chain(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer_id: uuid.UUID,
    tier: str,
) -> None:
    """Пересчитать сохранённый входящий долг всех месяцев этой таблицы.

    Цепочка своя у каждого тира: минус Tier2/3 не гасится прибылью Tier1,
    потому что и зарплата по ним считается отдельно.

    Строки блокируются до конца транзакции, чтобы параллельные правки двух
    месяцев не записали разные версии одной цепочки. SQLite в тестах блокировку
    игнорирует, PostgreSQL на VPS применяет её.
    """
    books = list(
        (
            await db.execute(
                select(FinanceBook)
                .where(
                    FinanceBook.workspace_id == workspace_id,
                    FinanceBook.buyer_id == buyer_id,
                    FinanceBook.tier == tier,
                )
                .order_by(FinanceBook.year, FinanceBook.month)
                .with_for_update()
            )
        ).scalars()
    )
    if not books:
        return

    # Первый уже существовавший ручной минус остаётся начальным остатком:
    # так обновление не потеряет данные, которые финансист ввёл до автоматики.
    loaded_books = await load_many(db, books)
    carry = max(q(books[0].prev_minus or ZERO), ZERO)
    for book in books:
        book.prev_minus = carry
        book_payload = loaded_books[book.id]
        book_payload["prev_minus"] = carry
        month_totals = totals(
            book_payload, calendar.monthrange(book.year, book.month)[1]
        )
        carry = month_totals["total"]["next_minus"]
