"""Background source writers keep the same financial books as media refreshes."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select

from app.core.database import SessionLocal
from app.models import (
    FinanceBook,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    IntegrationConnection,
    Offer,
)
from app.services.keitaro_sync import KeitaroSyncEngine
from app.services.partner_deposits import distribute_deposits
from tests.test_finance_book import buyer_id  # noqa: F401
from tests.test_finance_spend import DAY, MONTH, YEAR, _day, _sync, spend_case  # noqa: F401


@pytest.mark.parametrize("geo", ["BR", None])
async def test_keitaro_directory_geo_change_reclassifies_old_spend(spend_case, geo):  # noqa: F811
    async with SessionLocal() as db:
        connection = IntegrationConnection(
            workspace_id=spend_case["workspace"],
            name="Spend directory regression",
            base_url="https://tracker.example",
            api_key_encrypted="unused",
        )
        db.add(connection)
        await db.flush()
        connection_id = connection.id
        offer = await db.get(Offer, spend_case["offer"])
        offer.connection_id = connection_id
        offer.external_id = "spend-source-regression"
        await _sync(db, spend_case)
        await db.commit()

    client = AsyncMock()
    client.affiliate_networks.return_value = []
    client.groups.return_value = []
    client.campaigns.return_value = []
    client.offers.return_value = [{
        "id": "spend-source-regression", "name": "Directory offer", "country": geo,
    }]
    try:
        engine = KeitaroSyncEngine(SessionLocal)
        config = {"id": connection_id, "workspace_id": spend_case["workspace"]}
        await engine._sync_references(config, client)
        # No statistics sync or finance GET may be needed to repair old months.
        async with SessionLocal() as db:
            assert (await _day(db, spend_case, "T1")).spend_buyer == Decimal("0")
            other = await _day(db, spend_case, "T23")
            if geo:
                assert other.media_spend == other.spend_buyer == Decimal("100")
            else:
                assert other is None
                assert (await _sync(db, spend_case))["unassigned"] == Decimal("100")
        # The second directory fetch must not duplicate spend or financial books.
        await engine._sync_references(config, client)
        async with SessionLocal() as db:
            assert (await _day(db, spend_case, "T1")).spend_buyer == Decimal("0")
            if geo:
                assert (await _day(db, spend_case, "T23")).spend_buyer == Decimal("100")
    finally:
        async with SessionLocal() as db:
            offer = await db.get(Offer, spend_case["offer"])
            offer.connection_id = None
            await db.execute(delete(IntegrationConnection).where(
                IntegrationConnection.id == connection_id,
            ))
            await db.commit()


async def test_deposit_created_month_reuses_book_for_automatic_spend(spend_case):  # noqa: F811
    async with SessionLocal() as db:
        previous = FinanceBook(
            workspace_id=spend_case["workspace"], buyer_id=spend_case["buyer"],
            year=YEAR, month=MONTH - 1, tier="T1",
        )
        db.add(previous)
        await db.flush()
        offer_row = FinanceBookOffer(
            book_id=previous.id, source_offer_id=spend_case["offer"],
            name="Auto spend regression", geo="US", rate=Decimal("10"),
        )
        db.add(offer_row)
        await db.flush()
        db.add(FinanceOfferTag(offer_id=offer_row.id, name="SOURCE-BUYER"))
        await db.commit()

    facts = [{
        "date": f"{YEAR}-{MONTH:02d}-{DAY:02d}",
        "offer_id": str(spend_case["offer"]), "tag": "SOURCE-BUYER", "deposits": 4,
    }]
    async with SessionLocal() as db:
        for _ in range(2):
            assert (await distribute_deposits(db, spend_case["workspace"], facts))["upserted"] == 1
            await _sync(db, spend_case)
        assert await db.scalar(select(func.count()).select_from(FinanceBook).where(
            FinanceBook.buyer_id == spend_case["buyer"], FinanceBook.year == YEAR,
            FinanceBook.month == MONTH, FinanceBook.tier == "T1",
        )) == 1
        spend = await _day(db, spend_case)
        assert spend.media_spend == spend.spend_buyer == Decimal("100")
        deposits = await db.scalar(
            select(func.sum(FinanceTagDay.deposits))
            .join(FinanceOfferTag).join(FinanceBookOffer).join(FinanceBook)
            .where(FinanceBook.buyer_id == spend_case["buyer"], FinanceBook.year == YEAR,
                   FinanceBook.month == MONTH, FinanceBook.tier == "T1")
        )
        assert deposits == Decimal("4")
        await db.commit()
