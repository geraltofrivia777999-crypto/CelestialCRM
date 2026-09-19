"""Правила расчёта зарплаты — «Настройки → Расчет ЗП»."""

from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
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


async def _book(db, workspace_id, buyer_id, profit: int, *, year=2026, month=8) -> None:
    """Книга Финансов с заданным профитом: доход `profit`, без расходов.

    Правила считают от того же числа, которое финансист видит в Финансах,
    поэтому фикстура заводит именно книгу, а не запись аналитики.
    """
    book = FinanceBook(
        workspace_id=workspace_id,
        buyer_id=buyer_id,
        year=year,
        month=month,
        prev_minus=Decimal("0"),
        eur_usd_rate=Decimal("1"),
    )
    db.add(book)
    await db.flush()
    offer = FinanceBookOffer(
        book_id=book.id, position=0, name="Оффер", rate=Decimal(profit), rate_currency="USD"
    )
    db.add(offer)
    await db.flush()
    tag = FinanceOfferTag(offer_id=offer.id, position=0, name="SOK")
    db.add(tag)
    await db.flush()
    db.add(FinanceTagDay(tag_id=tag.id, day=10, deposits=Decimal("1")))


async def _drop_books(db, workspace_id) -> None:
    books = select(FinanceBook.id).where(FinanceBook.workspace_id == workspace_id)
    offers = select(FinanceBookOffer.id).where(FinanceBookOffer.book_id.in_(books))
    tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(offers))
    await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(tags)))
    await db.execute(delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(offers)))
    await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(books)))
    await db.execute(delete(FinanceBookDay).where(FinanceBookDay.book_id.in_(books)))
    await db.execute(delete(FinanceBook).where(FinanceBook.workspace_id == workspace_id))


@pytest.fixture
async def payroll(database):
    """Чистый набор правил и один баер с книгой на 20 000 за август 2026."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        buyer = User(
            workspace_id=admin.workspace_id,
            role_id=buyer_role.id,
            name="Баер Пётр",
            login="salarybuyer",
            password_hash=hash_password("salary-password"),
        )
        db.add(buyer)
        await db.flush()
        await _book(db, admin.workspace_id, buyer.id, 20000)
        await db.commit()
        ids = {
            "workspace": admin.workspace_id,
            "admin": admin.id,
            "buyer": buyer.id,
            "buyer_role": buyer_role.id,
        }

    yield ids

    async with SessionLocal() as db:
        await db.execute(delete(SalaryComponent))
        await db.execute(
            delete(SalaryRule).where(SalaryRule.workspace_id == ids["workspace"])
        )
        await _drop_books(db, ids["workspace"])
        await db.execute(delete(Session).where(Session.user_id == ids["buyer"]))
        await db.execute(delete(UserParent).where(UserParent.parent_id == ids["buyer"]))
        await db.execute(delete(UserParent).where(UserParent.user_id == ids["buyer"]))
        await db.execute(
            delete(User).where(
                User.workspace_id == ids["workspace"],
                User.login.in_(["salarybuyer", "salaryjunior"]),
            )
        )
        await db.commit()


async def _junior(payroll, profit: int) -> None:
    """Подчинённый с книгой: без него база «профит команды» пуста.

    Своя книга тимлида в эту базу не входит, поэтому команду в тестах надо
    заводить явно — иначе компонент от команды считал бы ноль и проверял бы
    только то, что он не падает.
    """
    async with SessionLocal() as db:
        junior = User(
            workspace_id=payroll["workspace"],
            role_id=payroll["buyer_role"],
            name="Джуниор",
            login="salaryjunior",
            password_hash=hash_password("salary-password"),
        )
        db.add(junior)
        await db.flush()
        db.add(UserParent(user_id=junior.id, parent_id=payroll["buyer"]))
        await _book(db, payroll["workspace"], junior.id, profit)
        await db.commit()


def _percent_rule(payroll, **overrides) -> dict:
    payload = {
        "name": "Баер — 5% от профита",
        "scope": "role",
        "role_id": str(payroll["buyer_role"]),
        "components": [
            {"kind": "percent", "base": "finance_profit", "percent": 5}
        ],
    }
    payload.update(overrides)
    return payload


async def test_a_rule_is_created_with_its_components(payroll) -> None:
    with _admin_client() as client:
        response = client.post("/api/v1/salary/rules", json=_percent_rule(payroll))
    assert response.status_code == 201
    rule = response.json()
    assert rule["scope"] == "role"
    assert rule["role_name"] == "Buyer"
    assert rule["components"][0]["base_label"] == "Профит (Финансы)"
    assert "periodicity" not in rule["components"][0]
    assert "subtract_penalties" not in rule


async def test_a_rule_without_components_is_refused(payroll) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/salary/rules", json=_percent_rule(payroll, components=[])
        )
    assert response.status_code == 422


async def test_a_percent_component_needs_a_base(payroll) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll, components=[{"kind": "percent", "percent": 5}]
            ),
        )
    assert response.status_code == 422


async def test_an_unknown_base_is_refused(payroll) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                components=[{"kind": "percent", "base": "выдумка", "percent": 5}],
            ),
        )
    assert response.status_code == 422


async def test_a_role_rule_needs_a_role(payroll) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/salary/rules", json=_percent_rule(payroll, role_id=None)
        )
    assert response.status_code == 422


async def test_dates_the_wrong_way_round_are_refused(payroll) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll, valid_from="2026-09-01", valid_to="2026-08-01"
            ),
        )
    assert response.status_code == 422


async def test_a_percent_is_taken_from_the_finance_profit(payroll) -> None:
    """Профит книги за август — 20000; 5 % = 1000."""
    with _admin_client() as client:
        client.post("/api/v1/salary/rules", json=_percent_rule(payroll))
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    assert Decimal(str(row["bases"]["finance_profit"])) == Decimal("20000")
    assert Decimal(str(row["payout"])) == Decimal("1000")


async def test_finance_salary_fund_matches_the_salary_calculation(payroll) -> None:
    with _admin_client() as client:
        client.post("/api/v1/salary/rules", json=_percent_rule(payroll))
        calculated = client.get(
            "/api/v1/salary/calculate?year=2026&month=8"
        ).json()
        summary = client.get(
            "/api/v1/finance/summary?year=2026&month=8&scope=all"
        ).json()

    assert Decimal(summary["salary"]["total"]) == Decimal(calculated["total"])
    assert sum(
        Decimal(group["amount"]) for group in summary["salary"]["groups"]
    ) == Decimal(calculated["total"])


async def test_a_grid_takes_the_rate_of_the_whole_amount(payroll) -> None:
    """20000 попадает в уровень «до 20000» → 20 %, а не по частям."""
    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                name="Баер — сетка",
                components=[
                    {
                        "kind": "grid",
                        "base": "finance_profit",
                        "tiers": [
                            {"up_to": 5000, "percent": 10},
                            {"up_to": 20000, "percent": 20},
                            {"up_to": None, "percent": 30},
                        ],
                    }
                ],
            ),
        )
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    assert Decimal(str(row["payout"])) == Decimal("4000")


async def test_several_components_are_added_together(payroll) -> None:
    """Процент от профита + сетка по профиту команды + оклад − вычет."""
    await _junior(payroll, 20000)
    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                name="Баер — составное правило",
                components=[
                    {"kind": "percent", "base": "finance_profit", "percent": 5},
                    {
                        "kind": "grid",
                        "base": "team_profit",
                        "tiers": [
                            {"up_to": 10000, "percent": 10},
                            {"up_to": None, "percent": 20},
                        ],
                    },
                    {"kind": "fixed", "amount": 500},
                    {"kind": "deduction", "amount": 200},
                ],
            ),
        )
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    # 1000 (5 % от своих 20000) + 4000 (20 % от книги подчинённого 20000)
    # + 500 − 200
    assert Decimal(str(row["payout"])) == Decimal("5300")
    assert len(row["lines"]) == 4


async def test_a_percentage_deduction_uses_its_own_base(payroll) -> None:
    await _junior(payroll, 20000)
    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                components=[
                    {"kind": "fixed", "amount": 1000},
                    {"kind": "deduction", "base": "team_profit", "percent": 2},
                ],
            ),
        )
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    # 1000 − 2 % от профита команды (книга подчинённого, 20000)
    assert Decimal(str(row["payout"])) == Decimal("600")


async def test_a_named_rule_replaces_the_rule_of_the_role(payroll) -> None:
    with _admin_client() as client:
        client.post("/api/v1/salary/rules", json=_percent_rule(payroll))
        client.post(
            "/api/v1/salary/rules",
            json={
                "name": "Пётр — особая договорённость",
                "scope": "user",
                "user_id": str(payroll["buyer"]),
                "mode": "replace",
                "components": [{"kind": "fixed", "amount": 3000}],
            },
        )
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    assert row["rules"] == ["Пётр — особая договорённость"]
    assert Decimal(str(row["payout"])) == Decimal("3000")


async def test_an_added_rule_keeps_the_rule_of_the_role(payroll) -> None:
    with _admin_client() as client:
        client.post("/api/v1/salary/rules", json=_percent_rule(payroll))
        client.post(
            "/api/v1/salary/rules",
            json={
                "name": "Пётр — доплата за ночные",
                "scope": "user",
                "user_id": str(payroll["buyer"]),
                "mode": "add",
                "components": [{"kind": "fixed", "amount": 300}],
            },
        )
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    assert len(row["rules"]) == 2
    assert Decimal(str(row["payout"])) == Decimal("1300")


async def test_a_rule_outside_its_dates_does_not_apply(payroll) -> None:
    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(payroll, valid_from="2026-09-01"),
        )
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()
    assert result["rows"] == []


async def test_a_switched_off_rule_does_not_apply(payroll) -> None:
    with _admin_client() as client:
        client.post("/api/v1/salary/rules", json=_percent_rule(payroll, status="inactive"))
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()
    assert result["rows"] == []


async def test_a_negative_result_is_not_paid_out(payroll) -> None:
    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                components=[
                    {"kind": "fixed", "amount": 100},
                    {"kind": "deduction", "amount": 900},
                ],
            ),
        )
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    assert Decimal(str(row["accrued"])) == Decimal("-800")
    assert Decimal(str(row["payout"])) == Decimal("0")


async def test_grid_tiers_are_sorted_and_the_open_one_goes_last(payroll) -> None:
    with _admin_client() as client:
        rule = client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                components=[
                    {
                        "kind": "grid",
                        "base": "finance_profit",
                        "tiers": [
                            {"up_to": None, "percent": 30},
                            {"up_to": 20000, "percent": 20},
                            {"up_to": 5000, "percent": 10},
                        ],
                    }
                ],
            ),
        ).json()

    tiers = rule["components"][0]["tiers"]
    assert [tier["up_to"] for tier in tiers] == ["5000", "20000", None]


async def test_a_rule_is_replaced_whole_on_update(payroll) -> None:
    with _admin_client() as client:
        rule = client.post("/api/v1/salary/rules", json=_percent_rule(payroll)).json()
        updated = client.patch(
            f"/api/v1/salary/rules/{rule['id']}",
            json=_percent_rule(
                payroll,
                name="Баер — новая формула",
                components=[{"kind": "fixed", "amount": 700}],
            ),
        ).json()
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    assert updated["name"] == "Баер — новая формула"
    assert [component["kind"] for component in updated["components"]] == ["fixed"]
    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    assert Decimal(str(row["payout"])) == Decimal("700")


async def test_the_reference_lists_bases_roles_and_people(payroll) -> None:
    with _admin_client() as client:
        reference = client.get("/api/v1/salary/bases").json()

    codes = [item["code"] for item in reference["bases"]]
    # Считать зарплату можно только от того, что видно в Финансах. Порядок
    # осмысленный: свои книги по тирам → своя ветка → вся компания. Общая
    # база своей книги скрыта — она осталась только для прежних правил.
    assert codes == [
        "finance_profit_t1",
        "finance_profit_t23",
        "team_profit",
        "company_profit",
    ]
    assert [scope["code"] for scope in reference["scopes"]] == ["role", "user"]
    assert "periodicities" not in reference
    assert any(role["name"] == "Buyer" for role in reference["roles"])
    assert any(person["id"] == str(payroll["buyer"]) for person in reference["users"])


async def test_a_buyer_cannot_read_salary_rules(payroll) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"login": "salarybuyer", "password": "salary-password"},
        )
        rules = client.get("/api/v1/salary/rules")
        calculate = client.get("/api/v1/salary/calculate")
    assert rules.status_code == 403
    assert calculate.status_code == 403


async def test_an_incomplete_component_explains_what_is_missing(payroll) -> None:
    """Сообщение должно быть читаемым: его показывают прямо в форме правила."""
    with _admin_client() as client:
        response = client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll, components=[{"kind": "deduction"}]
            ),
        )
    assert response.status_code == 422
    fields = response.json()["error"]["details"]["fields"]
    assert any("Укажите сумму вычета" in field["message"] for field in fields)


async def test_the_binding_to_everyone_is_gone(payroll) -> None:
    """«Все сотрудники» убрано: правило всегда чьё-то."""
    with _admin_client() as client:
        response = client.post(
            "/api/v1/salary/rules", json=_percent_rule(payroll, scope="all")
        )
    assert response.status_code == 422


async def test_the_team_profit_adds_up_the_books_of_the_subordinates(payroll) -> None:
    async with SessionLocal() as db:
        junior = User(
            workspace_id=payroll["workspace"],
            role_id=payroll["buyer_role"],
            name="Джуниор",
            login="salaryjunior",
            password_hash=hash_password("salary-password"),
        )
        db.add(junior)
        await db.flush()
        db.add(UserParent(user_id=junior.id, parent_id=payroll["buyer"]))
        await _book(db, payroll["workspace"], junior.id, 5000)
        await db.commit()

    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                name="Тимлид — 10% от команды",
                components=[{"kind": "percent", "base": "team_profit", "percent": 10}],
            ),
        )
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    lead = [row for row in result["rows"] if row["user_id"] == str(payroll["buyer"])][0]
    # Только книга подчинённого: своя книга тимлида (20000) в базу команды не
    # входит — за неё он получает по своей ставке, и попади она сюда, тот же
    # профит оплатился бы дважды.
    assert Decimal(str(lead["bases"]["team_profit"])) == Decimal("5000")
    assert Decimal(str(lead["bases"]["finance_profit"])) == Decimal("20000")
    assert Decimal(str(lead["payout"])) == Decimal("500")


async def test_the_finance_ladder_follows_the_rule(payroll) -> None:
    """Правило из Настроек — та же шкала, что видит финансист в Финансах."""
    with _admin_client() as client:
        default = client.get(
            f"/api/v1/finance/book?buyer_id={payroll['buyer']}&year=2026&month=8"
        ).json()
        # Пока правил нет, работает прежняя лестница: 20000 → 20 %.
        assert default["salary_plan"] is None
        assert Decimal(default["totals"]["total"]["salary_percent"]) == Decimal("20")
        assert Decimal(default["totals"]["total"]["salary"]) == Decimal("4000")

        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                name="Баер — сетка 12/25",
                components=[
                    {
                        "kind": "grid",
                        "base": "finance_profit",
                        "tiers": [
                            {"up_to": 10000, "percent": 12},
                            {"up_to": None, "percent": 25},
                        ],
                    }
                ],
            ),
        )
        ruled = client.get(
            f"/api/v1/finance/book?buyer_id={payroll['buyer']}&year=2026&month=8"
        ).json()

    plan = ruled["salary_plan"]
    assert plan["rule"] == "Баер — сетка 12/25"
    assert [step["percent"] for step in plan["steps"]] == ["0", "12", "25"]
    assert [step["label"] for step in plan["steps"]] == [
        "профит ≤ 0", "до 10 000", "выше 10 000"
    ]
    assert Decimal(ruled["totals"]["total"]["salary_percent"]) == Decimal("25")
    assert Decimal(ruled["totals"]["total"]["salary"]) == Decimal("5000")


async def test_the_finance_ladder_adds_the_fixed_part(payroll) -> None:
    """Фикс и вычеты приезжают одной суммой: за месяц они уже не изменятся."""
    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                name="Баер — 5% плюс оклад",
                components=[
                    {"kind": "percent", "base": "finance_profit", "percent": 5},
                    {"kind": "fixed", "amount": 700},
                    {"kind": "deduction", "amount": 200},
                ],
            ),
        )
        book = client.get(
            f"/api/v1/finance/book?buyer_id={payroll['buyer']}&year=2026&month=8"
        ).json()

    plan = book["salary_plan"]
    assert [part["kind"] for part in plan["parts"]] == ["percent", "flat"]
    assert Decimal(plan["parts"][1]["amount"]) == Decimal("500")
    # 5 % от 20000 плюс 700 − 200.
    assert Decimal(book["totals"]["total"]["salary"]) == Decimal("1500")
    assert Decimal(book["totals"]["total"]["salary_percent"]) == Decimal("5")


async def test_a_rule_of_another_role_leaves_the_default_ladder(payroll) -> None:
    async with SessionLocal() as db:
        other = await db.scalar(
            select(Role).where(
                Role.workspace_id == payroll["workspace"], Role.name != "Buyer"
            )
        )
        other_id = str(other.id)

    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(payroll, name="Чужая роль", role_id=other_id),
        )
        book = client.get(
            f"/api/v1/finance/book?buyer_id={payroll['buyer']}&year=2026&month=8"
        ).json()

    assert book["salary_plan"] is None
    assert Decimal(book["totals"]["total"]["salary"]) == Decimal("4000")


async def test_a_team_rule_does_not_break_the_book_of_its_lead(payroll) -> None:
    """Открыть книгу тимлида, у которого правило считает процент от команды.

    Профит приходит частями — по книге на каждый тир, — и один участок кода
    складывал сами объекты вместо их сумм. Книга отвечала 500, а на экране
    это выглядело как «Ошибка запроса» без единой подсказки, где искать.
    """
    async with SessionLocal() as db:
        junior = User(
            workspace_id=payroll["workspace"],
            role_id=payroll["buyer_role"],
            name="Джуниор",
            login="salaryjunior",
            password_hash=hash_password("salary-password"),
        )
        db.add(junior)
        await db.flush()
        db.add(UserParent(user_id=junior.id, parent_id=payroll["buyer"]))
        await _book(db, payroll["workspace"], junior.id, 5000)
        await db.commit()

    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                name="Тимлид — команда плюс своя сетка",
                components=[
                    {"kind": "percent", "base": "team_profit", "percent": 5},
                    {
                        "kind": "grid",
                        "base": "finance_profit",
                        "tiers": [
                            {"up_to": "10000", "percent": "10"},
                            {"up_to": None, "percent": "20"},
                        ],
                    },
                ],
            ),
        )
        response = client.get(
            f"/api/v1/finance/book?buyer_id={payroll['buyer']}&year=2026&month=8&tier=T1"
        )

    assert response.status_code == 200
    plan = response.json()["salary_plan"]
    # Процент от команды посчитан на сервере и приехал одной суммой: 5% от
    # книги подчинённого (5000). Своя книга тимлида сюда не входит — за неё
    # платит сетка ниже, и в шкале зарплаты подсвечена именно её ступень.
    flat = [part for part in plan["parts"] if part["kind"] == "flat"][0]
    assert Decimal(flat["amount"]) == Decimal("250")
    # Сетка от профита книги осталась частью, которую клиент считает сам.
    assert any(part["kind"] == "grid" for part in plan["parts"])


async def test_the_company_profit_covers_every_book(payroll) -> None:
    """База CMO — весь профит компании, а не своя книга и не одна ветка."""
    async with SessionLocal() as db:
        stranger = User(
            workspace_id=payroll["workspace"],
            role_id=payroll["buyer_role"],
            name="Баер чужой ветки",
            login="salaryjunior",
            password_hash=hash_password("salary-password"),
        )
        db.add(stranger)
        await db.flush()
        # Подчинения нет: в «профит команды» этот баер не попадёт.
        await _book(db, payroll["workspace"], stranger.id, 5000)
        await db.commit()

    with _admin_client() as client:
        client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                name="CMO — 1% от всего профита",
                components=[{"kind": "percent", "base": "company_profit", "percent": 1}],
            ),
        )
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()

    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    # Своя книга 20000 плюс чужая 5000 — обе входят в компанию.
    assert Decimal(str(row["bases"]["company_profit"])) == Decimal("25000")
    # Подчинённых нет, и своя книга в команду не входит: база пуста.
    assert Decimal(str(row["bases"]["team_profit"])) == Decimal("0")
    assert Decimal(str(row["payout"])) == Decimal("250")


async def _tier_book(payroll, tier: str, profit: int) -> None:
    """Ещё одна книга того же баера — другого тира."""
    async with SessionLocal() as db:
        book = FinanceBook(
            workspace_id=payroll["workspace"],
            buyer_id=payroll["buyer"],
            year=2026,
            month=8,
            tier=tier,
            prev_minus=Decimal("0"),
            eur_usd_rate=Decimal("1"),
        )
        db.add(book)
        await db.flush()
        offer = FinanceBookOffer(
            book_id=book.id, position=0, name="Оффер", rate=Decimal(profit),
            rate_currency="USD",
        )
        db.add(offer)
        await db.flush()
        tag = FinanceOfferTag(offer_id=offer.id, position=0, name="SOK")
        db.add(tag)
        await db.flush()
        db.add(FinanceTagDay(tag_id=tag.id, day=10, deposits=Decimal("1")))
        await db.commit()


async def test_tier_bases_count_only_their_own_book(payroll) -> None:
    """T1 и T2/3 — разные базы, и процент у каждой свой.

    Книга Tier1 даёт 20000 (фикстура), Tier2/3 — 10000. Правило берёт 5 % с
    первой и 10 % со второй: 1000 + 1000. Общая зарплата — сумма двух баз, и
    ни одна из них не должна прихватывать чужой тир.
    """
    await _tier_book(payroll, "T23", 10000)
    with _admin_client() as client:
        created = client.post(
            "/api/v1/salary/rules",
            json=_percent_rule(
                payroll,
                components=[
                    {"kind": "percent", "base": "finance_profit_t1", "percent": 5},
                    {"kind": "percent", "base": "finance_profit_t23", "percent": 10},
                ],
            ),
        )
        assert created.status_code == 201, created.text
        labels = [item["base_label"] for item in created.json()["components"]]
        assert labels == ["Профит T1 (Финансы)", "Профит T2/3 (Финансы)"]
        result = client.get("/api/v1/salary/calculate?year=2026&month=8").json()
        t1 = client.get(
            f"/api/v1/finance/book?buyer_id={payroll['buyer']}"
            "&year=2026&month=8&tier=T1"
        ).json()
        t23 = client.get(
            f"/api/v1/finance/book?buyer_id={payroll['buyer']}"
            "&year=2026&month=8&tier=T23"
        ).json()
        overview = client.get(
            f"/api/v1/finance/buyer-overview?buyer_id={payroll['buyer']}"
            "&year=2026&month=8"
        ).json()

    row = [item for item in result["rows"] if item["user_id"] == str(payroll["buyer"])][0]
    assert Decimal(str(row["bases"]["finance_profit_t1"])) == Decimal("20000")
    assert Decimal(str(row["bases"]["finance_profit_t23"])) == Decimal("10000")
    assert Decimal(str(row["payout"])) == Decimal("2000")
    book_salaries = {
        "T1": Decimal(t1["totals"]["total"]["salary"]),
        "T23": Decimal(t23["totals"]["total"]["salary"]),
    }
    overview_salaries = {
        item["tier"]: Decimal(item["amount"])
        for item in overview["salary"]["tiers"]
    }
    # Сводка не должна возвращаться к старой лестнице: она обязана сложить
    # ровно те суммы, которые баер видит в двух своих таблицах.
    assert overview_salaries["T1"] == book_salaries["T1"] == Decimal("1000")
    assert overview_salaries["T23"] == book_salaries["T23"] == Decimal("1000")
    assert overview_salaries["total"] == sum(book_salaries.values(), Decimal("0"))
    assert Decimal(overview["salary"]["total"]) == overview_salaries["total"]


async def test_the_old_common_profit_base_is_hidden_from_the_picker(payroll) -> None:
    """Прежние правила считаются, но выбрать общую базу заново уже нельзя."""
    with _admin_client() as client:
        bases = client.get("/api/v1/salary/bases").json()["bases"]
    codes = [base["code"] for base in bases]
    assert "finance_profit_t1" in codes
    assert "finance_profit_t23" in codes
    assert "finance_profit" not in codes
