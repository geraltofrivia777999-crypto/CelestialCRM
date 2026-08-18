import calendar
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import accessible_user_ids, has_permission, require_permission
from app.models import (
    FinanceBook,
    FinanceBookDay,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    Partner,
    Status,
    User,
    UserParent,
)
from app.schemas import FinanceBookIn
from app.services.audit import audit
from app.services.country_tiers import tier_for, tier_map
from app.services.finance_books import (
    ZERO,
    before_period,
    load,
    load_many,
    metrics_for,
    q6,
    totals,
)
from app.services.finance_pull import push_book_changes_to_offers
from app.services.formulas import q
from app.services.geo import countries, normalize_geo
from app.services.salary import payroll, plan_for_book

router = APIRouter(tags=["finance"])

# Книг у баера две — по одной на тир. Спенд по Tier1 и Tier2/3 приходит из
# разных кабинетов, и складывать их в одну таблицу означало бы снова делить
# общий расход пропорцией.
BOOK_TIERS = ("T1", "T23")


async def _finance_user_ids(db: AsyncSession, current: User) -> set[uuid.UUID]:
    # Финансисту с правом ведения книг нужен весь воркспейс; тимлид с одним
    # просмотром по-прежнему ограничен своей веткой.
    if has_permission(current, "finance.manage"):
        return set(
            await db.scalars(
                select(User.id).where(User.workspace_id == current.workspace_id)
            )
        )
    return await accessible_user_ids(db, current)


async def _visible_buyer(db: AsyncSession, current: User, buyer_id: uuid.UUID) -> User:
    """Баер, книгу которого этому пользователю можно открыть.

    Иерархия та же, что и везде: тимлид видит свою ветку, финансист — всех, кого
    ему открыли. Чужая книга отвечает 404, а не 403 — по ней нельзя даже понять,
    существует ли такой баер.
    """
    visible = await _finance_user_ids(db, current)
    buyer = await db.get(User, buyer_id)
    if not buyer or buyer.workspace_id != current.workspace_id or buyer.id not in visible:
        raise HTTPException(status_code=404, detail="Баер не найден")
    return buyer


async def _previous_book(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer_id: uuid.UUID,
    year: int,
    month: int,
    tier: str,
) -> FinanceBook | None:
    """Ближайшая книга того же тира до этого месяца."""
    return await db.scalar(
        select(FinanceBook)
        .where(
            FinanceBook.workspace_id == workspace_id,
            FinanceBook.buyer_id == buyer_id,
            FinanceBook.tier == tier,
            before_period(year, month),
        )
        .order_by(FinanceBook.year.desc(), FinanceBook.month.desc())
        .limit(1)
    )


def _carried_offers(previous: dict) -> list[dict]:
    """Офферы прошлого месяца без цифр: список работ переезжает, депозиты — нет.

    Это заготовка, а не сохранённая книга: пока финансист ничего не тронул, в
    базе за новый месяц ничего нет. Первая же правка сохранит то, что он оставил.
    """
    return [
        {
            "id": None,
            "name": offer["name"],
            "partner": offer["partner"],
            "geo": offer.get("geo"),
            "rate": offer["rate"],
            "rate_currency": offer["rate_currency"],
            "tags": [{"name": tag["name"], "values": {}} for tag in offer["tags"]],
        }
        for offer in previous["offers"]
    ]


def _serialize(
    payload: dict,
    buyer: User,
    year: int,
    month: int,
    plan: dict | None = None,
    country_tiers: dict[str, str] | None = None,
) -> dict:
    """Книга как её видит клиент. `country_tiers` нужен только подписи тира
    рядом с гео — на расчёт он больше не влияет: тир задаёт сама таблица."""
    days_in_month = calendar.monthrange(year, month)[1]
    return {
        "buyer": {"id": str(buyer.id), "name": buyer.name},
        "year": year,
        "month": month,
        "days_in_month": days_in_month,
        "prev_minus": q(payload["prev_minus"] or ZERO),
        "eur_usd_rate": q6(payload.get("eur_usd_rate") or Decimal("1")),
        "days": {
            str(day): entry for day, entry in sorted(payload["days"].items())
        },
        "offers": [
            {
                **offer,
                # Тир считает сервер по справочнику: два места, считающие его
                # по-своему, однажды разойдутся.
                "tier": tier_for(offer.get("geo"), country_tiers or {}),
                "tags": [
                    {
                        "name": tag["name"],
                        "values": {
                            str(day): value for day, value in sorted(tag["values"].items())
                        },
                    }
                    for tag in offer["tags"]
                ],
            }
            for offer in payload["offers"]
        ],
        # Под своим ключом, иначе месячные итоги затирают номер месяца.
        "totals": totals(payload, days_in_month, plan),
        # Шкала зарплаты: ступени правила, если оно есть. Пустое поле — знак
        # клиенту считать прежней лестницей, а не прятать блок.
        "salary_plan": plan,
    }


async def _carry_into_month(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer_id: uuid.UUID,
    year: int,
    month: int,
    tier: str,
    current_book: FinanceBook | None,
) -> Decimal:
    """Рассчитать входящий долг, не полагаясь на сохранённый кеш.

    Это делает GET идемпотентным и сразу исправляет цепочку в ответе, даже если
    старый месяц отредактировали до появления этой автоматики. Пропущенные
    месяцы долг не обнуляют.
    """
    books = list(
        (
            await db.execute(
                select(FinanceBook)
                .where(
                    FinanceBook.workspace_id == workspace_id,
                    FinanceBook.buyer_id == buyer_id,
                    FinanceBook.tier == tier,
                    before_period(year, month),
                )
                .order_by(FinanceBook.year, FinanceBook.month)
            )
        ).scalars()
    )
    if not books:
        # Сохраняем самый ранний ручной остаток с прежней версии CRM как
        # начальный долг. Для новой книги начальное значение всегда нулевое.
        return max(q(current_book.prev_minus or ZERO), ZERO) if current_book else ZERO

    loaded_books = await load_many(db, books)
    carry = max(q(books[0].prev_minus or ZERO), ZERO)
    for book in books:
        book_payload = loaded_books[book.id]
        book_payload["prev_minus"] = carry
        month_totals = totals(
            book_payload, calendar.monthrange(book.year, book.month)[1]
        )
        carry = month_totals["total"]["next_minus"]
    return q(carry)


async def _recalculate_carry_chain(
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


def _period(year: int, month: int) -> int:
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Некорректный период")
    return calendar.monthrange(year, month)[1]


async def _visible_users(
    db: AsyncSession, current: User, *, active_only: bool = False
) -> list[User]:
    visible = await _finance_user_ids(db, current)
    filters = [User.workspace_id == current.workspace_id, User.id.in_(visible)]
    if active_only:
        filters.append(User.status == Status.active)
    return list(
        (
            await db.execute(select(User).where(*filters).order_by(User.name, User.login))
        ).scalars()
    )


async def _visible_parent_links(
    db: AsyncSession, visible_ids: set[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    if not visible_ids:
        return []
    return list(
        (
            await db.execute(
                select(UserParent.user_id, UserParent.parent_id).where(
                    UserParent.user_id.in_(visible_ids),
                    UserParent.parent_id.in_(visible_ids),
                )
            )
        ).all()
    )


@router.get("/finance/scopes")
async def scopes(
    year: int,
    month: int,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.view")),
) -> dict:
    _period(year, month)
    users = await _visible_users(db, current, active_only=True)
    # Партнёрки — те же, что в Офферах: они приходят из Keitaro, и книга должна
    # называть их так же, иначе одна и та же партнёрка разъедется по написаниям.
    partners = list(
        await db.scalars(
            select(Partner.name)
            .where(Partner.workspace_id == current.workspace_id)
            .order_by(Partner.name)
        )
    )
    by_id = {user.id: user for user in users}
    links = await _visible_parent_links(db, set(by_id))
    parent_ids = {parent_id for _, parent_id in links}
    teams = [
        {
            "id": str(user.id),
            "name": user.team_name or user.name or user.login,
            "lead_name": user.name or user.login,
        }
        for user in users
        if user.id in parent_ids
    ]
    return {
        # Страны для поля «Гео» в книге. Отдаём здесь, а не из справочника
        # настроек: у финансиста может не быть `settings.view`, и книга не
        # должна зависеть от чужого права.
        "countries": countries(),
        "partners": partners,
        "summaries": [
            {"scope": "all", "name": "Общая"},
            {"scope": "tier1", "name": "Tier1"},
            {"scope": "tier23", "name": "Tier2/3"},
        ],
        "teams": teams,
        "buyers": [
            {"id": str(user.id), "name": user.name or user.login} for user in users
        ],
    }


def _public_metrics(values: dict) -> dict:
    return {
        "income": values["income"],
        "spend": values["spend_buyer"],
        "costs": values["costs"],
        "profit": values["profit"],
        "roi": values["roi"],
    }


def _combined(rows: list[dict], *, keep_debt: bool = False) -> dict:
    calculated = metrics_for(
        sum((row["income"] for row in rows), ZERO),
        sum((row["spend_buyer"] for row in rows), ZERO),
        sum((row["costs"] for row in rows), ZERO),
    )
    if keep_debt:
        calculated["profit"] = q(sum((row["profit"] for row in rows), ZERO))
    return _public_metrics(calculated)


def _tier_rows(calculated: list[dict]) -> list[dict]:
    """Разрез по тирам — это группировка книг, а не деление чисел внутри книги.

    Баер ведёт Tier1 и Tier2/3 отдельными таблицами, поэтому спенд и costs
    здесь фактические: доли и пропорции больше не участвуют.
    """
    labels = {"T1": "Tier1", "T23": "Tier2/3"}
    rows = []
    for tier in ("T1", "T23"):
        parts = [item["total"] for item in calculated if item["tier"] == tier]
        rows.append(
            {
                "tier": tier,
                "name": labels[tier],
                "books": len(parts),
                **_combined(parts),
            }
        )
    return rows


def _daily_rows(calculated: list[dict], days_in_month: int) -> list[dict]:
    result = []
    for day in range(1, days_in_month + 1):
        parts = [book["daily"][day - 1] for book in calculated]
        result.append({"day": day, **_combined(parts)})
    return result


def _buyer_rows(
    users: list[User], by_buyer: dict[uuid.UUID, list[dict]], *, only_with_offers: bool
) -> list[dict]:
    """Строка на человека: его книги за месяц сложены вместе."""
    rows = []
    for user in users:
        books = by_buyer.get(user.id) or []
        offers = sum(book["offer_count"] for book in books)
        if only_with_offers and not offers:
            continue
        if not books and only_with_offers:
            continue
        rows.append(
            {
                "buyer_id": str(user.id),
                "buyer": user.name or user.login,
                "role": user.role.name,
                **_combined([book["total"] for book in books], keep_debt=True),
            }
        )
    return rows


def _children(links: list[tuple[uuid.UUID, uuid.UUID]]) -> dict[uuid.UUID, set[uuid.UUID]]:
    result: dict[uuid.UUID, set[uuid.UUID]] = {}
    for child_id, parent_id in links:
        result.setdefault(parent_id, set()).add(child_id)
    return result


def _branch_ids(root: uuid.UUID, children: dict[uuid.UUID, set[uuid.UUID]]) -> set[uuid.UUID]:
    seen = {root}
    queue = [root]
    while queue:
        for child in children.get(queue.pop(), set()):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen


def _salary_tier(tiers: set[str | None]) -> str:
    clean = {tier for tier in tiers if tier in {"T1", "T23"}}
    if len(clean) > 1:
        return "mixed"
    if clean == {"T1"}:
        return "T1"
    if clean == {"T23"}:
        return "T23"
    return "unassigned"


CATEGORY_LABELS = {
    "buyers": "Баеры",
    "team_leads": "Тимлиды",
    "cmo": "CMO",
    "other": "Другие роли",
}
TIER_LABELS = {
    "T1": "Tier1",
    "T23": "Tier2/3",
    "mixed": "Оба тира",
    "unassigned": "Без тира",
    "all": "",
}
# Набор карточек фонда фиксирован: пустая карточка отвечает на вопрос «а где
# CMO», ненарисованная — нет. Остальные сочетания добавляются, только если в них
# действительно есть деньги, иначе блок зарастает строками по нулю.
SALARY_GROUPS = (
    ("buyers", "T1"),
    ("buyers", "T23"),
    ("team_leads", "T1"),
    ("team_leads", "T23"),
    ("cmo", "all"),
)


def _salary_summary(
    source: dict,
    allowed_ids: set[uuid.UUID],
    users: dict[uuid.UUID, User],
    tiers: dict[uuid.UUID, set[str | None]],
    children: dict[uuid.UUID, set[uuid.UUID]],
) -> dict:
    groups: dict[tuple[str, str], Decimal] = {}
    people = []
    for row in source["rows"]:
        user_id = uuid.UUID(row["user_id"])
        if user_id not in allowed_ids or user_id not in users:
            continue
        user = users[user_id]
        role = user.role.name.lower()
        if "cmo" in role:
            category, tier = "cmo", "all"
        elif "team lead" in role or children.get(user_id):
            category = "team_leads"
            team_tiers = set()
            for member_id in _branch_ids(user_id, children):
                team_tiers.update(tiers.get(member_id, set()))
            tier = _salary_tier(team_tiers)
        elif "buyer" in role:
            category, tier = "buyers", _salary_tier(tiers.get(user_id, set()))
        else:
            category, tier = "other", "all"
        payout = Decimal(row["payout"])
        groups[(category, tier)] = groups.get((category, tier), ZERO) + payout
        people.append({**row, "role": user.role.name, "category": category, "tier": tier})

    keys = list(SALARY_GROUPS) + [key for key in groups if key not in SALARY_GROUPS]
    group_rows = [
        {
            "category": category,
            "tier": tier,
            "name": " · ".join(
                part for part in (CATEGORY_LABELS[category], TIER_LABELS[tier]) if part
            ),
            "amount": q(groups.get((category, tier), ZERO)),
        }
        for category, tier in keys
    ]
    total = q(sum((Decimal(row["payout"]) for row in people), ZERO))
    # Разбивка фонда по тирам: та часть начислений, что посчиталась от профита
    # книги Tier1 или Tier2/3. Фикс, вычеты и проценты от профита команды к
    # тиру не относятся, поэтому их видно отдельной строкой — иначе три числа
    # в таблице не складывались бы в четвёртое.
    by_tier: dict[str, Decimal] = {}
    for row in people:
        for tier, value in (row.get("by_tier") or {}).items():
            by_tier[tier] = by_tier.get(tier, ZERO) + Decimal(value)
    tier_rows = [
        {"tier": tier, "name": TIER_LABELS[tier], "amount": q(by_tier.get(tier, ZERO))}
        for tier in ("T1", "T23")
    ]
    rest = q(total - sum(by_tier.values(), ZERO))
    if rest:
        tier_rows.append({"tier": "other", "name": "Вне тиров", "amount": rest})
    tier_rows.append({"tier": "total", "name": "ЗП итого", "amount": total})
    return {
        "total": total,
        "groups": group_rows,
        "tiers": tier_rows,
        "people": people,
    }


@router.get("/finance/summary")
async def summary(
    year: int,
    month: int,
    scope: str = "all",
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.view")),
) -> dict:
    days_in_month = _period(year, month)
    visible_users = await _visible_users(db, current)
    users_by_id = {user.id: user for user in visible_users}
    visible_ids = set(users_by_id)
    links = await _visible_parent_links(db, visible_ids)
    children = _children(links)

    tier = None
    selected_ids = visible_ids
    team_id = None
    if scope == "tier1":
        tier = "T1"
    elif scope == "tier23":
        tier = "T23"
    elif scope.startswith("team:"):
        try:
            team_id = uuid.UUID(scope.split(":", 1)[1])
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Команда не найдена") from exc
        if team_id not in visible_ids or not children.get(team_id):
            raise HTTPException(status_code=404, detail="Команда не найдена")
        selected_ids = _branch_ids(team_id, children)
    elif scope != "all":
        raise HTTPException(status_code=422, detail="Неизвестная сводка")

    book_query = select(FinanceBook).where(
        FinanceBook.workspace_id == current.workspace_id,
        FinanceBook.buyer_id.in_(selected_ids),
        FinanceBook.year == year,
        FinanceBook.month == month,
    )
    if tier is not None:
        book_query = book_query.where(FinanceBook.tier == tier)
    books = list((await db.execute(book_query)).scalars())
    loaded = await load_many(db, books)
    calculated = []
    by_buyer: dict[uuid.UUID, list[dict]] = {}
    tiers_by_user: dict[uuid.UUID, set[str]] = {}
    for book in books:
        result = totals(loaded[book.id], days_in_month)
        result["tier"] = book.tier
        result["offer_count"] = len(loaded[book.id]["offers"])
        calculated.append(result)
        by_buyer.setdefault(book.buyer_id, []).append(result)
        tiers_by_user.setdefault(book.buyer_id, set()).add(book.tier)

    cards = _combined(
        [item["total"] for item in calculated], keep_debt=tier is None
    )
    # В тир-сводке разбивка по тирам повторила бы карточки одной строкой.
    tier_rows = [] if tier is not None else _tier_rows(calculated)
    buyer_rows = []
    if tier is not None or team_id is not None:
        row_users = [user for user in visible_users if user.id in selected_ids]
        buyer_rows = _buyer_rows(
            row_users, by_buyer, only_with_offers=tier is not None
        )

    salary = None
    if scope == "all" and has_permission(current, "salary.view"):
        salary_source = await payroll(db, current.workspace_id, year, month)
        salary = _salary_summary(
            salary_source, selected_ids, users_by_id, tiers_by_user, children
        )
    title = "Общая сводка"
    if tier == "T1":
        title = "Сводка Tier1"
    elif tier == "T23":
        title = "Сводка Tier2/3"
    elif team_id is not None:
        lead = users_by_id[team_id]
        title = f'Сводка по «{lead.team_name or lead.name or lead.login}»'
    return {
        "year": year,
        "month": month,
        "scope": scope,
        "title": title,
        "cards": cards,
        "tiers": tier_rows,
        "daily": _daily_rows(calculated, days_in_month),
        "buyers": buyer_rows,
        "salary": salary,
    }


@router.get("/finance/book")
async def get_book(
    buyer_id: uuid.UUID,
    year: int,
    month: int,
    tier: str = "T1",
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.view")),
) -> dict:
    """Одна таблица баера за месяц: Tier1 или Tier2/3.

    Спенд по тирам приходит из разных кабинетов, поэтому и таблицы разные —
    каждая со своими затратами, офферами, долгом и зарплатой.
    """
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Некорректный месяц")
    if tier not in BOOK_TIERS:
        raise HTTPException(status_code=422, detail="Неизвестный тир")
    buyer = await _visible_buyer(db, current, buyer_id)
    book = await db.scalar(
        select(FinanceBook).where(
            FinanceBook.workspace_id == current.workspace_id,
            FinanceBook.buyer_id == buyer_id,
            FinanceBook.year == year,
            FinanceBook.month == month,
            FinanceBook.tier == tier,
        )
    )
    book_payload = await load(db, book)
    book_payload["prev_minus"] = await _carry_into_month(
        db, current.workspace_id, buyer_id, year, month, tier, book
    )
    if book is None:
        # Месяца ещё нет: список офферов и валюта переезжают из прошлого, чтобы
        # не набирать вручную то же самое заново.
        previous = await _previous_book(
            db, current.workspace_id, buyer_id, year, month, tier
        )
        if previous is not None:
            previous_payload = await load(db, previous)
            book_payload["offers"] = _carried_offers(previous_payload)
            book_payload["eur_usd_rate"] = previous_payload["eur_usd_rate"]
    plan = await plan_for_book(db, current.workspace_id, buyer, year, month)
    tiers = await tier_map(db, current.workspace_id)
    return {
        **_serialize(book_payload, buyer, year, month, plan, tiers),
        "tier": tier,
    }


@router.get("/finance/buyer-overview")
async def buyer_overview(
    buyer_id: uuid.UUID,
    year: int,
    month: int,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.view")),
) -> dict:
    """Общая сводка баера: обе его таблицы за месяц, только чтение.

    Заполняют Tier1 и Tier2/3 по отдельности, а этот экран отвечает на
    вопрос «сколько вышло всего» — числа складываются, но не смешиваются.
    """
    days_in_month = _period(year, month)
    buyer = await _visible_buyer(db, current, buyer_id)
    books = list(
        (
            await db.execute(
                select(FinanceBook)
                .where(
                    FinanceBook.workspace_id == current.workspace_id,
                    FinanceBook.buyer_id == buyer_id,
                    FinanceBook.year == year,
                    FinanceBook.month == month,
                )
                .order_by(FinanceBook.tier)
            )
        ).scalars()
    )
    loaded = await load_many(db, books)
    calculated = []
    for book in books:
        result = totals(loaded[book.id], days_in_month)
        result["tier"] = book.tier
        result["offer_count"] = len(loaded[book.id]["offers"])
        calculated.append(result)
    monthly = [item["total"] for item in calculated]
    # Зарплата у каждой таблицы своя: ступень считается от её профита, поэтому
    # разбивка здесь точная, а не разложенная задним числом.
    by_tier = {tier: ZERO for tier in BOOK_TIERS}
    for item in calculated:
        by_tier[item["tier"]] += item["total"]["salary"]
    total_salary = q(sum(by_tier.values(), ZERO))
    salary_rows = [
        {"tier": tier, "name": TIER_LABELS[tier], "amount": q(by_tier[tier])}
        for tier in BOOK_TIERS
    ]
    salary_rows.append({"tier": "total", "name": "ЗП итого", "amount": total_salary})
    return {
        "buyer": {"id": str(buyer.id), "name": buyer.name},
        "year": year,
        "month": month,
        "days_in_month": days_in_month,
        "cards": _combined(monthly, keep_debt=True),
        "salary": {
            "total": total_salary,
            "label": "ЗП баера",
            "groups": [],
            "tiers": salary_rows,
            "people": [],
        },
        "tiers": _tier_rows(calculated),
        "daily": _daily_rows(calculated, days_in_month),
    }


@router.put("/finance/book")
async def save_book(
    payload: FinanceBookIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.manage")),
) -> dict:
    buyer = await _visible_buyer(db, current, payload.buyer_id)
    # Одна стабильная строка блокирует финансовую цепочку баера до того, как
    # начнётся создание книги или перезапись её дочерних значений. Поэтому два
    # параллельных PUT разных месяцев не рассчитают перенос по неполной истории.
    await db.execute(
        select(User.id).where(User.id == payload.buyer_id).with_for_update()
    )
    days_in_month = calendar.monthrange(payload.year, payload.month)[1]

    book = await db.scalar(
        select(FinanceBook).where(
            FinanceBook.workspace_id == current.workspace_id,
            FinanceBook.buyer_id == payload.buyer_id,
            FinanceBook.year == payload.year,
            FinanceBook.month == payload.month,
            FinanceBook.tier == payload.tier,
        )
    )
    created = book is None
    if created:
        book = FinanceBook(
            workspace_id=current.workspace_id,
            buyer_id=payload.buyer_id,
            year=payload.year,
            month=payload.month,
            tier=payload.tier,
            prev_minus=ZERO,
            eur_usd_rate=payload.eur_usd_rate,
        )
        db.add(book)
        await db.flush()
    else:
        book.eur_usd_rate = payload.eur_usd_rate

    # Книга приходит целиком, поэтому дни и офферы переписываются заново: так
    # удалённый оффер исчезает вместе со своими SOK, без сверки «что изменилось».
    await db.execute(delete(FinanceBookDay).where(FinanceBookDay.book_id == book.id))
    # Теги и их дни удаляются явно: bulk delete идёт мимо ORM-каскада, а на каскад
    # самой базы полагаться нельзя — SQLite его по умолчанию не применяет, и строки
    # удалённого оффера остались бы висеть сиротами.
    book_offers = select(FinanceBookOffer.id).where(FinanceBookOffer.book_id == book.id)
    book_tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(book_offers))
    await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(book_tags)))
    await db.execute(delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(book_offers)))
    await db.execute(
        delete(FinanceBookOffer).where(FinanceBookOffer.book_id == book.id)
    )
    await db.flush()

    for day, entry in payload.days.items():
        if day > days_in_month:
            continue
        if not (entry.spend_buyer or entry.spend_agent or entry.costs):
            continue
        db.add(
            FinanceBookDay(
                book_id=book.id,
                day=day,
                spend_buyer=entry.spend_buyer,
                spend_agent=entry.spend_agent,
                costs=entry.costs,
            )
        )

    for position, offer in enumerate(payload.offers):
        row = FinanceBookOffer(
            book_id=book.id,
            position=position,
            name=offer.name.strip(),
            partner=(offer.partner or "").strip() or None,
            geo=normalize_geo(offer.geo),
            rate=offer.rate,
            rate_currency=offer.rate_currency,
            # Книга приходит целиком и переписывается заново, поэтому связь со
            # справочником клиент возвращает обратно — иначе назначение баера
            # завело бы вторую строку того же оффера.
            source_offer_id=offer.source_offer_id,
        )
        db.add(row)
        await db.flush()
        for tag_position, tag in enumerate(offer.tags):
            tag_row = FinanceOfferTag(
                offer_id=row.id, position=tag_position, name=tag.name.strip()
            )
            db.add(tag_row)
            await db.flush()
            for day, deposits in tag.values.items():
                if day > days_in_month or not deposits:
                    continue
                db.add(FinanceTagDay(tag_id=tag_row.id, day=day, deposits=deposits))

    # Гео и ставка едут обратно в справочник: правку чаще замечает тот, кто
    # ведёт книгу, и без этого следующее назначение привезло бы другому баеру
    # старые числа.
    changed_offers = await push_book_changes_to_offers(
        db,
        current.workspace_id,
        [
            {
                "source_offer_id": offer.source_offer_id,
                "geo": normalize_geo(offer.geo),
                "rate": offer.rate,
                "rate_currency": offer.rate_currency,
            }
            for offer in payload.offers
        ],
    )

    await db.flush()
    # Входящий минус принадлежит серверу: значение из клиента намеренно не
    # используется. Пересчёт присваивает остаток, поэтому повторный PUT не
    # удваивает долг.
    await _recalculate_carry_chain(
        db, current.workspace_id, payload.buyer_id, payload.tier
    )

    await audit(
        db,
        current,
        "finance.book_saved",
        f"{'Создана' if created else 'Обновлена'} книга {buyer.name} "
        f"({payload.tier}) за {payload.month:02d}.{payload.year}"
        + (f"; справочник обновлён: {', '.join(changed_offers)}" if changed_offers else ""),
        request=request,
        entity_id=str(book.id),
    )
    await db.commit()
    book_payload = await load(db, book)
    book_payload["prev_minus"] = book.prev_minus
    book_payload["eur_usd_rate"] = book.eur_usd_rate
    plan = await plan_for_book(
        db, current.workspace_id, buyer, payload.year, payload.month
    )
    tiers = await tier_map(db, current.workspace_id)
    return {
        **_serialize(book_payload, buyer, payload.year, payload.month, plan, tiers),
        "tier": payload.tier,
    }
