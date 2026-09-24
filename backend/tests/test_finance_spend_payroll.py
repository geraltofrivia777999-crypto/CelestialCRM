"""Salary consumers share one fresh spend snapshot without changing scope."""

from collections import Counter
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.database import SessionLocal
from app.models import MediaRecord, Offer, SalaryComponent, SalaryRule, Status, User, UserParent
from app.services import finance_spend, salary
from tests.test_salary import _book, _drop_books, payroll  # noqa: F401

YEAR, MONTH = 2038, 1


@pytest.fixture
async def spend_payroll(payroll):  # noqa: F811
    async with SessionLocal() as db:
        junior = User(
            workspace_id=payroll["workspace"], role_id=payroll["buyer_role"],
            login="spend-salary-junior", name="Spend salary junior", password_hash="unused",
        )
        inactive = User(
            workspace_id=payroll["workspace"], role_id=payroll["buyer_role"],
            login="spend-salary-inactive", name="Spend salary inactive", password_hash="unused",
            status=Status.inactive,
        )
        offer = Offer(workspace_id=payroll["workspace"], name="Salary spend source", geo="US")
        db.add_all([junior, inactive, offer])
        await db.flush()
        db.add(UserParent(user_id=junior.id, parent_id=payroll["buyer"]))
        for buyer_id, profit in ((payroll["buyer"], 20000), (junior.id, 10000), (inactive.id, 5000)):
            await _book(db, payroll["workspace"], buyer_id, profit, year=YEAR, month=MONTH)
        records = [
            MediaRecord(
                workspace_id=payroll["workspace"], buyer_id=buyer_id, offer_id=offer.id,
                record_date=date(YEAR, MONTH, 10), spend_calculated=spend,
            )
            for buyer_id, spend in ((junior.id, Decimal("100")), (inactive.id, Decimal("50")))
        ]
        db.add_all(records)
        db.add_all([
            SalaryRule(
                workspace_id=payroll["workspace"], name="Buyer percent", scope="role",
                role_id=payroll["buyer_role"],
                components=[SalaryComponent(kind="percent", base="finance_profit", percent=5)],
            ),
            SalaryRule(
                workspace_id=payroll["workspace"], name="Lead mixed bases", scope="user",
                user_id=payroll["buyer"], mode="replace",
                components=[
                    SalaryComponent(kind="percent", base="finance_profit", percent=5, position=0),
                    SalaryComponent(kind="percent", base="team_profit", percent=1, position=1),
                    SalaryComponent(kind="percent", base="company_profit", percent=1, position=2),
                ],
            ),
        ])
        await db.commit()
        ids = {**payroll, "junior": junior.id, "inactive": inactive.id,
               "offer": offer.id, "junior_record": records[0].id}
    yield ids
    async with SessionLocal() as db:
        await db.execute(delete(MediaRecord).where(MediaRecord.offer_id == ids["offer"]))
        await db.execute(delete(Offer).where(Offer.id == ids["offer"]))
        await _drop_books(db, ids["workspace"])
        await db.execute(delete(UserParent).where(UserParent.user_id == ids["junior"]))
        await db.execute(delete(User).where(User.id.in_([ids["junior"], ids["inactive"]])))
        await db.commit()


@pytest.mark.parametrize("reader", ["payroll", "base_values", "plan_for_book"])
async def test_salary_readers_refresh_each_buyer_once_and_preserve_scopes(spend_payroll, monkeypatch, reader):
    case = spend_payroll
    refreshes = Counter()
    original = finance_spend.pull_spend_to_books

    async def counted(db, workspace_id, buyer_id, year, month):
        refreshes[(buyer_id, year, month)] += 1
        return await original(db, workspace_id, buyer_id, year, month)

    monkeypatch.setattr(finance_spend, "pull_spend_to_books", counted)
    async with SessionLocal() as db:
        if reader == "payroll":
            result = await salary.payroll(db, case["workspace"], YEAR, MONTH)
            rows = {row["user_id"]: row for row in result["rows"]}
            bases = rows[str(case["buyer"])]["bases"]
            assert rows[str(case["junior"])]["bases"]["finance_profit"] == Decimal("9900")
            assert str(case["inactive"]) not in rows
        elif reader == "base_values":
            bases = await salary.base_values(db, case["workspace"], case["buyer"], YEAR, MONTH)
        else:
            buyer = await db.get(User, case["buyer"])
            plan = await salary.plan_for_book(db, case["workspace"], buyer, YEAR, MONTH)
            assert [part["kind"] for part in plan["parts"]] == ["percent", "flat"]
            assert Decimal(plan["parts"][0]["percent"]) == Decimal("5")
            # 1% команды (только книга джуниора, 9900) плюс 1% всей компании (34850).
            assert Decimal(plan["parts"][1]["amount"]) == Decimal("447.50")
            bases = None
        if bases is not None:
            assert bases["finance_profit"] == Decimal("20000")
            # Своя книга тимлида (20000) в базу команды не входит.
            assert bases["team_profit"] == Decimal("9900")
            assert bases["company_profit"] == Decimal("34850")
        assert refreshes[(case["buyer"], YEAR, MONTH)] == 1
        assert refreshes[(case["junior"], YEAR, MONTH)] == 1
        assert refreshes[(case["inactive"], YEAR, MONTH)] == 1
        assert set(refreshes.values()) == {1}

        # A new calculation in the same session must see later source edits.
        record = await db.get(MediaRecord, case["junior_record"])
        record.spend_calculated = Decimal("200")
        await db.flush()
        fresh = await salary.payroll(db, case["workspace"], YEAR, MONTH)
        lead = next(row for row in fresh["rows"] if row["user_id"] == str(case["buyer"]))
        assert lead["bases"]["team_profit"] == Decimal("9800")
        assert lead["bases"]["company_profit"] == Decimal("34750")
        assert set(refreshes.values()) == {2}


async def test_book_spend_is_written_in_whole_dollars(database) -> None:
    """Спенд книги — целые доллары.

    В ячейку шириной в три цифры «1259.9876» не помещается и обрезается на
    глазах, а копейки рекламных кабинетов на решения финансиста не влияют.
    Округляем по обычным правилам: 0.5 вверх.
    """
    import uuid as uuid_module
    from decimal import Decimal

    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import FinanceBook, FinanceBookDay, MediaRecord, User
    from app.services import finance_spend
    from tests.test_media_finance import _fixture_ids

    # Оффер и баер берём той же фикстурой, что и остальные тесты аналитики:
    # в чистой базе своего оффера ещё нет.
    buyer_id, offer_id = await _fixture_ids()

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        record = MediaRecord(
            workspace_id=admin.workspace_id,
            record_date=date(2026, 5, 4),
            buyer_id=uuid_module.UUID(buyer_id),
            offer_id=uuid_module.UUID(offer_id),
            spend_override=Decimal("1259.9876"),
        )
        db.add(record)
        await db.commit()
        await finance_spend.refresh_for_record(
            db, admin.workspace_id, uuid_module.UUID(buyer_id), date(2026, 5, 4)
        )
        await db.commit()

        book = await db.scalar(
            select(FinanceBook).where(
                FinanceBook.workspace_id == admin.workspace_id,
                FinanceBook.buyer_id == uuid_module.UUID(buyer_id),
                FinanceBook.year == 2026,
                FinanceBook.month == 5,
            )
        )
        assert book is not None
        row = await db.scalar(
            select(FinanceBookDay).where(
                FinanceBookDay.book_id == book.id, FinanceBookDay.day == 4
            )
        )
        assert row is not None
        assert row.media_spend == Decimal("1260")
        assert row.spend_buyer == Decimal("1260")
