"""Наполнение базы демо-данными для нагрузочной проверки интерфейса.

Запуск (внутри контейнера api):

    python -m app.demo_seed

Объёмы настраиваются переменными окружения DEMO_*, см. константы ниже.
Скрипт идемпотентен по ключам уникальности: повторный запуск дополняет
данные, а не дублирует их. Чтобы удалить всё созданное, запустите с
DEMO_RESET=1 — будут удалены только записи с демо-префиксами.
"""

import asyncio
import calendar
import os
import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, or_, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import encrypt_secret, hash_password
from app.models import (
    AuditEvent,
    FinanceBook,
    FinanceBookDay,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceRecord,
    FinanceServiceValue,
    FinanceSpendValue,
    FinanceTagDay,
    IntegrationConnection,
    MediaRecord,
    MediaServiceValue,
    MediaSpendValue,
    Offer,
    OfferBuyer,
    OfferStatus,
    Partner,
    Role,
    SalaryComponent,
    SalaryRule,
    Service,
    SpendProvider,
    Status,
    User,
    UserParent,
    Workspace,
)
from app.services.formulas import (
    amount_with_commission,
    finance_import_key,
    service_cost,
)

DEMO_PREFIX = "demo"

BUYERS = int(os.getenv("DEMO_BUYERS", "24"))
LEADS = int(os.getenv("DEMO_LEADS", "4"))
PARTNERS = int(os.getenv("DEMO_PARTNERS", "12"))
OFFERS = int(os.getenv("DEMO_OFFERS", "90"))
DAYS = int(os.getenv("DEMO_DAYS", "180"))
OFFERS_PER_BUYER = int(os.getenv("DEMO_OFFERS_PER_BUYER", "3"))
AUDIT_EVENTS = int(os.getenv("DEMO_AUDIT_EVENTS", "4000"))
BATCH = int(os.getenv("DEMO_BATCH", "2000"))
RESET = os.getenv("DEMO_RESET") == "1"
FINANCE_ONLY = os.getenv("DEMO_FINANCE_ONLY") == "1"

TEAM_NAMES = ["Север", "Юг", "Восток", "Запад"]

GEOS = [
    "BR", "MX", "IN", "ID", "PH", "VN", "TR", "EG", "NG", "ZA",
    "PL", "DE", "IT", "ES", "KZ", "UZ", "AZ", "PE", "CO", "CL",
]
VERTICALS = ["Casino", "Betting", "Crypto", "Dating", "Nutra", "Gambling", "Sweeps"]
WORKFLOW_STATUSES = [
    OfferStatus.working,
    OfferStatus.working,
    OfferStatus.working,
    OfferStatus.active,
    OfferStatus.hold,
    OfferStatus.working,
    OfferStatus.stop,
]
SOURCES = ["Facebook", "Google", "TikTok", "UAC", "PWA", "Push"]
FIRST = [
    "Ivan", "Petr", "Anna", "Marat", "Dina", "Oleg", "Sergey", "Alina",
    "Timur", "Kirill", "Nurlan", "Aigerim", "Roman", "Daniil", "Elena",
    "Artem", "Vlad", "Yerlan", "Karina", "Maxim", "Damir", "Sofia",
    "Ruslan", "Alexey", "Madina", "Nikita", "Aidos", "Polina",
]
LAST = [
    "Orlov", "Sidorov", "Kim", "Nurpeisov", "Volkov", "Grishin", "Belov",
    "Zaitsev", "Ismailov", "Tokayev", "Popov", "Egorov", "Serik", "Bekov",
]


def money(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


async def flush_batch(db, rows: list, force: bool = False) -> None:
    if rows and (force or len(rows) >= BATCH):
        db.add_all(rows)
        await db.flush()
        rows.clear()


async def clear_demo_finance(db, workspace_id, user_ids: list) -> None:
    """Удаляет книги демо-пользователей и только помеченные демо-правила ЗП."""
    if user_ids:
        books = select(FinanceBook.id).where(
            FinanceBook.workspace_id == workspace_id,
            FinanceBook.buyer_id.in_(user_ids),
        )
        offers = select(FinanceBookOffer.id).where(
            FinanceBookOffer.book_id.in_(books)
        )
        tags = select(FinanceOfferTag.id).where(
            FinanceOfferTag.offer_id.in_(offers)
        )
        await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(tags)))
        await db.execute(delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(offers)))
        await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(books)))
        await db.execute(delete(FinanceBookDay).where(FinanceBookDay.book_id.in_(books)))
        await db.execute(delete(FinanceBook).where(FinanceBook.id.in_(books)))

    rules = select(SalaryRule.id).where(
        SalaryRule.workspace_id == workspace_id,
        or_(
            SalaryRule.name.like("Demo · %"),
            SalaryRule.user_id.in_(user_ids or [None]),
        ),
    )
    await db.execute(delete(SalaryComponent).where(SalaryComponent.rule_id.in_(rules)))
    await db.execute(delete(SalaryRule).where(SalaryRule.id.in_(rules)))


async def add_finance_offer(
    db,
    *,
    book: FinanceBook,
    position: int,
    name: str,
    partner: str,
    tier: str | None,
    rate: Decimal,
    currency: str,
    day_count: int,
    spend: Decimal,
    costs: Decimal,
    deposits: int,
    offset: int,
    day_totals: dict[int, dict[str, Decimal]],
) -> int:
    """Создаёт оффер с двумя тегами и депозитами по дням.

    Затраты оффер не хранит — они вводятся на день книги целиком, поэтому свою
    долю расхода оффер докладывает в `day_totals`, а строку дня пишет вызывающий.
    """
    offer = FinanceBookOffer(
        book_id=book.id,
        position=position,
        name=name,
        partner=partner,
        tier=tier,
        rate=rate,
        rate_currency=currency,
    )
    db.add(offer)
    await db.flush()

    sok = FinanceOfferTag(offer_id=offer.id, position=0, name="SOK")
    late = FinanceOfferTag(offer_id=offer.id, position=1, name="Долёты")
    db.add_all([sok, late])
    await db.flush()

    tag_days = 0
    for day in range(1, day_count + 1):
        # Пропуски делают график и таблицу похожими на живой месяц.
        if (day + offset + position) % 7 == 0:
            continue
        entry = day_totals.setdefault(day, {"spend": Decimal("0"), "costs": Decimal("0")})
        entry["spend"] += spend + Decimal((day + offset) % 9)
        entry["costs"] += costs + Decimal(day % 3)

        db.add(
            FinanceTagDay(
                tag_id=sok.id,
                day=day,
                deposits=Decimal(deposits + ((day + offset) % 3)),
            )
        )
        tag_days += 1
        if day % 4 == 0:
            db.add(FinanceTagDay(tag_id=late.id, day=day, deposits=Decimal("1")))
            tag_days += 1
    return tag_days


async def seed_finance_demo(
    db,
    *,
    workspace_id,
    buyers: list[User],
    leads: list[User],
    cmo: User,
    creator: User,
    buyer_role: Role,
    lead_role: Role,
    cmo_role: Role,
) -> dict[str, int]:
    """Пересоздаёт согласованный набор книг и правил для нового раздела."""
    demo_people = [*buyers, *leads, cmo]
    await clear_demo_finance(db, workspace_id, [person.id for person in demo_people])

    today = date.today()
    current_year, current_month = today.year, today.month
    if current_month == 1:
        previous_year, previous_month = current_year - 1, 12
    else:
        previous_year, previous_month = current_year, current_month - 1
    current_days = min(today.day, calendar.monthrange(current_year, current_month)[1])

    # Один закрытый убыточный месяц нужен для проверки автоматического переноса
    # долга в следующую книгу.
    debt_owner = buyers[0]
    previous_book = FinanceBook(
        workspace_id=workspace_id,
        buyer_id=debt_owner.id,
        year=previous_year,
        month=previous_month,
        prev_minus=Decimal("0"),
        eur_usd_rate=Decimal("1.09"),
    )
    db.add(previous_book)
    await db.flush()
    previous_offer = FinanceBookOffer(
        book_id=previous_book.id,
        position=0,
        name="Demo · Долг прошлого месяца",
        partner="Demo Legacy Partners",
        tier="T1",
        rate=Decimal("20"),
        rate_currency="USD",
    )
    db.add(previous_offer)
    await db.flush()
    previous_tag = FinanceOfferTag(offer_id=previous_offer.id, position=0, name="SOK")
    db.add(previous_tag)
    await db.flush()
    db.add_all(
        [
            FinanceBookDay(
                book_id=previous_book.id,
                day=1,
                spend_buyer=Decimal("2400"),
                spend_agent=Decimal("0"),
                costs=Decimal("0"),
            ),
            FinanceTagDay(tag_id=previous_tag.id, day=1, deposits=Decimal("10")),
        ]
    )

    book_people = [*buyers, *leads]
    offer_days = 1
    tag_days = 1
    offer_count = 1
    for index, person in enumerate(book_people):
        book = FinanceBook(
            workspace_id=workspace_id,
            buyer_id=person.id,
            year=current_year,
            month=current_month,
            prev_minus=Decimal("2200") if person.id == debt_owner.id else Decimal("0"),
            eur_usd_rate=Decimal("1.09"),
        )
        db.add(book)
        await db.flush()

        # Затраты книги собираются со всех офферов и пишутся одной строкой на
        # день: в самой книге они и вводятся так же, одним блоком на день.
        day_totals: dict[int, dict[str, Decimal]] = {}
        for day in range(1, current_days + 1):
            if (day + index) % 5 == 0:
                entry = day_totals.setdefault(
                    day, {"spend": Decimal("0"), "costs": Decimal("0")}
                )
                entry["spend"] += Decimal("8") + Decimal(index % 4)
                entry["costs"] += Decimal("2")

        loss_multiplier = Decimal("1.8") if index % 9 == 0 else Decimal("1")
        created_tags = await add_finance_offer(
            db,
            book=book,
            position=0,
            name=f"Demo · T1 — {person.name}",
            partner="Demo Alpha Network",
            tier="T1",
            rate=Decimal("52") + Decimal(index % 7),
            currency="USD",
            day_count=current_days,
            spend=Decimal("110") * loss_multiplier,
            costs=Decimal("4"),
            deposits=2,
            offset=index,
            day_totals=day_totals,
        )
        offer_count += 1
        tag_days += created_tags

        created_tags = await add_finance_offer(
            db,
            book=book,
            position=1,
            name=f"Demo · T2/3 — {person.name}",
            partner="Demo Euro Partners",
            tier="T23",
            rate=Decimal("36") + Decimal(index % 5),
            currency="EUR" if index % 4 == 0 else "USD",
            day_count=current_days,
            spend=Decimal("70") * loss_multiplier,
            costs=Decimal("3"),
            deposits=1,
            offset=index + 2,
            day_totals=day_totals,
        )
        offer_count += 1
        tag_days += created_tags

        if index % 3 == 0:
            created_tags = await add_finance_offer(
                db,
                book=book,
                position=2,
                name=f"Demo · Без тира — {person.name}",
                partner="Demo Direct",
                tier=None,
                rate=Decimal("28"),
                currency="USD",
                day_count=current_days,
                spend=Decimal("48") * loss_multiplier,
                costs=Decimal("2"),
                deposits=1,
                offset=index + 4,
                day_totals=day_totals,
            )
            offer_count += 1
            tag_days += created_tags

        for day, entry in sorted(day_totals.items()):
            db.add(
                FinanceBookDay(
                    book_id=book.id,
                    day=day,
                    spend_buyer=entry["spend"],
                    spend_agent=Decimal("25") + Decimal(day),
                    costs=entry["costs"],
                )
            )
            offer_days += 1

    rules = [
        SalaryRule(
            workspace_id=workspace_id,
            name="Demo · Баеры — процент, сетка, фикс и вычет",
            status=Status.active,
            mode="replace",
            scope="role",
            role_id=buyer_role.id,
            valid_from=date(current_year, 1, 1),
            position=0,
            created_by_id=creator.id,
            components=[
                SalaryComponent(
                    kind="percent", base="finance_profit", percent=Decimal("5"), position=0
                ),
                SalaryComponent(
                    kind="grid",
                    base="finance_profit",
                    tiers=[
                        {"up_to": "3000", "percent": "2"},
                        {"up_to": "8000", "percent": "4"},
                        {"up_to": None, "percent": "6"},
                    ],
                    position=1,
                ),
                SalaryComponent(kind="fixed", amount=Decimal("350"), position=2),
                SalaryComponent(kind="deduction", amount=Decimal("25"), position=3),
            ],
        ),
        SalaryRule(
            workspace_id=workspace_id,
            name="Demo · Тимлиды — фикс и процент команды",
            status=Status.active,
            mode="replace",
            scope="role",
            role_id=lead_role.id,
            valid_from=date(current_year, 1, 1),
            position=1,
            created_by_id=creator.id,
            components=[
                SalaryComponent(kind="fixed", amount=Decimal("1200"), position=0),
                SalaryComponent(
                    kind="percent", base="team_profit", percent=Decimal("3"), position=1
                ),
            ],
        ),
        SalaryRule(
            workspace_id=workspace_id,
            name="Demo · CMO — фикс и процент компании",
            status=Status.active,
            mode="replace",
            scope="role",
            role_id=cmo_role.id,
            valid_from=date(current_year, 1, 1),
            position=2,
            created_by_id=creator.id,
            components=[
                SalaryComponent(kind="fixed", amount=Decimal("2500"), position=0),
                SalaryComponent(
                    kind="percent", base="team_profit", percent=Decimal("1"), position=1
                ),
            ],
        ),
        SalaryRule(
            workspace_id=workspace_id,
            name="Demo · Персональная надбавка баеру",
            status=Status.active,
            mode="add",
            scope="user",
            user_id=buyers[1].id,
            valid_from=date(current_year, 1, 1),
            position=3,
            created_by_id=creator.id,
            components=[SalaryComponent(kind="fixed", amount=Decimal("150"), position=0)],
        ),
        SalaryRule(
            workspace_id=workspace_id,
            name="Demo · Персональная сетка баера",
            status=Status.active,
            mode="replace",
            scope="user",
            user_id=buyers[2].id,
            valid_from=date(current_year, 1, 1),
            position=4,
            created_by_id=creator.id,
            components=[
                SalaryComponent(
                    kind="grid",
                    base="finance_profit",
                    tiers=[
                        {"up_to": "3000", "percent": "8"},
                        {"up_to": None, "percent": "12"},
                    ],
                    position=0,
                )
            ],
        ),
    ]
    db.add_all(rules)
    await db.flush()
    return {
        "books": len(book_people) + 1,
        "offers": offer_count,
        "offer_days": offer_days,
        "tag_days": tag_days,
        "salary_rules": len(rules),
    }


async def reset_demo(db, workspace_id) -> None:
    """Удаляет только демо-данные (по префиксу логина/имени)."""
    demo_users = (
        await db.execute(
            select(User.id).where(
                User.workspace_id == workspace_id,
                User.login.like(f"{DEMO_PREFIX}%"),
            )
        )
    ).scalars().all()
    await clear_demo_finance(db, workspace_id, demo_users)
    demo_offers = (
        await db.execute(
            select(Offer.id).where(
                Offer.workspace_id == workspace_id,
                Offer.external_id.like(f"{DEMO_PREFIX}-%"),
            )
        )
    ).scalars().all()
    if demo_users or demo_offers:
        media_ids = (
            await db.execute(
                select(MediaRecord.id).where(
                    MediaRecord.workspace_id == workspace_id,
                    MediaRecord.buyer_id.in_(demo_users or [None]),
                )
            )
        ).scalars().all()
        finance_ids = (
            await db.execute(
                select(FinanceRecord.id).where(
                    FinanceRecord.workspace_id == workspace_id,
                    FinanceRecord.buyer_id.in_(demo_users or [None]),
                )
            )
        ).scalars().all()
        for model, ids in (
            (MediaServiceValue, media_ids),
            (MediaSpendValue, media_ids),
        ):
            if ids:
                await db.execute(delete(model).where(model.media_record_id.in_(ids)))
        for model, ids in (
            (FinanceServiceValue, finance_ids),
            (FinanceSpendValue, finance_ids),
        ):
            if ids:
                await db.execute(delete(model).where(model.finance_record_id.in_(ids)))
        if media_ids:
            await db.execute(delete(MediaRecord).where(MediaRecord.id.in_(media_ids)))
        if finance_ids:
            await db.execute(
                delete(FinanceRecord).where(FinanceRecord.id.in_(finance_ids))
            )
        if demo_offers:
            await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id.in_(demo_offers)))
            await db.execute(delete(Offer).where(Offer.id.in_(demo_offers)))
        await db.execute(
            delete(Partner).where(
                Partner.workspace_id == workspace_id,
                Partner.external_id.like(f"{DEMO_PREFIX}-%"),
            )
        )
        if demo_users:
            await db.execute(delete(UserParent).where(UserParent.user_id.in_(demo_users)))
            await db.execute(delete(UserParent).where(UserParent.parent_id.in_(demo_users)))
            await db.execute(
                delete(AuditEvent).where(AuditEvent.user_id.in_(demo_users))
            )
            await db.execute(delete(User).where(User.id.in_(demo_users)))
    await db.commit()
    print("Демо-данные удалены.")


async def main() -> None:
    rnd = random.Random(20260725)
    async with SessionLocal() as db:
        workspace = await db.scalar(select(Workspace).limit(1))
        if workspace is None:
            raise SystemExit("Сначала запустите базовый seed: python -m app.seed")

        if RESET:
            await reset_demo(db, workspace.id)
            return

        roles = {
            role.name: role
            for role in (
                await db.execute(select(Role).where(Role.workspace_id == workspace.id))
            ).scalars()
        }
        buyer_role = roles.get("Buyer") or roles["Administrator"]
        lead_role = roles.get("Team Lead") or buyer_role
        finance_role = roles.get("Finance") or roles["Administrator"]
        cmo_role = roles.get("CMO")
        if cmo_role is None:
            cmo_role = Role(
                workspace_id=workspace.id,
                name="CMO",
                description="Marketing executive access",
                is_system=True,
                permissions=list(lead_role.permissions),
            )
            db.add(cmo_role)
            await db.flush()
            roles["CMO"] = cmo_role

        creator = await db.scalar(
            select(User).where(
                User.workspace_id == workspace.id,
                User.role_id == roles["Administrator"].id,
            )
        )
        if creator is None:
            raise SystemExit("Нет администратора: запустите python -m app.seed")

        services = (
            await db.execute(
                select(Service).where(Service.workspace_id == workspace.id)
            )
        ).scalars().all()
        providers = (
            await db.execute(
                select(SpendProvider).where(SpendProvider.workspace_id == workspace.id)
            )
        ).scalars().all()
        if not services or not providers:
            raise SystemExit("Нет services/spend_providers: запустите python -m app.seed")

        # --- подключение (обязательно для offers/partners) -------------------
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == workspace.id
            )
        )
        if connection is None:
            connection = IntegrationConnection(
                workspace_id=workspace.id,
                name="Demo Keitaro",
                base_url="https://demo-tracker.example.com",
                api_key_encrypted=encrypt_secret("demo-api-key"),
                status=Status.inactive,
            )
            db.add(connection)
            await db.flush()

        # --- пользователи ----------------------------------------------------
        existing_logins = set(
            (
                await db.execute(
                    select(User.login).where(User.workspace_id == workspace.id)
                )
            ).scalars()
        )
        password_hash = hash_password("demo12345")

        finance_user = await db.scalar(
            select(User).where(
                User.workspace_id == workspace.id,
                User.login == f"{DEMO_PREFIX}finance01",
            )
        )
        if finance_user is None:
            finance_user = User(
                workspace_id=workspace.id,
                role_id=finance_role.id,
                name="Demo Finance Manager",
                login=f"{DEMO_PREFIX}finance01",
                password_hash=password_hash,
                status=Status.active,
            )
            db.add(finance_user)
        else:
            finance_user.role_id = finance_role.id
            finance_user.status = Status.active

        cmo = await db.scalar(
            select(User).where(
                User.workspace_id == workspace.id,
                User.login == f"{DEMO_PREFIX}cmo01",
            )
        )
        if cmo is None:
            cmo = User(
                workspace_id=workspace.id,
                role_id=cmo_role.id,
                name="Demo CMO",
                login=f"{DEMO_PREFIX}cmo01",
                password_hash=password_hash,
                status=Status.active,
                team_name="Celestial Performance",
            )
            db.add(cmo)
        else:
            cmo.role_id = cmo_role.id
            cmo.status = Status.active
            cmo.team_name = "Celestial Performance"
        await db.flush()

        leads: list[User] = []
        for i in range(LEADS):
            login = f"{DEMO_PREFIX}lead{i + 1:02d}"
            if login in existing_logins:
                user = await db.scalar(
                    select(User).where(
                        User.workspace_id == workspace.id, User.login == login
                    )
                )
                user.role_id = lead_role.id
                user.status = Status.active
                user.keitaro_company_group = f"Team {i + 1}"
                user.team_name = TEAM_NAMES[i % len(TEAM_NAMES)]
                leads.append(user)
                continue
            user = User(
                workspace_id=workspace.id,
                role_id=lead_role.id,
                name=f"{rnd.choice(FIRST)} {rnd.choice(LAST)}",
                login=login,
                password_hash=password_hash,
                status=Status.active,
                keitaro_company_group=f"Team {i + 1}",
                team_name=TEAM_NAMES[i % len(TEAM_NAMES)],
            )
            db.add(user)
            leads.append(user)
        await db.flush()

        buyers: list[User] = []
        for i in range(BUYERS):
            login = f"{DEMO_PREFIX}buyer{i + 1:03d}"
            if login in existing_logins:
                user = await db.scalar(
                    select(User).where(
                        User.workspace_id == workspace.id, User.login == login
                    )
                )
                user.role_id = buyer_role.id
                user.status = Status.active if i % 9 else Status.inactive
                user.keitaro_company_group = f"Team {i % max(LEADS, 1) + 1}"
                buyers.append(user)
                continue
            status = Status.active if i % 9 else Status.inactive
            user = User(
                workspace_id=workspace.id,
                role_id=buyer_role.id,
                name=f"{rnd.choice(FIRST)} {rnd.choice(LAST)}",
                login=login,
                password_hash=password_hash,
                status=status,
                keitaro_company_group=f"Team {i % max(LEADS, 1) + 1}",
                keitaro_offer_group=rnd.choice(VERTICALS),
            )
            db.add(user)
            buyers.append(user)
        await db.flush()

        existing_parents = set(
            (
                await db.execute(
                    select(UserParent.user_id, UserParent.parent_id)
                )
            ).all()
        )
        for i, buyer in enumerate(buyers):
            if not leads:
                break
            parent = leads[i % len(leads)]
            if (buyer.id, parent.id) not in existing_parents:
                db.add(UserParent(user_id=buyer.id, parent_id=parent.id))
        for lead in leads:
            if (lead.id, cmo.id) not in existing_parents:
                db.add(UserParent(user_id=lead.id, parent_id=cmo.id))
        await db.flush()
        print(
            f"Пользователи: {len(leads)} тимлидов, {len(buyers)} байеров, "
            "Finance и CMO"
        )

        finance_stats = await seed_finance_demo(
            db,
            workspace_id=workspace.id,
            buyers=buyers,
            leads=leads,
            cmo=cmo,
            creator=creator,
            buyer_role=buyer_role,
            lead_role=lead_role,
            cmo_role=cmo_role,
        )
        await db.commit()
        print(
            "Новые Финансы: "
            f"книг {finance_stats['books']}, офферов {finance_stats['offers']}, "
            f"дней офферов {finance_stats['offer_days']}, "
            f"дней тегов {finance_stats['tag_days']}, "
            f"правил ЗП {finance_stats['salary_rules']}"
        )
        if FINANCE_ONLY:
            print("Готово. Пароль всех демо-пользователей: demo12345")
            return

        # --- партнёры --------------------------------------------------------
        existing_partner_ext = set(
            (
                await db.execute(
                    select(Partner.external_id).where(
                        Partner.connection_id == connection.id
                    )
                )
            ).scalars()
        )
        partners: list[Partner] = []
        for i in range(PARTNERS):
            ext = f"{DEMO_PREFIX}-p{i + 1:03d}"
            if ext in existing_partner_ext:
                partners.append(
                    await db.scalar(
                        select(Partner).where(
                            Partner.connection_id == connection.id,
                            Partner.external_id == ext,
                        )
                    )
                )
                continue
            partner = Partner(
                workspace_id=workspace.id,
                connection_id=connection.id,
                external_id=ext,
                name=f"{rnd.choice(VERTICALS)} Affiliates {i + 1:02d}",
                status=Status.active if i % 7 else Status.inactive,
            )
            db.add(partner)
            partners.append(partner)
        await db.flush()

        # --- офферы ----------------------------------------------------------
        existing_offer_ext = set(
            (
                await db.execute(
                    select(Offer.external_id).where(Offer.connection_id == connection.id)
                )
            ).scalars()
        )
        offers: list[Offer] = []
        for i in range(OFFERS):
            ext = f"{DEMO_PREFIX}-o{i + 1:04d}"
            if ext in existing_offer_ext:
                offers.append(
                    await db.scalar(
                        select(Offer).where(
                            Offer.connection_id == connection.id,
                            Offer.external_id == ext,
                        )
                    )
                )
                continue
            geo = GEOS[i % len(GEOS)]
            vertical = VERTICALS[i % len(VERTICALS)]
            offer = Offer(
                workspace_id=workspace.id,
                connection_id=connection.id,
                partner_id=partners[i % len(partners)].id,
                external_id=ext,
                name=f"{vertical} {geo} {rnd.choice(SOURCES)} #{i + 1:03d}",
                geo=geo,
                # Раздел «Оффера» ведёт свои строки вручную; здесь генерируются
                # трекерные офферы для Медиаборда и Финансов, поэтому группа
                # раздаётся так же, как это делает синхронизация.
                group_name=settings.keitaro_offers_group if i % 7 else vertical,
                # Workflow status follows the buyer links assigned below.
                status=OfferStatus.free,
                keitaro_state=Status.active if i % 11 else Status.inactive,
            )
            db.add(offer)
            offers.append(offer)
        await db.flush()
        print(f"Партнёры: {len(partners)}, офферы: {len(offers)}")

        # --- связка оффер ↔ байер -------------------------------------------
        existing_links = set(
            (await db.execute(select(OfferBuyer.offer_id, OfferBuyer.user_id))).all()
        )
        buyer_offers: dict[User, list[Offer]] = {}
        for index, buyer in enumerate(buyers):
            picked = [
                offers[(index * OFFERS_PER_BUYER + shift) % len(offers)]
                for shift in range(OFFERS_PER_BUYER)
            ]
            buyer_offers[buyer] = picked
            for offer in picked:
                if (offer.id, buyer.id) not in existing_links:
                    db.add(OfferBuyer(offer_id=offer.id, user_id=buyer.id))
                    existing_links.add((offer.id, buyer.id))
        # An offer with buyers is no longer "не занят": most go to work, the rest
        # spread over the other statuses so every pill shows up on the page.
        assigned = {offer_id for offer_id, _ in existing_links}
        for index, offer in enumerate(offers):
            if offer.id in assigned:
                offer.status = WORKFLOW_STATUSES[index % len(WORKFLOW_STATUSES)]
        await db.flush()

        # --- медиаборд и финансы --------------------------------------------
        today = date.today()
        start = today - timedelta(days=DAYS - 1)
        existing_media = set(
            (
                await db.execute(
                    select(
                        MediaRecord.record_date,
                        MediaRecord.buyer_id,
                        MediaRecord.offer_id,
                    ).where(MediaRecord.workspace_id == workspace.id)
                )
            ).all()
        )
        existing_finance = set(
            (
                await db.execute(
                    select(FinanceRecord.import_key).where(
                        FinanceRecord.workspace_id == workspace.id
                    )
                )
            ).scalars()
        )

        media_children: list = []
        finance_children: list = []
        media_count = 0
        finance_count = 0

        for day_offset in range(DAYS):
            record_date = start + timedelta(days=day_offset)
            season = 1.0 + 0.25 * ((day_offset % 30) / 30.0)
            weekend = 0.75 if record_date.weekday() >= 5 else 1.0
            day_records: list[MediaRecord] = []
            day_finance: list[FinanceRecord] = []

            for buyer in buyers:
                if buyer.status is not Status.active and rnd.random() < 0.8:
                    continue
                for offer in buyer_offers[buyer]:
                    if rnd.random() < 0.15:
                        continue

                    installs = int(rnd.gauss(1400, 500) * season * weekend)
                    installs = max(installs, 60)
                    registrations = int(installs * rnd.uniform(0.06, 0.22))
                    ftd = int(registrations * rnd.uniform(0.05, 0.3))
                    revenue = money(ftd * rnd.uniform(18, 140))

                    service = services[(day_offset + installs) % len(services)]
                    rent_qty = money(installs)
                    rent = service_cost(
                        rent_qty, service.install_cost, service.commission_pct
                    )

                    provider = providers[(day_offset + ftd) % len(providers)]
                    base_spend = money(installs * rnd.uniform(0.06, 0.35))
                    spend = amount_with_commission(base_spend, provider.commission_pct)

                    if (record_date, buyer.id, offer.id) not in existing_media:
                        record = MediaRecord(
                            workspace_id=workspace.id,
                            record_date=record_date,
                            buyer_id=buyer.id,
                            offer_id=offer.id,
                            installs=installs,
                            registrations=registrations,
                            ftd=ftd,
                            revenue=revenue,
                            spend_calculated=spend,
                            spend_override=(
                                money(float(spend) * rnd.uniform(0.9, 1.15))
                                if rnd.random() < 0.05
                                else None
                            ),
                            source="demo",
                            manual_fields=["installs"] if rnd.random() < 0.08 else [],
                            external_payload={"cost": float(base_spend)},
                        )
                        day_records.append(record)

                    import_key = finance_import_key(
                        record_date, buyer.id, offer.id, f"https://trk.example.com/{offer.external_id}"
                    )
                    if import_key not in existing_finance:
                        existing_finance.add(import_key)
                        day_finance.append(
                            FinanceRecord(
                                workspace_id=workspace.id,
                                record_date=record_date,
                                buyer_id=buyer.id,
                                offer_id=offer.id,
                                link=f"https://trk.example.com/{offer.external_id}",
                                import_key=import_key,
                                rent=rent,
                                spend=spend,
                                qual=money(float(revenue) * rnd.uniform(0.4, 0.9)),
                                revenue=revenue,
                                salary=money(float(revenue) * rnd.uniform(0.02, 0.08)),
                                source="demo",
                            )
                        )

            if day_records:
                db.add_all(day_records)
                await db.flush()
                media_count += len(day_records)
                for record in day_records:
                    service = services[record.installs % len(services)]
                    provider = providers[record.installs % len(providers)]
                    media_children.append(
                        MediaServiceValue(
                            media_record_id=record.id,
                            service_id=service.id,
                            quantity=money(record.installs),
                        )
                    )
                    media_children.append(
                        MediaSpendValue(
                            media_record_id=record.id,
                            provider_id=provider.id,
                            base_amount=money(float(record.spend_calculated) * 0.95),
                        )
                    )
                await flush_batch(db, media_children)

            if day_finance:
                db.add_all(day_finance)
                await db.flush()
                finance_count += len(day_finance)
                for record in day_finance:
                    service = services[int(record.rent) % len(services)]
                    provider = providers[int(record.spend) % len(providers)]
                    finance_children.append(
                        FinanceServiceValue(
                            finance_record_id=record.id,
                            service_id=service.id,
                            quantity=money(float(record.rent) / max(float(service.install_cost), 0.0001)),
                        )
                    )
                    finance_children.append(
                        FinanceSpendValue(
                            finance_record_id=record.id,
                            provider_id=provider.id,
                            base_amount=money(float(record.spend) * 0.95),
                        )
                    )
                await flush_batch(db, finance_children)

            if day_offset % 20 == 0:
                await db.commit()
                print(
                    f"  {record_date}: медиа {media_count}, финансы {finance_count}",
                    flush=True,
                )

        await flush_batch(db, media_children, force=True)
        await flush_batch(db, finance_children, force=True)
        await db.commit()
        print(f"Медиазаписи: +{media_count}, финансовые записи: +{finance_count}")

        # --- аудит -----------------------------------------------------------
        event_types = [
            ("auth.login", "Пользователь вошёл в систему"),
            ("media.values_changed", "Изменены сервисы и расходы"),
            ("finance.import", "Импорт финансовых данных"),
            ("offers.updated", "Обновлён оффер"),
            ("users.updated", "Обновлён пользователь"),
            ("settings.changed", "Изменены настройки"),
        ]
        audit_rows: list[AuditEvent] = []
        actors = buyers + leads + [finance_user, cmo]
        for _ in range(AUDIT_EVENTS):
            event_type, description = rnd.choice(event_types)
            actor = rnd.choice(actors)
            audit_rows.append(
                AuditEvent(
                    workspace_id=workspace.id,
                    user_id=actor.id,
                    event_type=event_type,
                    entity_type="demo",
                    entity_id=str(rnd.randint(1, 9999)),
                    description=description,
                    ip_address=f"10.{rnd.randint(0, 255)}.{rnd.randint(0, 255)}.{rnd.randint(1, 254)}",
                    data={"demo": True},
                )
            )
            await flush_batch(db, audit_rows)
        await flush_batch(db, audit_rows, force=True)
        await db.commit()
        print(f"События аудита: +{AUDIT_EVENTS}")
        print("Готово. Пароль всех демо-пользователей: demo12345")


if __name__ == "__main__":
    asyncio.run(main())
