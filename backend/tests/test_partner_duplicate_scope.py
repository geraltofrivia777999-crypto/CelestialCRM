from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import (
    FinanceBook,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    Offer,
    PartnerIntegration,
)
from app.services.partner_deposits import distribute_deposits
from app.services.partner_integrations import PartnerServiceClient
from app.services.partner_sync import perform_sync
from tests.test_partner_integrations import pp_setup, pp_transport  # noqa: F401


async def test_selected_connection_is_sent_to_service(pp_setup, pp_transport):  # noqa: F811
    pp_transport["responses"][("GET", "stats")] = [
        {"date": "2026-08-31", "offer_id": "2687", "tag": "EVS", "deposits": 15}
    ]
    async with SessionLocal() as db:
        integration = await db.get(PartnerIntegration, pp_setup["integration"])
        for _ in range(2):
            result = await perform_sync(
                db, integration, PartnerServiceClient(), date(2026, 8, 31), date(2026, 8, 31)
            )
            assert result["upserted"] == 1
        requests = [c for c in pp_transport["calls"] if c["path"].endswith("/stats")]
        assert len(requests) == 2
        assert all(c["params"]["integration_id"] == "17" for c in requests)
        amounts = list(
            await db.scalars(
                select(FinanceTagDay.deposits)
                .join(FinanceOfferTag)
                .join(FinanceBookOffer)
                .where(FinanceBookOffer.source_offer_id == pp_setup["offer"])
            )
        )
        assert amounts == [Decimal(15)]
        await db.commit()


async def test_all_days_use_same_correct_tier_when_legacy_copy_exists(pp_setup):  # noqa: F811
    async with SessionLocal() as db:
        offer = await db.get(Offer, pp_setup["offer"])
        offer.geo = "PE"
        old = await db.scalar(select(FinanceBook).where(FinanceBook.buyer_id == pp_setup["buyer"]))
        correct = FinanceBook(
            workspace_id=pp_setup["workspace"],
            buyer_id=pp_setup["buyer"],
            year=2026,
            month=8,
            tier="T23",
        )
        db.add(correct)
        await db.flush()
        row = FinanceBookOffer(
            book_id=correct.id, name="PP Offer", geo="PE", source_offer_id=offer.id
        )
        db.add(row)
        await db.flush()
        tag = FinanceOfferTag(offer_id=row.id, name="EVS")
        db.add(tag)
        await db.flush()
        facts = [
            {"date": f"2026-08-{day}", "offer_id": str(offer.id), "tag": "EVS", "deposits": 15}
            for day in (30, 31)
        ]
        for _ in range(2):
            assert (await distribute_deposits(db, pp_setup["workspace"], facts))["upserted"] == 2
        values = list(await db.scalars(select(FinanceTagDay).where(FinanceTagDay.tag_id == tag.id)))
        assert len(values) == 2
        assert sum(v.deposits for v in values) == 30
        old_values = list(
            await db.scalars(
                select(FinanceTagDay)
                .join(FinanceOfferTag)
                .join(FinanceBookOffer)
                .where(FinanceBookOffer.book_id == old.id)
            )
        )
        assert old_values == []
        await db.commit()
