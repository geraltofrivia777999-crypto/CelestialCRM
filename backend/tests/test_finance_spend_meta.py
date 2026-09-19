"""Meta spend commits reconcile finance books without a later read repairing them."""

import uuid
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
    MetaSpendCommit,
    Offer,
)
from tests.test_meta import (  # noqa: F401
    _admin_client,
    _commit_payload,
    meta_connection,
    spend_window,
)


@pytest.fixture
async def finance_spend_window(spend_window):  # noqa: F811
    day = spend_window["day"]
    async with SessionLocal() as db:
        offer = await db.get(Offer, spend_window["offer"])
        offer.geo = "US"
        existing_books = set(await db.scalars(select(FinanceBook.id).where(
            FinanceBook.workspace_id == spend_window["workspace"],
            FinanceBook.buyer_id == spend_window["buyer"],
            FinanceBook.year == day.year,
            FinanceBook.month == day.month,
        )))
        await db.commit()

    yield spend_window

    # The shared SQLite test database does not enable ON DELETE CASCADE.
    # Automatic books can also inherit offer/tag templates from an earlier month.
    async with SessionLocal() as db:
        new_books = select(FinanceBook.id).where(
            FinanceBook.workspace_id == spend_window["workspace"],
            FinanceBook.buyer_id == spend_window["buyer"],
            FinanceBook.year == day.year,
            FinanceBook.month == day.month,
            FinanceBook.id.not_in(existing_books),
        )
        offers = select(FinanceBookOffer.id).where(FinanceBookOffer.book_id.in_(new_books))
        tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(offers))
        await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(tags)))
        await db.execute(delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(offers)))
        await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(new_books)))
        await db.execute(delete(FinanceBookDay).where(FinanceBookDay.book_id.in_(new_books)))
        await db.execute(delete(FinanceBook).where(FinanceBook.id.in_(new_books)))
        await db.commit()


async def test_meta_commit_and_last_commit_removal_update_finance_immediately(
    finance_spend_window,
):
    case = finance_spend_window
    day = case["day"]
    with _admin_client() as client:
        response = client.post("/api/v1/meta/spend/commit", json=_commit_payload(case))
    assert response.status_code == 201, response.text
    body = response.json()
    assert Decimal(str(body["spend"])) == Decimal("4.4")
    record_id = uuid.UUID(body["media_record_ids"][0])

    async with SessionLocal() as db:
        # Inspect persisted rows before any finance GET: the GET itself backfills,
        # so checking only the API response would conceal a missing commit hook.
        record = await db.get(MediaRecord, record_id)
        assert record.spend_calculated == Decimal("4.4")
        finance_day = await db.scalar(select(FinanceBookDay).join(FinanceBook).where(
            FinanceBook.workspace_id == case["workspace"],
            FinanceBook.buyer_id == case["buyer"],
            FinanceBook.year == day.year,
            FinanceBook.month == day.month,
            FinanceBook.tier == "T1",
            FinanceBookDay.day == day.day,
        ))
        assert finance_day is not None
        # Спенд книги ведётся в целых долларах: 4.4 → 4. В самой записи
        # медиаборда сумма остаётся точной — округление живёт на границе
        # «медиаборд → книга».
        assert finance_day.media_spend == finance_day.spend_buyer == Decimal("4")
        assert finance_day.manual_spend is None
        finance_day_id = finance_day.id
        commits = (await db.scalars(select(MetaSpendCommit).where(
            MetaSpendCommit.media_record_id == record_id,
        ))).all()
        assert len(commits) == 1
        commit_id = commits[0].id

    with _admin_client() as client:
        response = client.delete(f"/api/v1/meta/spend/commits/{commit_id}")
    assert response.status_code == 200, response.text

    async with SessionLocal() as db:
        assert await db.get(MetaSpendCommit, commit_id) is None
        record = await db.get(MediaRecord, record_id)
        finance_day = await db.get(FinanceBookDay, finance_day_id)
        assert record.spend_calculated == Decimal("0")
        assert finance_day.media_spend == finance_day.spend_buyer == Decimal("0")
        assert finance_day.manual_spend is None
