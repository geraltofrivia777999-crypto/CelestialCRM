import calendar
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_today
from app.core.database import get_db
from app.core.deps import (
    accessible_user_ids,
    has_full_access,
    has_permission,
    require_permission,
)
from app.models import (
    FinanceBook,
    FinanceBookDay,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    Offer,
    OfferBuyer,
    Partner,
    PartnerIntegration,
    PartnerSyncRun,
    Status,
    User,
    UserParent,
)
from app.schemas import FinanceBookIn, FinancePartnersIn
from app.services import finance_spend
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
from app.services.finance_books import recalculate_carry_chain as _recalculate_carry_chain
from app.services.finance_pull import push_book_changes_to_offers
from app.services.formulas import q
from app.services.geo import countries, normalize_geo
from app.services.partner_integrations import PartnerServiceClient
from app.services.partner_sync import fail_run, finish_run, perform_sync
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


async def _sees_team_summaries(db: AsyncSession, current: User) -> bool:
    """Сводки складывают книги нескольких человек — значит, не для всех ролей.

    «Общая», «Tier1», «Tier2/3» и сводка по команде показывают чужие цифры,
    поэтому роль с областью «Только свои данные» их не получает: ей остаются
    собственные книги. Полный доступ по правам остаётся полным.
    """
    scope = getattr(current.role, "data_scope", None) or "team"
    if scope != "own":
        return True
    return await has_full_access(db, current)


async def _sees_workspace_summaries(db: AsyncSession, current: User) -> bool:
    """Сводки «Общая», «Tier1», «Tier2/3» — по переключателю в роли.

    Сводки по командам остаются на области доступа (`_sees_team_summaries`):
    это разные вопросы — видеть свою команду и видеть общий срез.
    """
    if getattr(current.role, "show_finance_summaries", True):
        return True
    return await has_full_access(db, current)


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
        # Теги баера: оффер, добавленный прямо в книге, получает их строками.
        "buyer": {
            "id": str(buyer.id),
            "name": buyer.name,
            "tags": list(buyer.finance_tags or []),
        },
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



def _period(year: int, month: int) -> int:
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Некорректный период")
    return calendar.monthrange(year, month)[1]


async def _visible_users(db: AsyncSession, current: User) -> list[User]:
    visible = await _finance_user_ids(db, current)
    filters = [User.workspace_id == current.workspace_id, User.id.in_(visible)]
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
    # Заблокированный баер остаётся в списке книг: его месяцы уже посчитаны, и
    # закрывать их всё равно придётся. Блокировка забирает вход в CRM, а не
    # историю работы.
    users = await _visible_users(db, current)
    # Партнёрки — те же, что в Офферах: они приходят из Keitaro, и книга должна
    # называть их так же, иначе одна и та же партнёрка разъедется по написаниям.
    partners = list(
        await db.scalars(
            select(Partner.name)
            .where(Partner.workspace_id == current.workspace_id)
            .order_by(Partner.name)
        )
    )
    summaries_allowed = await _sees_team_summaries(db, current)
    workspace_summaries = await _sees_workspace_summaries(db, current)
    by_id = {user.id: user for user in users}
    links = await _visible_parent_links(db, set(by_id))
    parent_ids = {parent_id for _, parent_id in links}
    # Сводка по команде — только у названной команды. Безымянная показывалась
    # под именем тимлида и выглядела как ещё один баер в разделе «Команды».
    teams = [] if not summaries_allowed else [
        {
            "id": str(user.id),
            "name": user.team_name.strip(),
            "lead_name": user.name or user.login,
        }
        for user in users
        if user.id in parent_ids and (user.team_name or "").strip()
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
        ] if workspace_summaries else [],
        "teams": teams,
        "buyers": [
            {
                "id": str(user.id),
                "name": user.name or user.login,
                "blocked": user.status != Status.active,
            }
            for user in users
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
        if only_with_offers and not offers and not any(
            book["total"]["spend_buyer"] or book["total"]["costs"] for book in books
        ):
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


# Роль сотрудника — свободный текст: рабочие пространства переименовывают её
# как удобно, и «СМО» кириллицей встречается чаще латинского CMO. Матчинг по
# одному латинскому написанию отправлял такого человека в «Другие роли», а
# карточка «ЗП СМО» оставалась с нулём.
CATEGORY_KEYWORDS = (
    ("cmo", ("cmo", "смо", "chief marketing")),
    ("team_leads", ("team lead", "teamlead", "тимлид", "тим лид", "тим-лид")),
    ("buyers", ("buyer", "баер", "байер")),
)


def _role_category(role_name: str) -> str | None:
    role = (role_name or "").lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in role for keyword in keywords):
            return category
    return None


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
        known = _role_category(user.role.name)
        if known == "cmo":
            category, tier = "cmo", "all"
        elif known == "team_leads" or children.get(user_id):
            category = "team_leads"
            team_tiers = set()
            for member_id in _branch_ids(user_id, children):
                team_tiers.update(tiers.get(member_id, set()))
            tier = _salary_tier(team_tiers)
        elif known == "buyers":
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
    if scope.startswith("team:"):
        allowed = await _sees_team_summaries(db, current)
        detail = "Сводки по командам доступны ролям с доступом к данным команды"
    else:
        allowed = await _sees_workspace_summaries(db, current)
        detail = "Сводки «Общая», «Tier1» и «Tier2/3» закрыты для вашей роли"
    if not allowed:
        raise HTTPException(status_code=403, detail=detail)
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

    unassigned_spend = await finance_spend.refresh_period(
        db, current.workspace_id, selected_ids, year, month
    )
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

    # Профит сводки — это доход минус спенд и costs за сам месяц. Раньше в него
    # входил ещё и минус прошлых месяцев, и карточки не сходились между собой:
    # три числа про октябрь, а четвёртое — про всю историю. Перенос живёт у
    # баера, в его таблице офферов, и в сводку не поднимается.
    cards = _combined([item["total"] for item in calculated])
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
    await db.commit()
    return {
        "year": year,
        "month": month,
        "scope": scope,
        "unassigned_spend": unassigned_spend,
        "title": title,
        "cards": cards,
        "tiers": tier_rows,
        "daily": _daily_rows(calculated, days_in_month),
        "buyers": buyer_rows,
        "salary": salary,
    }


# --- сводка «Партнёрки» -------------------------------------------------------
#
# Общий лист для заполнения: строки тех же книг баеров, но сразу по всем людям
# и с фильтром по партнёрке. Числа те же самые — сводка пишет прямо в книги,
# а не хранит свою копию.


async def _partners_rows(
    db: AsyncSession, current: User, year: int, month: int
) -> dict:
    buyers = await _visible_users(db, current)
    buyer_ids = [buyer.id for buyer in buyers]
    if not buyer_ids:
        return {"buyers": [], "partners": [], "days_in_month": calendar.monthrange(year, month)[1]}
    books = list(
        (
            await db.execute(
                select(FinanceBook).where(
                    FinanceBook.workspace_id == current.workspace_id,
                    FinanceBook.buyer_id.in_(buyer_ids),
                    FinanceBook.year == year,
                    FinanceBook.month == month,
                )
            )
        ).scalars()
    )
    book_by_id = {book.id: book for book in books}
    offers = list(
        (
            await db.execute(
                select(FinanceBookOffer)
                .where(FinanceBookOffer.book_id.in_([book.id for book in books]))
                .order_by(FinanceBookOffer.position, FinanceBookOffer.name)
            )
        ).scalars()
    ) if books else []
    tags = list(
        (
            await db.execute(
                select(FinanceOfferTag)
                .where(FinanceOfferTag.offer_id.in_([offer.id for offer in offers]))
                .order_by(FinanceOfferTag.position, FinanceOfferTag.name)
            )
        ).scalars()
    ) if offers else []
    values = list(
        (
            await db.execute(
                select(FinanceTagDay).where(
                    FinanceTagDay.tag_id.in_([tag.id for tag in tags])
                )
            )
        ).scalars()
    ) if tags else []
    by_tag: dict[uuid.UUID, dict[int, Decimal]] = {}
    for row in values:
        by_tag.setdefault(row.tag_id, {})[row.day] = row.deposits
    by_offer: dict[uuid.UUID, list[dict]] = {}
    for tag in tags:
        by_offer.setdefault(tag.offer_id, []).append({
            "id": str(tag.id),
            "name": tag.name,
            "values": {str(day): q(value) for day, value in sorted(
                by_tag.get(tag.id, {}).items()
            )},
        })

    # Назначенные офферы справочника: строки, которых в книге ещё нет, тоже
    # показываем — ради них сводку и завели, чтобы заполнять не открывая книги.
    assigned = list(
        (
            await db.execute(
                select(OfferBuyer.user_id, Offer)
                .join(Offer, Offer.id == OfferBuyer.offer_id)
                .where(
                    OfferBuyer.user_id.in_(buyer_ids),
                    Offer.workspace_id == current.workspace_id,
                    Offer.connection_id.is_(None),
                )
                .order_by(Offer.name)
            )
        ).all()
    )
    partner_names = {
        row.id: row.name
        for row in (
            await db.execute(
                select(Partner).where(Partner.workspace_id == current.workspace_id)
            )
        ).scalars()
    }
    tiers = await tier_map(db, current.workspace_id)

    rows: dict[uuid.UUID, list[dict]] = {}
    seen: dict[uuid.UUID, set[uuid.UUID]] = {}
    for offer in offers:
        book = book_by_id.get(offer.book_id)
        if not book:
            continue
        rows.setdefault(book.buyer_id, []).append({
            "book_offer_id": str(offer.id),
            "source_offer_id": str(offer.source_offer_id) if offer.source_offer_id else None,
            "name": offer.name,
            "partner": offer.partner,
            "geo": offer.geo,
            "rate": q(offer.rate or ZERO),
            "rate_currency": offer.rate_currency or "USD",
            "tier": book.tier,
            "tags": by_offer.get(offer.id, []),
        })
        if offer.source_offer_id:
            seen.setdefault(book.buyer_id, set()).add(offer.source_offer_id)
    for buyer_id, offer in assigned:
        if offer.id in seen.get(buyer_id, set()):
            continue
        rows.setdefault(buyer_id, []).append({
            "book_offer_id": None,
            "source_offer_id": str(offer.id),
            "name": offer.name,
            "partner": partner_names.get(offer.partner_id),
            "geo": offer.geo,
            "rate": q(offer.cpa or ZERO),
            "rate_currency": offer.cpa_currency or "USD",
            "tier": tier_for(offer.geo, tiers),
            "tags": [],
        })

    eur_by_buyer: dict[uuid.UUID, Decimal] = {}
    for book in books:
        eur_by_buyer.setdefault(book.buyer_id, book.eur_usd_rate or Decimal("1"))
    partners = sorted({
        row["partner"] for offers_of in rows.values() for row in offers_of if row["partner"]
    })
    return {
        "year": year,
        "month": month,
        "days_in_month": calendar.monthrange(year, month)[1],
        "partners": partners,
        "buyers": [
            {
                "id": str(buyer.id),
                "name": buyer.name or buyer.login,
                "eur_usd_rate": q6(eur_by_buyer.get(buyer.id, Decimal("1"))),
                "offers": rows.get(buyer.id, []),
            }
            for buyer in buyers
            if rows.get(buyer.id)
        ],
    }


@router.get("/finance/partners")
async def partners_sheet(
    year: int,
    month: int,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.view")),
) -> dict:
    """Строки книг всех доступных баеров за месяц — одним листом."""
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Некорректный месяц")
    await finance_spend.refresh_period(
        db, current.workspace_id, await _finance_user_ids(db, current), year, month
    )
    await db.commit()
    return await _partners_rows(db, current, year, month)


async def _partners_book(
    db: AsyncSession, current: User, buyer_id: uuid.UUID, year: int, month: int, tier: str
) -> FinanceBook:
    book = await db.scalar(
        select(FinanceBook).where(
            FinanceBook.workspace_id == current.workspace_id,
            FinanceBook.buyer_id == buyer_id,
            FinanceBook.year == year,
            FinanceBook.month == month,
            FinanceBook.tier == tier,
        )
    )
    if book:
        return book
    return await finance_spend.create_book_from_previous(
        db, current.workspace_id, buyer_id, year, month, tier
    )


@router.put("/finance/partners")
async def save_partners_sheet(
    payload: FinancePartnersIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.manage")),
) -> dict:
    """Записать введённое в сводке прямо в книги баеров.

    Лист не хранит своих чисел: тег и его депозиты ложатся в книгу того баера,
    у которого этот оффер, — в ту же строку, что он увидит у себя. Оффера,
    которого в книге ещё не было, заводим из справочника: тир берётся по гео.
    """
    await finance_spend.lock_workspace(db, current.workspace_id)
    days_in_month = calendar.monthrange(payload.year, payload.month)[1]
    tiers = await tier_map(db, current.workspace_id)
    touched: set[tuple[uuid.UUID, str]] = set()
    saved: list[dict] = []
    for entry in payload.tags:
        buyer = await _visible_buyer(db, current, entry.buyer_id)
        offer_row = None
        book = None
        if entry.book_offer_id:
            offer_row = await db.get(FinanceBookOffer, entry.book_offer_id)
            book = await db.get(FinanceBook, offer_row.book_id) if offer_row else None
            if not book or book.buyer_id != buyer.id or book.workspace_id != current.workspace_id:
                # Сохранение книги пересоздаёт её строки с новыми id, поэтому
                # присланный id мог устареть. Это не повод терять введённое:
                # ту же строку находим по офферу справочника.
                offer_row = None
                book = None
        if offer_row is None:
            source = await db.get(Offer, entry.source_offer_id) if entry.source_offer_id else None
            if not source or source.workspace_id != current.workspace_id:
                raise HTTPException(status_code=404, detail="Оффер не найден")
            book = await _partners_book(
                db, current, buyer.id, payload.year, payload.month,
                tier_for(source.geo, tiers),
            )
            await db.flush()
            offer_row = await db.scalar(
                select(FinanceBookOffer).where(
                    FinanceBookOffer.book_id == book.id,
                    FinanceBookOffer.source_offer_id == source.id,
                )
            )
            if offer_row is None:
                partner = await db.get(Partner, source.partner_id) if source.partner_id else None
                position = await db.scalar(
                    select(func.count()).select_from(FinanceBookOffer).where(
                        FinanceBookOffer.book_id == book.id
                    )
                )
                offer_row = FinanceBookOffer(
                    book_id=book.id,
                    source_offer_id=source.id,
                    position=position or 0,
                    name=source.name,
                    partner=partner.name if partner else None,
                    geo=normalize_geo(source.geo),
                    rate=source.cpa or ZERO,
                    rate_currency=source.cpa_currency or "USD",
                )
                db.add(offer_row)
                await db.flush()
        touched.add((buyer.id, book.tier))

        name = entry.name.strip()
        tag_row = None
        if entry.tag_id:
            tag_row = await db.get(FinanceOfferTag, entry.tag_id)
            if tag_row and tag_row.offer_id != offer_row.id:
                tag_row = None
        if tag_row is None:
            # Тег ищем по названию: у баера он мог быть заведён раньше или
            # пересоздан сохранением книги. Нет такого — заведём новый.
            tag_row = await db.scalar(
                select(FinanceOfferTag).where(
                    FinanceOfferTag.offer_id == offer_row.id,
                    FinanceOfferTag.name == name,
                )
            )
        if tag_row is None and name:
            # Баер часто заводит строку, не называя её. Эта безымянная строка
            # получает название из сводки вместо создания второй рядом.
            tag_row = await db.scalar(
                select(FinanceOfferTag)
                .where(
                    FinanceOfferTag.offer_id == offer_row.id,
                    FinanceOfferTag.name == "",
                )
                .order_by(FinanceOfferTag.position)
            )
        if entry.drop:
            if tag_row:
                await db.execute(
                    delete(FinanceTagDay).where(FinanceTagDay.tag_id == tag_row.id)
                )
                await db.delete(tag_row)
            saved.append({
                "buyer_id": str(buyer.id),
                "book_offer_id": str(offer_row.id),
                "tag_id": None,
                "tier": book.tier,
            })
            continue
        if tag_row is None:
            position = await db.scalar(
                select(func.count()).select_from(FinanceOfferTag).where(
                    FinanceOfferTag.offer_id == offer_row.id
                )
            )
            tag_row = FinanceOfferTag(
                offer_id=offer_row.id, position=position or 0, name=name
            )
            db.add(tag_row)
            await db.flush()
        else:
            tag_row.name = name

        existing = {
            row.day: row
            for row in (
                await db.execute(
                    select(FinanceTagDay).where(FinanceTagDay.tag_id == tag_row.id)
                )
            ).scalars()
        }
        for day, deposits in entry.values.items():
            if not 1 <= day <= days_in_month:
                continue
            row = existing.get(day)
            if not deposits:
                if row:
                    await db.delete(row)
                continue
            if row:
                row.deposits = deposits
            else:
                db.add(FinanceTagDay(tag_id=tag_row.id, day=day, deposits=deposits))
        saved.append({
            "buyer_id": str(buyer.id),
            "book_offer_id": str(offer_row.id),
            "tag_id": str(tag_row.id),
            "tier": book.tier,
        })

    await db.flush()
    for buyer_id, tier in touched:
        await _recalculate_carry_chain(db, current.workspace_id, buyer_id, tier)
    if touched:
        await audit(
            db, current, "finance.partners_saved",
            f"Сводка «Партнёрки» за {payload.month:02d}.{payload.year}: "
            f"строк {len(payload.tags)}, книг {len(touched)}",
            request=request,
        )
    await db.commit()
    return {"saved": saved}


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
    unassigned_spend = await finance_spend.refresh_period(
        db, current.workspace_id, {buyer_id}, year, month
    )
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
    # Тир книги решает, какая база «своя»: у Tier1 и Tier2/3 бывают разные
    # проценты, и шкала книги должна показывать её собственный.
    plan = await plan_for_book(db, current.workspace_id, buyer, year, month, tier)
    tiers = await tier_map(db, current.workspace_id)
    await db.commit()
    return {
        "unassigned_spend": unassigned_spend,
        **_serialize(book_payload, buyer, year, month, plan, tiers),
        "tier": tier,
    }


@router.post("/finance/book/sync-partners")
async def sync_partners_for_book(
    request: Request,
    buyer_id: uuid.UUID,
    year: int,
    month: int,
    tier: str = "T1",
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("finance.manage")),
) -> dict:
    """Подтянуть депозиты из партнёрок в открытую таблицу.

    Сервис партнёрок ничего не собирает сам по себе — он идёт в ПП только когда
    его об этом просят. Поэтому кнопка стоит там, где на результат смотрят: в
    финансах, рядом с той самой книгой.

    Период берётся по книге (месяц, но не дальше сегодняшнего дня — будущее
    партнёрка всё равно не отдаст), офферы — те, что стоят в этой таблице, а
    теги — те, что в ней уже заведены. Ничего дополнительно указывать не нужно:
    и то, и другое уже есть на экране.
    """
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Некорректный месяц")
    if tier not in BOOK_TIERS:
        raise HTTPException(status_code=422, detail="Неизвестный тир")
    await _visible_buyer(db, current, buyer_id)

    date_from = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    date_to = date(year, month, last_day)
    today = business_today()
    if date_to > today:
        date_to = today
    if date_from > today:
        raise HTTPException(
            status_code=422, detail="Месяц ещё не начался — тянуть нечего"
        )

    integrations = list(
        (
            await db.execute(
                select(PartnerIntegration).where(
                    PartnerIntegration.workspace_id == current.workspace_id,
                    PartnerIntegration.is_enabled.is_(True),
                )
            )
        ).scalars()
    )
    if not integrations:
        raise HTTPException(
            status_code=422,
            detail=(
                "Нет включённых интеграций с ПП. Заведите подключение в "
                "«Настройках» — там нужны только адрес сервиса и API-ключ."
            ),
        )

    upserted = 0
    pending = 0
    skipped = 0
    reasons: list[str] = []
    errors: list[str] = []
    # id запоминаем до работы: откат сессии обесценивает объекты, и обратиться
    # к ним после него значило бы уйти в ленивую загрузку.
    integration_ids = [row.id for row in integrations]
    for integration_id in integration_ids:
        integration = await db.get(PartnerIntegration, integration_id)
        if not integration:
            continue
        run = PartnerSyncRun(
            integration_id=integration.id,
            date_from=date_from,
            date_to=date_to,
            status="running",
            trigger="manual",
        )
        db.add(run)
        integration.last_sync_status = "running"
        await db.commit()
        await db.refresh(run)
        run_id = run.id
        try:
            client = PartnerServiceClient(integration)
            result = await perform_sync(db, integration, client, date_from, date_to)
            finish_run(run, integration, result)
            upserted += result["upserted"]
            pending += result.get("pending", 0)
            skipped += result.get("skipped", 0)
            reasons.extend(result.get("reasons", []))
        except Exception as exc:  # noqa: BLE001 — одна интеграция не роняет остальные
            await db.rollback()
            run = await db.get(PartnerSyncRun, run_id)
            stored = await db.get(PartnerIntegration, integration_id)
            if run and stored:
                errors.append(f"«{stored.partner_name}»: {fail_run(run, stored, exc)}")
        await db.commit()

    # Откат внутри цикла обесценивает и объект текущего пользователя — аудит
    # ушёл бы в ленивую загрузку и упал. Перечитываем его перед записью.
    await db.refresh(current)
    await audit(
        db, current, "finance.partner_sync",
        f"Синк ПП в книгу за {month:02d}.{year}: записано {upserted}, "
        f"без строки {pending}",
        request=request,
    )
    await db.commit()
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "integrations": len(integrations),
        "upserted": upserted,
        "pending": pending,
        "skipped": skipped,
        "reasons": reasons[:20],
        "errors": errors,
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
    unassigned_spend = await finance_spend.refresh_period(
        db, current.workspace_id, {buyer_id}, year, month
    )
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
        # Общая сводка должна повторять расчёт каждой открытой таблицы.
        # Без плана totals() молча применяет старую лестницу зарплаты, поэтому
        # «ЗП баера» расходилась с суммой Tier1 и Tier2/3 при новых правилах.
        plan = await plan_for_book(
            db, current.workspace_id, buyer, year, month, book.tier
        )
        result = totals(loaded[book.id], days_in_month, plan)
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
    await db.commit()
    return {
        "unassigned_spend": unassigned_spend,
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
    await finance_spend.lock_workspace(db, current.workspace_id)
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
        book = await finance_spend.create_book_from_previous(
            db, current.workspace_id, payload.buyer_id, payload.year, payload.month, payload.tier
        )
    book.eur_usd_rate = payload.eur_usd_rate

    await finance_spend.pull_spend_to_books(
        db, current.workspace_id, payload.buyer_id, payload.year, payload.month
    )
    existing_days = {row.day: row for row in (await db.scalars(
        select(FinanceBookDay).where(FinanceBookDay.book_id == book.id)
    )).all()}

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

    for day in sorted(set(payload.days) | set(existing_days)):
        if day > days_in_month:
            continue
        entry = payload.days.get(day)
        old = existing_days.get(day)
        media_amount = old.media_spend if old and old.media_spend is not None else ZERO
        if entry is not None and "manual_spend" in entry.model_fields_set:
            manual = entry.manual_spend
        elif (entry is not None and "spend_buyer" in entry.model_fields_set
              and (old is None or old.media_spend is None)):
            # Compatibility for manual-only books opened in an older browser.
            manual = entry.spend_buyer if entry.spend_buyer else None
        else:
            # An omitted override is not an instruction to clear another user's edit.
            manual = old.manual_spend if old is not None else None
        effective = manual if manual is not None else media_amount
        spend_agent = entry.spend_agent if entry is not None else ZERO
        costs = entry.costs if entry is not None else ZERO
        if not (effective or spend_agent or costs or media_amount or manual is not None
                or (old is not None and old.media_spend is not None)):
            continue
        db.add(FinanceBookDay(
            book_id=book.id, day=day, spend_buyer=effective, media_spend=old.media_spend if old else None,
            manual_spend=manual, spend_agent=spend_agent, costs=costs,
        ))

    for position, offer in enumerate(payload.offers):
        if offer.sync_from_catalog and offer.source_offer_id:
            source = await db.scalar(
                select(Offer).where(
                    Offer.id == offer.source_offer_id,
                    Offer.workspace_id == current.workspace_id,
                )
            )
            if source is not None:
                offer.name = source.name
                offer.geo = source.geo
                offer.rate = source.cpa
                offer.rate_currency = source.cpa_currency or "USD"
                offer.partner = (
                    await db.scalar(select(Partner.name).where(Partner.id == source.partner_id))
                    if source.partner_id else None
                )
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
            locked_fields=list(dict.fromkeys(offer.locked_fields)),
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
                "locked_fields": offer.locked_fields,
            }
            for offer in payload.offers
        ],
    )

    await db.flush()
    if changed_offers:
        await finance_spend.refresh_workspace(db, current.workspace_id)
    else:
        await finance_spend.pull_spend_to_books(
            db, current.workspace_id, payload.buyer_id, payload.year, payload.month
        )
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
        db, current.workspace_id, buyer, payload.year, payload.month, payload.tier
    )
    tiers = await tier_map(db, current.workspace_id)
    return {
        **_serialize(book_payload, buyer, payload.year, payload.month, plan, tiers),
        "tier": payload.tier,
    }
