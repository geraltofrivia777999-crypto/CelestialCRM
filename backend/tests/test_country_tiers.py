from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models import (
    CountryTier,
    FinanceBook,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    User,
)
from app.services.country_tiers import DEFAULT_TIER_1
from tests.test_media_finance import _admin_client


@pytest.fixture
async def book_with_two_geos(database):
    """Книга с двумя офферами: США и Бразилия, по сто долларов дохода каждый."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        book = FinanceBook(
            workspace_id=admin.workspace_id,
            buyer_id=admin.id,
            year=2026,
            month=9,
            prev_minus=Decimal("0"),
            eur_usd_rate=Decimal("1"),
        )
        db.add(book)
        await db.flush()
        for position, geo in enumerate(("US", "BR")):
            offer = FinanceBookOffer(
                book_id=book.id,
                position=position,
                name=f"Оффер {geo}",
                geo=geo,
                rate=Decimal("10"),
                rate_currency="USD",
            )
            db.add(offer)
            await db.flush()
            tag = FinanceOfferTag(offer_id=offer.id, position=0, name="SOK")
            db.add(tag)
            await db.flush()
            db.add(FinanceTagDay(tag_id=tag.id, day=1, deposits=Decimal("10")))
        await db.commit()
        ids = {"book": book.id, "workspace": admin.workspace_id, "buyer": str(admin.id)}

    yield ids

    async with SessionLocal() as db:
        offers = select(FinanceBookOffer.id).where(
            FinanceBookOffer.book_id == ids["book"]
        )
        tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(offers))
        await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(tags)))
        await db.execute(delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(offers)))
        await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id == ids["book"]))
        await db.execute(delete(FinanceBook).where(FinanceBook.id == ids["book"]))
        # Справочник возвращаем к базовому виду: он общий на воркспейс.
        await db.execute(
            delete(CountryTier).where(CountryTier.workspace_id == ids["workspace"])
        )
        for code in DEFAULT_TIER_1:
            db.add(
                CountryTier(workspace_id=ids["workspace"], code=code, tier="T1")
            )
        await db.commit()


async def test_the_base_list_is_seeded_and_the_rest_is_tier_two_three(database) -> None:
    with _admin_client() as client:
        payload = client.get("/api/v1/country-tiers").json()

    codes = {row["code"] for row in payload["tier1"]}
    assert codes == set(DEFAULT_TIER_1)
    assert "BR" not in codes
    assert "BR" in {row["code"] for row in payload["tier23"]}
    # Обе таблицы вместе — это весь справочник стран, без пересечений.
    assert codes.isdisjoint({row["code"] for row in payload["tier23"]})


async def test_the_reference_only_labels_the_geo(book_with_two_geos) -> None:
    """Тир задаёт таблица, а справочник подписывает страну рядом с гео."""
    with _admin_client() as client:
        client.put("/api/v1/country-tiers", json={"tier1": ["BR"]})
        book = client.get(
            f"/api/v1/finance/book?buyer_id={book_with_two_geos['buyer']}"
            "&year=2026&month=9&tier=T1"
        ).json()

    assert book["tier"] == "T1"
    # Подпись пошла за справочником, а книга осталась своей таблицей.
    assert {offer["geo"]: offer["tier"] for offer in book["offers"]} == {
        "US": "T23",
        "BR": "T1",
    }
