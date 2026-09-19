from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import (
    FinanceBook,
    FinanceBookDay,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    Role,
    SalaryComponent,
    SalaryRule,
    Session,
    User,
    UserParent,
)
from tests.test_media_finance import _admin_client


async def _book_with_tiers(db, workspace_id, buyer_id, *, foreign: bool = False) -> None:
    """Две таблицы баера за месяц: Tier1 и Tier2/3, каждая со своими числами."""
    specs = (
        [("T1", 999, 1, 0, 0)]
        if foreign
        else [("T1", 10, 10, 30, 6), ("T23", 20, 5, 30, 6)]
    )
    for tier, rate, deposits, spend, costs in specs:
        book = FinanceBook(
            workspace_id=workspace_id,
            buyer_id=buyer_id,
            year=2026,
            month=8,
            tier=tier,
            prev_minus=Decimal("0"),
            eur_usd_rate=Decimal("1"),
        )
        db.add(book)
        await db.flush()
        if spend or costs:
            db.add(
                FinanceBookDay(
                    book_id=book.id,
                    day=1,
                    spend_buyer=Decimal(spend),
                    costs=Decimal(costs),
                )
            )
        offer = FinanceBookOffer(
            book_id=book.id,
            position=0,
            name=f"Оффер {tier}",
            geo="US" if tier == "T1" else "BR",
            rate=Decimal(rate),
            rate_currency="USD",
        )
        db.add(offer)
        await db.flush()
        tag = FinanceOfferTag(offer_id=offer.id, position=0, name="SOK")
        db.add(tag)
        await db.flush()
        db.add(FinanceTagDay(tag_id=tag.id, day=1, deposits=Decimal(deposits)))


@pytest.fixture
async def team_summary(database):
    logins = [
        "finteamlead1",
        "finteambuyer1",
        "finteamfixed1",
        "finteamlead2",
        "finteambuyer2",
    ]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        lead_role = await db.scalar(select(Role).where(Role.name == "Team Lead"))
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        people = [
            User(
                workspace_id=admin.workspace_id,
                role_id=lead_role.id,
                name="Тимлид своей команды",
                login=logins[0],
                password_hash=hash_password("summary-password"),
                team_name="Своя команда",
            ),
            User(
                workspace_id=admin.workspace_id,
                role_id=buyer_role.id,
                name="Баер своей команды",
                login=logins[1],
                password_hash=hash_password("summary-password"),
            ),
            User(
                workspace_id=admin.workspace_id,
                role_id=buyer_role.id,
                name="Баер без книги",
                login=logins[2],
                password_hash=hash_password("summary-password"),
            ),
            User(
                workspace_id=admin.workspace_id,
                role_id=lead_role.id,
                name="Чужой тимлид",
                login=logins[3],
                password_hash=hash_password("summary-password"),
                team_name="Чужая команда",
            ),
            User(
                workspace_id=admin.workspace_id,
                role_id=buyer_role.id,
                name="Чужой баер",
                login=logins[4],
                password_hash=hash_password("summary-password"),
            ),
        ]
        db.add_all(people)
        await db.flush()
        own_lead, own_buyer, own_without_book, foreign_lead, foreign_buyer = people
        db.add_all(
            [
                UserParent(user_id=own_buyer.id, parent_id=own_lead.id),
                UserParent(user_id=own_without_book.id, parent_id=own_lead.id),
                UserParent(user_id=foreign_buyer.id, parent_id=foreign_lead.id),
            ]
        )
        await _book_with_tiers(db, admin.workspace_id, own_buyer.id)
        await _book_with_tiers(
            db, admin.workspace_id, foreign_buyer.id, foreign=True
        )
        payouts = [100, 200, 300, 400, 500]
        rules = []
        for position, (person, payout) in enumerate(zip(people, payouts, strict=True)):
            rules.append(
                SalaryRule(
                    workspace_id=admin.workspace_id,
                    name=f"Сводка команды — {person.name}",
                    scope="user",
                    mode="replace",
                    user_id=person.id,
                    position=position,
                    created_by_id=admin.id,
                    components=[
                        SalaryComponent(
                            kind="fixed", amount=Decimal(payout), position=0
                        )
                    ],
                )
            )
        db.add_all(rules)
        await db.commit()
        result = {
            "own_team": str(own_lead.id),
            "book_owner": str(own_buyer.id),
            "foreign_team": str(foreign_lead.id),
            "own_ids": {str(person.id) for person in people[:3]},
            "without_book": str(own_without_book.id),
            "foreign_ids": {str(person.id) for person in people[3:]},
            "logins": logins,
        }

    yield result

    async with SessionLocal() as db:
        user_ids = select(User.id).where(User.login.in_(logins))
        books = select(FinanceBook.id).where(FinanceBook.buyer_id.in_(user_ids))
        offers = select(FinanceBookOffer.id).where(FinanceBookOffer.book_id.in_(books))
        tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(offers))
        rules = select(SalaryRule.id).where(SalaryRule.user_id.in_(user_ids))
        await db.execute(delete(SalaryComponent).where(SalaryComponent.rule_id.in_(rules)))
        await db.execute(delete(SalaryRule).where(SalaryRule.id.in_(rules)))
        await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(tags)))
        await db.execute(delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(offers)))
        await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(books)))
        await db.execute(delete(FinanceBookDay).where(FinanceBookDay.book_id.in_(books)))
        await db.execute(delete(FinanceBook).where(FinanceBook.id.in_(books)))
        await db.execute(
            delete(UserParent).where(
                UserParent.user_id.in_(user_ids) | UserParent.parent_id.in_(user_ids)
            )
        )
        await db.execute(delete(Session).where(Session.user_id.in_(user_ids)))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


async def test_team_summary_tiers_add_up_to_cards(team_summary) -> None:
    with _admin_client() as client:
        response = client.get(
            "/api/v1/finance/summary?year=2026&month=8&"
            f"scope=team:{team_summary['own_team']}"
        )

    assert response.status_code == 200
    summary = response.json()
    assert [row["tier"] for row in summary["tiers"]] == ["T1", "T23"]
    for key in ("income", "spend", "costs", "profit"):
        assert sum(Decimal(row[key]) for row in summary["tiers"]) == Decimal(
            summary["cards"][key]
        )


async def test_tier_summaries_have_neither_tier_table_nor_salary(team_summary) -> None:
    with _admin_client() as client:
        tier_one = client.get(
            "/api/v1/finance/summary?year=2026&month=8&scope=tier1"
        ).json()
        tier_two_three = client.get(
            "/api/v1/finance/summary?year=2026&month=8&scope=tier23"
        ).json()

    for summary in (tier_one, tier_two_three):
        assert summary["tiers"] == []
        assert summary["salary"] is None


async def test_the_salary_fund_lives_only_in_the_common_summary(team_summary) -> None:
    """В команде фонда нет: зарплату ведёт финансист по всей компании сразу."""
    with _admin_client() as client:
        team = client.get(
            "/api/v1/finance/summary?year=2026&month=8&"
            f"scope=team:{team_summary['own_team']}"
        ).json()
        common = client.get("/api/v1/finance/summary?year=2026&month=8").json()

    assert team["salary"] is None
    salary = common["salary"]
    people_ids = {row["user_id"] for row in salary["people"]}
    assert team_summary["own_ids"] | team_summary["foreign_ids"] <= people_ids
    # Человек без книги за месяц из фонда не выпадает: начисление у него есть.
    assert team_summary["without_book"] in people_ids
    assert Decimal(salary["total"]) == sum(
        (Decimal(row["payout"]) for row in salary["people"]), Decimal("0")
    )


async def test_the_fund_always_shows_every_category(team_summary) -> None:
    """Пустая карточка отвечает на вопрос «а где CMO», ненарисованная — нет."""
    with _admin_client() as client:
        salary = client.get(
            "/api/v1/finance/summary?year=2026&month=8"
        ).json()["salary"]

    names = [group["name"] for group in salary["groups"]]
    assert names[:5] == [
        "Баеры · Tier1",
        "Баеры · Tier2/3",
        "Тимлиды · Tier1",
        "Тимлиды · Tier2/3",
        "CMO",
    ]


async def test_each_tier_reports_the_numbers_it_was_given(team_summary) -> None:
    """Таблицы независимы, поэтому в сводке стоят фактические числа."""
    with _admin_client() as client:
        summary = client.get(
            "/api/v1/finance/summary?year=2026&month=8&"
            f"scope=team:{team_summary['own_team']}"
        ).json()

    rows = {row["tier"]: row for row in summary["tiers"]}
    assert Decimal(rows["T1"]["spend"]) == Decimal("30")
    assert Decimal(rows["T23"]["spend"]) == Decimal("30")
    assert Decimal(rows["T1"]["costs"]) == Decimal("6")
    assert Decimal(rows["T23"]["costs"]) == Decimal("6")
    assert Decimal(rows["T1"]["income"]) == Decimal("100")
    assert Decimal(rows["T23"]["income"]) == Decimal("100")


async def test_team_lead_cannot_open_another_team(team_summary) -> None:
    """Чужая команда отвечает 404: по ответу нельзя понять, что она вообще есть."""
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login",
            json={"login": team_summary["logins"][0], "password": "summary-password"},
        ).status_code == 200
        own = client.get(
            "/api/v1/finance/summary?year=2026&month=8&"
            f"scope=team:{team_summary['own_team']}"
        )
        foreign = client.get(
            "/api/v1/finance/summary?year=2026&month=8&"
            f"scope=team:{team_summary['foreign_team']}"
        )

    assert own.status_code == 200
    assert foreign.status_code == 404


async def test_the_fund_splits_by_tier_and_still_adds_up(team_summary) -> None:
    """Три числа в таблице обязаны складываться в четвёртое.

    По тирам раскладывается только то, что посчиталось от профита книги;
    фиксы и вычеты к тиру не относятся и уходят в «Вне тиров».
    """
    with _admin_client() as client:
        salary = client.get(
            "/api/v1/finance/summary?year=2026&month=8"
        ).json()["salary"]

    rows = {row["tier"]: Decimal(row["amount"]) for row in salary["tiers"]}
    assert rows["total"] == Decimal(salary["total"])
    parts = sum(
        (value for tier, value in rows.items() if tier != "total"), Decimal("0")
    )
    assert parts == rows["total"]
    # Правила в фикстуре — фиксированные суммы, поэтому тиру ничего не досталось.
    assert rows["T1"] == Decimal("0")
    assert rows["T23"] == Decimal("0")
    assert rows["other"] == rows["total"]


async def test_buyer_overview_has_days_tiers_and_salary(team_summary) -> None:
    """Общая сводка баера отдаёт всё, что рисует экран: дни, тиры и ЗП."""
    with _admin_client() as client:
        overview = client.get(
            "/api/v1/finance/buyer-overview?buyer_id="
            f"{team_summary['book_owner']}&year=2026&month=8"
        ).json()

    assert len(overview["daily"]) == 31
    assert [row["tier"] for row in overview["tiers"]] == ["T1", "T23"]
    salary = overview["salary"]
    rows = {row["tier"]: Decimal(row["amount"]) for row in salary["tiers"]}
    # Зарплата каждой таблицы своя, а «ЗП итого» — их сумма.
    assert rows["T1"] + rows["T23"] == rows["total"]
    assert Decimal(salary["total"]) == rows["total"]


@pytest.mark.parametrize("role_name", ["CMO", "СМО"])
async def test_the_cmo_card_finds_its_role_in_any_spelling(database, role_name) -> None:
    """Карточка «ЗП СМО» должна находить человека и по русскому названию роли.

    Роль — свободный текст, и рабочее пространство называет её как удобно.
    Раньше совпадение искалось только с латинским «cmo», а у такого человека
    в подчинении вся компания — поэтому его зарплата уходила в колонку
    тимлидов, а карточка «ЗП СМО» оставалась с нулём.
    """
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        lead_role = await db.scalar(select(Role).where(Role.name == "Team Lead"))
        # Латинская роль в рабочем пространстве уже есть — заводим только ту,
        # которой не хватает, и убираем за собой тоже только её.
        role = await db.scalar(
            select(Role).where(
                Role.workspace_id == admin.workspace_id, Role.name == role_name
            )
        )
        created_role = role is None
        if created_role:
            role = Role(workspace_id=admin.workspace_id, name=role_name)
            db.add(role)
            await db.flush()
        chief = User(
            workspace_id=admin.workspace_id,
            role_id=role.id,
            name="Директор по маркетингу",
            login="fincmo1",
            password_hash=hash_password("summary-password"),
        )
        lead = User(
            workspace_id=admin.workspace_id,
            role_id=lead_role.id,
            name="Тимлид под СМО",
            login="fincmolead1",
            password_hash=hash_password("summary-password"),
        )
        db.add_all([chief, lead])
        await db.flush()
        # Вся компания в подчинении — по этому признаку человека и записывали
        # в тимлиды, когда название роли не узнавалось.
        db.add(UserParent(user_id=lead.id, parent_id=chief.id))
        db.add(
            SalaryRule(
                workspace_id=admin.workspace_id,
                name="СМО — оклад",
                scope="user",
                mode="replace",
                user_id=chief.id,
                created_by_id=admin.id,
                components=[
                    SalaryComponent(kind="fixed", amount=Decimal("2500"), position=0)
                ],
            )
        )
        await db.commit()
        chief_id = str(chief.id)
        workspace_id = admin.workspace_id

    try:
        with _admin_client() as client:
            salary = client.get(
                "/api/v1/finance/summary?year=2026&month=8"
            ).json()["salary"]
    finally:
        async with SessionLocal() as db:
            user_ids = select(User.id).where(User.login.in_(["fincmo1", "fincmolead1"]))
            rules = select(SalaryRule.id).where(SalaryRule.user_id.in_(user_ids))
            await db.execute(
                delete(SalaryComponent).where(SalaryComponent.rule_id.in_(rules))
            )
            await db.execute(delete(SalaryRule).where(SalaryRule.id.in_(rules)))
            await db.execute(delete(UserParent).where(UserParent.parent_id.in_(user_ids)))
            await db.execute(delete(Session).where(Session.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            if created_role:
                await db.execute(
                    delete(Role).where(
                        Role.name == role_name, Role.workspace_id == workspace_id
                    )
                )
            await db.commit()

    chief_row = next(row for row in salary["people"] if row["user_id"] == chief_id)
    assert chief_row["category"] == "cmo"
    groups = {(row["category"], row["tier"]): Decimal(row["amount"]) for row in salary["groups"]}
    assert groups[("cmo", "all")] >= Decimal("2500")
