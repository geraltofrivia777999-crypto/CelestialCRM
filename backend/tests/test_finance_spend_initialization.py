"""Automatic finance books retain month templates and the original opening debt."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models import (
    FinanceBook,
    FinanceBookDay,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    MediaRecord,
)
from app.services.finance_books import load, totals
from app.services.finance_spend import pull_spend_to_books
from tests.test_finance_book import buyer_id  # noqa: F401
from tests.test_finance_spend import DAY, MONTH, YEAR, spend_case  # noqa: F401


@pytest.fixture
async def initialization_case(spend_case):  # noqa: F811
    yield spend_case
    async with SessionLocal() as db:
        # The base fixture deletes its original record only; the earlier-month
        # regression also creates a second source record for the same offer.
        await db.execute(delete(MediaRecord).where(MediaRecord.offer_id == spend_case["offer"]))
        await db.commit()


async def _book(db, case, month=MONTH, tier="T1"):
    return await db.scalar(select(FinanceBook).where(
        FinanceBook.workspace_id == case["workspace"],
        FinanceBook.buyer_id == case["buyer"],
        FinanceBook.year == YEAR,
        FinanceBook.month == month,
        FinanceBook.tier == tier,
    ))


async def _pull(db, case, month=MONTH):
    return await pull_spend_to_books(db, case["workspace"], case["buyer"], YEAR, month)


async def test_auto_month_inherits_offer_template_rate_and_carry_without_old_values(
    initialization_case,
):
    case = initialization_case
    async with SessionLocal() as db:
        previous = FinanceBook(
            workspace_id=case["workspace"], buyer_id=case["buyer"],
            year=YEAR, month=MONTH - 1, tier="T1",
            eur_usd_rate=Decimal("1.25"), prev_minus=Decimal("20"),
        )
        db.add(previous)
        await db.flush()
        previous_offer = FinanceBookOffer(
            book_id=previous.id, source_offer_id=case["offer"], position=3,
            name="Custom buyer offer", partner="Partner", geo="US",
            rate=Decimal("3"), rate_currency="EUR",
        )
        db.add(previous_offer)
        db.add(FinanceBookDay(
            book_id=previous.id, day=1, spend_buyer=Decimal("11"),
            manual_spend=Decimal("11"), spend_agent=Decimal("12"), costs=Decimal("3"),
        ))
        await db.flush()
        tags = [
            FinanceOfferTag(offer_id=previous_offer.id, position=2, name="SOK"),
            FinanceOfferTag(offer_id=previous_offer.id, position=4, name="Долёты"),
        ]
        db.add_all(tags)
        await db.flush()
        db.add_all([
            FinanceTagDay(tag_id=tags[0].id, day=1, deposits=Decimal("2")),
            FinanceTagDay(tag_id=tags[1].id, day=1, deposits=Decimal("4")),
        ])
        await db.flush()

        await _pull(db, case)
        book = await _book(db, case)
        payload = await load(db, book)
        assert book.eur_usd_rate == Decimal("1.25")
        # Previous income 6 * EUR3 * 1.25 = 22.50; spend11 + costs3,
        # opening20 leaves 11.50 to carry into the automatic month.
        assert book.prev_minus == Decimal("11.50")
        assert len(payload["offers"]) == 1
        copied = payload["offers"][0]
        assert copied["id"] != str(previous_offer.id)
        assert copied["source_offer_id"] == str(case["offer"])
        assert (copied["name"], copied["partner"], copied["geo"]) == (
            "Custom buyer offer", "Partner", "US",
        )
        assert copied["rate"] == Decimal("3")
        assert copied["rate_currency"] == "EUR"
        assert copied["tags"] == [
            {"name": "SOK", "values": {}}, {"name": "Долёты", "values": {}},
        ]
        assert set(payload["days"]) == {DAY}
        assert payload["days"][DAY]["spend_buyer"] == Decimal("100")
        assert payload["days"][DAY]["manual_spend"] is None
        assert payload["days"][DAY]["spend_agent"] == Decimal("0")
        assert payload["days"][DAY]["costs"] == Decimal("0")
        assert totals(payload, 30)["total"]["income"] == Decimal("0")

        assert (await _pull(db, case))["written"] == 0
        assert (await load(db, book))["offers"] == payload["offers"]
        assert (await load(db, previous))["offers"][0]["tags"][0]["values"][1] == Decimal("2")


async def test_backfilling_earlier_books_preserves_opening_balance_exactly_once(
    initialization_case,
):
    case = initialization_case
    async with SessionLocal() as db:
        following = FinanceBook(
            workspace_id=case["workspace"], buyer_id=case["buyer"],
            year=YEAR, month=MONTH + 1, tier="T1", prev_minus=Decimal("75"),
        )
        db.add(following)
        await db.flush()
        await _pull(db, case)
        current = await _book(db, case)
        assert current.prev_minus == Decimal("75")
        assert following.prev_minus == Decimal("175")

        earlier_source = MediaRecord(
            workspace_id=case["workspace"], buyer_id=case["buyer"], offer_id=case["offer"],
            record_date=date(YEAR, MONTH - 1, DAY), source="manual",
            spend_calculated=Decimal("50"),
        )
        db.add(earlier_source)
        await db.flush()
        await _pull(db, case, MONTH - 1)
        earlier = await _book(db, case, MONTH - 1)
        assert earlier.prev_minus == Decimal("75")
        assert current.prev_minus == Decimal("125")
        assert following.prev_minus == Decimal("225")
        for month in (MONTH - 1, MONTH, MONTH + 1):
            assert (await _pull(db, case, month))["written"] == 0
        assert following.prev_minus == Decimal("225")

        earlier_source.spend_calculated = Decimal("0")
        await _pull(db, case, MONTH - 1)
        assert earlier.prev_minus == Decimal("75")
        assert current.prev_minus == Decimal("75")
        assert following.prev_minus == Decimal("175")


async def test_new_tier_never_inherits_another_tiers_template_or_opening_debt(
    initialization_case,
):
    case = initialization_case
    async with SessionLocal() as db:
        other_tier = FinanceBook(
            workspace_id=case["workspace"], buyer_id=case["buyer"],
            year=YEAR, month=MONTH - 1, tier="T23", prev_minus=Decimal("300"),
            eur_usd_rate=Decimal("1.5"),
        )
        db.add(other_tier)
        await db.flush()
        db.add(FinanceBookOffer(
            book_id=other_tier.id, name="Other tier", rate=Decimal("4"),
            rate_currency="EUR", geo="BR",
        ))
        await _pull(db, case)
        book = await _book(db, case)
        assert book.prev_minus == Decimal("0")
        assert book.eur_usd_rate == Decimal("1")
        assert (await load(db, book))["offers"] == []
        assert other_tier.prev_minus == Decimal("300")
