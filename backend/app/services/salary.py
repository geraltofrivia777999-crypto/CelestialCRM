"""Расчёт зарплаты по правилам — «Настройки → Расчет ЗП».

Здесь три вещи: список баз, от которых вообще можно считать; выбор правила,
которое действует на человека в конкретном месяце; и применение компонентов к
числам этого месяца.

База — это не произвольная формула, а выбор из двух показателей Финансов:
профит самого человека и профит его команды. Свободный ввод формул выглядел бы
гибче, но любая опечатка в нём превращается в неверную зарплату, о которой
узнают уже после выплаты.

Оба показателя читаются из книги Финансов — из той же таблицы и той же формулы,
которую финансист видит на экране. Иначе правило считало бы одно, а «Шкала
зарплаты» в Финансах — другое.
"""

import calendar
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SalaryComponent, SalaryRule, Status, User, UserParent
from app.services import finance_books
from app.services.formulas import grid_percent, q

ZERO = Decimal("0")

# Показатели, от которых можно считать зарплату. Ключ хранится в базе, поэтому
# переименовывать его нельзя — только добавлять новые.
BASES = {
    "finance_profit_t1": {
        "label": "Профит T1 (Финансы)",
        "hint": "Книга Tier1 этого человека: доход минус спенд, косты и долг",
    },
    "finance_profit_t23": {
        "label": "Профит T2/3 (Финансы)",
        "hint": "Книга Tier2/3 этого человека: доход минус спенд, косты и долг",
    },
    # Обе книги одной базой. Ключ хранится в правилах, заведённых до разделения
    # на тиры, поэтому запись остаётся — но в выборе новых правил её нет:
    # проценты по Tier1 и Tier2/3 обычно разные, и общая база это скрывала.
    "finance_profit": {
        "label": "Профит (Финансы)",
        "hint": "Обе книги этого человека — старая база, оставлена для прежних правил",
        "hidden": True,
    },
    "team_profit": {
        "label": "Профит команды (Финансы)",
        "hint": "Сумма книг подчинённых — без книги самого тимлида",
    },
    "company_profit": {
        "label": "Весь профит (Финансы)",
        "hint": "Сумма книг всех баеров компании — база для CMO",
    },
}
# Базы, которые меняются прямо во время правки книги: их компоненты Финансы
# пересчитывают у себя, остальные приходят готовой суммой. Книга у баера своя
# на каждый тир, поэтому у каждой книги своя база; общая осталась от правил,
# заведённых до разделения.
BOOK_BASE = "finance_profit"
TIER_BASES = {"T1": "finance_profit_t1", "T23": "finance_profit_t23"}


def book_bases(tier: str | None) -> set[str]:
    """Базы, которые считает сама книга этого тира.

    Тир неизвестен — считаем обе: так вела себя единственная база до
    разделения, и правило без тира не должно молча терять компоненты.
    """
    if tier in TIER_BASES:
        return {BOOK_BASE, TIER_BASES[tier]}
    return {BOOK_BASE, *TIER_BASES.values()}
KINDS = {"percent", "fixed", "grid", "deduction"}
MODES = {"replace", "add"}
SCOPES = {"role", "user"}
# Чем уже привязка, тем она сильнее: именное правило перекрывает ролевое.
SCOPE_WEIGHT = {"role": 1, "user": 2}


def month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def decimal_or_none(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return None


def normalize_tiers(raw: object) -> list[dict]:
    """Уровни сетки: сумма-потолок и процент, по возрастанию потолка.

    Последний уровень может быть без потолка — это «и выше». Пустой потолок
    посередине списка бессмыслен, поэтому такие уровни отбрасываются: иначе
    сетка молча перестала бы работать выше него.
    """
    if not isinstance(raw, list):
        return []
    tiers: list[dict] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        percent = decimal_or_none(item.get("percent"))
        if percent is None:
            continue
        up_to = decimal_or_none(item.get("up_to"))
        tiers.append({"up_to": None if up_to is None else str(up_to), "percent": str(percent)})
    capped = [tier for tier in tiers if tier["up_to"] is not None]
    open_ended = [tier for tier in tiers if tier["up_to"] is None]
    capped.sort(key=lambda tier: Decimal(tier["up_to"]))
    # Открытый уровень всегда один и всегда последний.
    return capped + open_ended[:1]


async def subordinates(db: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Ветка подчинённых без самого человека — это и есть «команда».

    Свою книгу тимлид отрабатывает по своей же ставке (сетка от «Профит
    (Финансы)»), и если она попадёт ещё и в базу команды, процент за команду
    начислится на тот же профит второй раз: в «Шкале зарплаты» подсвечена одна
    ступень, а зарплата выходит по другой.
    """
    return await descendants(db, user_id) - {user_id}


async def descendants(db: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Сам человек и вся его ветка подчинённых."""
    links = list(
        (await db.execute(select(UserParent.user_id, UserParent.parent_id))).all()
    )
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for child, parent in links:
        children.setdefault(parent, []).append(child)
    seen = {user_id}
    queue = [user_id]
    while queue:
        current = queue.pop()
        for child in children.get(current, []):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen


async def _workspace_profit_parts(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    year: int,
    month: int,
) -> dict[uuid.UUID, list[dict]]:
    """Один свежий снимок книг для всех зарплатных баз месяца.

    В расчёт компании входят и книги неактивных сотрудников. Сам список
    получателей зарплаты по-прежнему ограничен активными сотрудниками.
    """
    buyer_ids = set(
        await db.scalars(select(User.id).where(User.workspace_id == workspace_id))
    )
    return await finance_books.profits(db, workspace_id, buyer_ids, year, month)


async def base_values(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    year: int,
    month: int,
    *,
    profit_parts: dict[uuid.UUID, list[dict]] | None = None,
) -> dict[str, Decimal]:
    """Обе базы одного человека за месяц — одним расчётом на всё правило.

    Рядом с суммой едут части: у баера две книги, Tier1 и Tier2/3, и процент
    со ступенью считаются по каждой отдельно. По сумме ступень получилась бы
    выше — это была бы уже другая зарплата, чем показывают сами Финансы.
    """
    if profit_parts is None:
        profit_parts = await _workspace_profit_parts(db, workspace_id, year, month)
    team = await subordinates(db, user_id)
    own = profit_parts.get(user_id, [])
    team_parts = [
        part
        for buyer_id, parts in profit_parts.items()
        if buyer_id in team
        for part in parts
    ]
    company_parts = [part for parts in profit_parts.values() for part in parts]
    # Свои книги по тирам: у Tier1 и Tier2/3 обычно разные проценты, и правило
    # должно уметь посчитать каждую отдельно. «Общая» зарплата — сумма двух.
    own_t1 = [part for part in own if part["tier"] == "T1"]
    own_t23 = [part for part in own if part["tier"] != "T1"]
    return {
        "finance_profit": q(sum((part["profit"] for part in own), ZERO)),
        "finance_profit_parts": own,
        "finance_profit_t1": q(sum((part["profit"] for part in own_t1), ZERO)),
        "finance_profit_t1_parts": own_t1,
        "finance_profit_t23": q(sum((part["profit"] for part in own_t23), ZERO)),
        "finance_profit_t23_parts": own_t23,
        "team_profit": q(sum((part["profit"] for part in team_parts), ZERO)),
        "team_profit_parts": team_parts,
        "company_profit": q(sum((part["profit"] for part in company_parts), ZERO)),
        "company_profit_parts": company_parts,
    }


def rule_applies(rule: SalaryRule, user: User, first: date, last: date) -> bool:
    if rule.status != Status.active:
        return False
    if rule.scope == "user" and rule.user_id != user.id:
        return False
    if rule.scope == "role" and rule.role_id != user.role_id:
        return False
    # Правило действует на месяц, если его срок пересекается с этим месяцем.
    if rule.valid_from and rule.valid_from > last:
        return False
    if rule.valid_to and rule.valid_to < first:
        return False
    return True


def effective_rules(rules: list[SalaryRule]) -> list[SalaryRule]:
    """Что реально применяется, с учётом «заменить» и «дополнить».

    Идём от общего к частному. Правило в режиме «заменить» отбрасывает всё, что
    успели собрать более общие правила: именно так выглядит «у этого человека
    отдельная договорённость, остальное не считать».
    """
    ordered = sorted(
        rules,
        key=lambda rule: (
            SCOPE_WEIGHT.get(rule.scope, 0),
            rule.position,
            rule.created_at,
        ),
    )
    result: list[SalaryRule] = []
    for rule in ordered:
        if rule.mode == "replace":
            result = [rule]
        else:
            result.append(rule)
    return result


def evaluate_component(
    component: SalaryComponent,
    values: dict[str, Decimal],
    by_tier: dict[str | None, Decimal] | None = None,
) -> tuple[Decimal, str]:
    """Сумма компонента и человеческое объяснение, откуда она взялась.

    `by_tier` наполняется той частью начисления, которая привязана к книге
    конкретного тира. Фикс и вычеты к тиру не относятся — они остаются вне
    разбивки, поэтому сумма по тирам меньше фонда, а не равна ему.
    """
    if by_tier is None:
        by_tier = {}
    if component.kind == "fixed":
        amount = component.amount or ZERO
        return q(amount), "фиксированная сумма"

    if component.kind == "deduction":
        if component.base:
            base_value = values.get(component.base, ZERO)
            percent = component.percent or ZERO
            amount = q(base_value * percent / Decimal("100"))
            label = BASES.get(component.base, {}).get("label", component.base)
            return -amount, f"{percent}% от «{label}» ({base_value})"
        return -q(component.amount or ZERO), "фиксированный вычет"

    base_value = values.get(component.base or "", ZERO)
    label = BASES.get(component.base or "", {}).get("label", component.base or "—")
    # Профит приходит частями — по книге на каждый тир. Процент и сетка
    # применяются к каждой части, потому что так же считают и сами Финансы.
    parts = values.get(f"{component.base}_parts")
    if not parts:
        parts = [{"tier": None, "profit": base_value}]

    if component.kind == "grid":
        tiers = normalize_tiers(component.tiers)
        amount = ZERO
        rates = []
        for part in parts:
            if part["profit"] <= ZERO:
                continue
            percent = grid_percent(tiers, part["profit"])
            share = q(part["profit"] * percent / Decimal("100"))
            amount += share
            by_tier[part["tier"]] = by_tier.get(part["tier"], ZERO) + share
            rates.append(f"{percent}% от {part['profit']}")
        if not rates:
            return ZERO, f"«{label}» = {base_value}, сетка не применяется"
        return q(amount), "сетка: " + ", ".join(rates) + f" · «{label}»"

    percent = component.percent or ZERO
    positive = [part for part in parts if part["profit"] > ZERO]
    if not positive:
        return ZERO, f"«{label}» = {base_value}, процент не начисляется"
    amount = ZERO
    for part in positive:
        share = q(part["profit"] * percent / Decimal("100"))
        amount += share
        by_tier[part["tier"]] = by_tier.get(part["tier"], ZERO) + share
    total = sum((part["profit"] for part in positive), ZERO)
    return q(amount), f"{percent}% от «{label}» ({total})"


def step_label(previous: Decimal | None, up_to: Decimal | None) -> str:
    if up_to is None:
        return f"выше {previous:,.0f}".replace(",", " ") if previous is not None else "от профита"
    if previous is None:
        return f"до {up_to:,.0f}".replace(",", " ")
    return f"{previous + 1:,.0f} – {up_to:,.0f}".replace(",", " ")


def plan_steps(tiers: list[dict]) -> list[dict]:
    """Ступени для блока «Шкала зарплаты» в Финансах.

    Нулевая ступень добавляется явно: сетка на убыточном месяце не начисляет
    ничего, и шкала должна показывать это, а не начинаться сразу с процента.
    """
    steps = [{"percent": "0", "up_to": "0", "label": "профит ≤ 0"}]
    previous: Decimal | None = None
    for tier in tiers:
        up_to = None if tier["up_to"] is None else Decimal(tier["up_to"])
        steps.append(
            {
                "percent": tier["percent"],
                "up_to": None if up_to is None else str(up_to),
                "label": step_label(previous, up_to),
            }
        )
        previous = up_to
    return steps


def build_plan(
    rules: list[SalaryRule],
    values: dict[str, Decimal],
    bases: set[str] | None = None,
) -> dict | None:
    """Правило в виде, который Финансы применяют к профиту книги.

    Части, которые зависят от профита книги, уезжают как есть — они
    пересчитываются на каждое нажатие клавиши. Всё остальное (фикс, вычеты,
    проценты от профита команды) считается здесь один раз: за этот месяц оно
    уже не изменится от правки самой книги.
    """
    if not rules:
        return None
    if bases is None:
        bases = book_bases(None)
    parts: list[dict] = []
    flat = ZERO
    steps: list[dict] | None = None
    for rule in rules:
        for component in rule.components:
            # Компонент чужого тира к этой книге не относится вовсе: посчитать
            # его здесь суммой значило бы начислить зарплату второй книги ещё
            # и в этой.
            if component.base in TIER_BASES.values() and component.base not in bases:
                continue
            on_book = component.base in bases
            if component.kind == "grid" and on_book:
                tiers = normalize_tiers(component.tiers)
                parts.append({"kind": "grid", "tiers": tiers})
                if steps is None:
                    steps = plan_steps(tiers)
                continue
            if component.kind == "percent" and on_book:
                parts.append({"kind": "percent", "percent": str(component.percent or ZERO)})
                continue
            if component.kind == "deduction" and on_book:
                parts.append(
                    {"kind": "percent", "percent": str(-(component.percent or ZERO))}
                )
                continue
            value, _ = evaluate_component(component, values)
            flat += value
    if flat:
        parts.append({"kind": "flat", "amount": str(q(flat))})
    if steps is None:
        # Сетки от профита книги нет — шкала показывает одну ставку: сумму
        # процентных частей. Пустой блок был бы хуже: пользователь решил бы,
        # что правило не подхватилось.
        percent = sum(
            (Decimal(part["percent"]) for part in parts if part["kind"] == "percent"),
            ZERO,
        )
        steps = [
            {"percent": "0", "up_to": "0", "label": "профит ≤ 0"},
            {"percent": str(percent), "up_to": None, "label": "от профита"},
        ]
    return {
        "rule": " → ".join(rule.name for rule in rules),
        "parts": parts,
        "steps": steps,
    }


async def payroll(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    year: int,
    month: int,
) -> dict:
    """Начисления месяца для API зарплаты и финансовых сводок."""
    first, last = month_range(year, month)
    rules = list(
        (
            await db.execute(
                select(SalaryRule)
                .where(SalaryRule.workspace_id == workspace_id)
                .order_by(SalaryRule.position, SalaryRule.created_at)
            )
        ).scalars()
    )
    people = list(
        (
            await db.execute(
                select(User)
                .where(
                    User.workspace_id == workspace_id,
                    User.status == Status.active,
                )
                .order_by(User.name, User.login)
            )
        ).scalars()
    )

    rows = []
    total = ZERO
    profit_parts = None
    for person in people:
        chosen = effective_rules(
            [rule for rule in rules if rule_applies(rule, person, first, last)]
        )
        if not chosen:
            continue
        if profit_parts is None:
            profit_parts = await _workspace_profit_parts(db, workspace_id, year, month)
        values = await base_values(
            db, workspace_id, person.id, year, month, profit_parts=profit_parts
        )
        lines = []
        amount = ZERO
        by_tier: dict[str | None, Decimal] = {}
        for rule in chosen:
            for component in rule.components:
                value, explanation = evaluate_component(component, values, by_tier)
                amount += value
                lines.append(
                    {
                        "rule": rule.name,
                        "kind": component.kind,
                        "amount": value,
                        "explanation": explanation,
                    }
                )
        # Минус месяца переносится финансовым долгом, а не счётом сотруднику.
        payout = amount if amount > ZERO else ZERO
        total += payout
        rows.append(
            {
                "user_id": str(person.id),
                "user_name": person.name or person.login,
                "rules": [rule.name for rule in chosen],
                "lines": lines,
                "accrued": amount,
                "payout": payout,
                # Часть начисления, привязанная к книге тира. Остальное — фикс,
                # вычеты и проценты от профита команды — к тиру не относится.
                "by_tier": {
                    tier: q(value)
                    for tier, value in by_tier.items()
                    if tier is not None
                },
                "bases": {
                    key: value
                    for key, value in values.items()
                    if not key.endswith("_parts")
                },
            }
        )
    return {
        "year": year,
        "month": month,
        "rows": rows,
        "total": total,
        "people": len(rows),
    }


async def plan_for_book(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer: User,
    year: int,
    month: int,
    tier: str | None = None,
) -> dict | None:
    """Правило зарплаты этого баера за этот месяц; None — правил нет.

    None означает «считать прежней лестницей», а не «зарплаты нет»: пока
    правила не завели, Финансы работают как раньше.
    """
    first, last = month_range(year, month)
    rules = list(
        (
            await db.execute(
                select(SalaryRule)
                .where(
                    SalaryRule.workspace_id == workspace_id,
                    SalaryRule.status == Status.active,
                )
                .order_by(SalaryRule.position, SalaryRule.created_at)
            )
        ).scalars()
    )
    chosen = effective_rules(
        [rule for rule in rules if rule_applies(rule, buyer, first, last)]
    )
    if not chosen:
        return None
    needs = {
        component.base
        for rule in chosen
        for component in rule.components
        if component.base
    }
    values = {
        "finance_profit": ZERO,
        "finance_profit_t1": ZERO,
        "finance_profit_t23": ZERO,
        "team_profit": ZERO,
        "company_profit": ZERO,
    }
    profit_parts = None
    if "company_profit" in needs:
        profit_parts = await _workspace_profit_parts(db, workspace_id, year, month)
        company_parts = [part for parts in profit_parts.values() for part in parts]
        values["company_profit"] = q(
            sum((part["profit"] for part in company_parts), ZERO)
        )
        values["company_profit_parts"] = company_parts
    if "team_profit" in needs:
        team = await subordinates(db, buyer.id)
        by_buyer = profit_parts
        if by_buyer is None:
            by_buyer = await finance_books.profits(db, workspace_id, team, year, month)
        # Профит приходит частями — по книге на каждый тир, — поэтому суммируем
        # поле, а не сами объекты. Разбивка едет дальше: от неё считаются
        # процент и ступень, как и в самих Финансах.
        parts = [
            part
            for buyer_id, books in by_buyer.items()
            if buyer_id in team
            for part in books
        ]
        values["team_profit"] = q(sum((part["profit"] for part in parts), ZERO))
        values["team_profit_parts"] = parts
    return build_plan(chosen, values, book_bases(tier))
