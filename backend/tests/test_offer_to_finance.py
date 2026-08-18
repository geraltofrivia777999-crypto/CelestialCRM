import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models import (
    FinanceBook,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    IntegrationConnection,
    Offer,
    OfferBuyer,
    Partner,
    User,
)
from tests.test_media_finance import _admin_client


@pytest.fixture
async def pulled_offer(database):
    """Ручной оффер на США со ставкой — его и раздают баеру."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        offer = Offer(
            workspace_id=admin.workspace_id,
            name="Оффер для книги",
            geo="US",
            cpa=Decimal("25"),
            cpa_currency="USD",
        )
        db.add(offer)
        await db.commit()
        ids = {
            "offer": str(offer.id),
            "offer_id": offer.id,
            "buyer": str(admin.id),
            "workspace": admin.workspace_id,
        }

    yield ids

    today = date.today()
    async with SessionLocal() as db:
        books = select(FinanceBook.id).where(
            FinanceBook.workspace_id == ids["workspace"],
            FinanceBook.year == today.year,
            FinanceBook.month == today.month,
        )
        offers = select(FinanceBookOffer.id).where(FinanceBookOffer.book_id.in_(books))
        tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(offers))
        await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(tags)))
        await db.execute(delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(offers)))
        await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(books)))
        await db.execute(delete(FinanceBook).where(FinanceBook.id.in_(books)))
        await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id == ids["offer_id"]))
        await db.execute(delete(Offer).where(Offer.id == ids["offer_id"]))
        await db.commit()


def _book(client, buyer_id: str, tier: str) -> dict:
    today = date.today()
    return client.get(
        f"/api/v1/finance/book?buyer_id={buyer_id}&year={today.year}"
        f"&month={today.month}&tier={tier}"
    ).json()


async def test_assigning_a_buyer_pulls_the_offer_into_the_book(pulled_offer) -> None:
    """Строку в Финансах больше не заводят руками — она приезжает со справочником."""
    with _admin_client() as client:
        response = client.put(
            f"/api/v1/offers/{pulled_offer['offer']}/buyers",
            json={"buyer_ids": [pulled_offer["buyer"]]},
        )
        book = _book(client, pulled_offer["buyer"], "T1")

    assert response.status_code == 200
    assert response.json()["pulled_to_finance"] == [pulled_offer["buyer"]]
    row = book["offers"][0]
    assert row["name"] == "Оффер для книги"
    assert row["geo"] == "US"
    assert Decimal(row["rate"]) == Decimal("25")
    assert row["source_offer_id"] == pulled_offer["offer"]
    # Пустая строка под оффером: без неё вводить депозиты некуда.
    assert len(row["tags"]) == 1


async def test_the_geo_decides_which_table_gets_the_offer(pulled_offer) -> None:
    """Тир — это страна, поэтому бразильский оффер уезжает в Tier2/3."""
    with _admin_client() as client:
        client.put(
            f"/api/v1/offers/{pulled_offer['offer']}",
            json={"name": "Оффер для книги", "geo": "BR", "cpa": 25},
        )
        client.put(
            f"/api/v1/offers/{pulled_offer['offer']}/buyers",
            json={"buyer_ids": [pulled_offer["buyer"]]},
        )
        tier_one = _book(client, pulled_offer["buyer"], "T1")
        tier_two = _book(client, pulled_offer["buyer"], "T23")

    assert tier_one["offers"] == []
    assert [row["geo"] for row in tier_two["offers"]] == ["BR"]


async def test_a_repeated_assignment_updates_the_row_instead_of_doubling_it(
    pulled_offer,
) -> None:
    """Второе назначение не должно заводить второй такой же оффер."""
    with _admin_client() as client:
        client.put(
            f"/api/v1/offers/{pulled_offer['offer']}/buyers",
            json={"buyer_ids": [pulled_offer["buyer"]]},
        )
        client.put(
            f"/api/v1/offers/{pulled_offer['offer']}",
            json={"name": "Оффер для книги", "geo": "US", "cpa": 40},
        )
        client.put(
            f"/api/v1/offers/{pulled_offer['offer']}/buyers",
            json={"buyer_ids": [pulled_offer["buyer"]]},
        )
        book = _book(client, pulled_offer["buyer"], "T1")

    assert len(book["offers"]) == 1
    # Ставка в справочнике поменялась — книга подтянула новую.
    assert Decimal(book["offers"][0]["rate"]) == Decimal("40")


async def test_an_offer_without_geo_is_not_pulled(pulled_offer) -> None:
    """Тир — это страна: без гео выбрать таблицу нечем, и мы не гадаем."""
    with _admin_client() as client:
        client.put(
            f"/api/v1/offers/{pulled_offer['offer']}",
            json={"name": "Оффер для книги", "cpa": 25},
        )
        response = client.put(
            f"/api/v1/offers/{pulled_offer['offer']}/buyers",
            json={"buyer_ids": [pulled_offer["buyer"]]},
        )
        tier_one = _book(client, pulled_offer["buyer"], "T1")
        tier_two = _book(client, pulled_offer["buyer"], "T23")

    assert response.json()["pulled_to_finance"] == []
    assert tier_one["offers"] == []
    assert tier_two["offers"] == []


async def test_editing_the_book_updates_the_offer_reference(pulled_offer) -> None:
    """Связь двусторонняя: ставку и гео чаще правит тот, кто ведёт книгу."""
    today = date.today()
    with _admin_client() as client:
        client.put(
            f"/api/v1/offers/{pulled_offer['offer']}/buyers",
            json={"buyer_ids": [pulled_offer["buyer"]]},
        )
        book = _book(client, pulled_offer["buyer"], "T1")
        row = book["offers"][0]
        client.put(
            "/api/v1/finance/book",
            json={
                "buyer_id": pulled_offer["buyer"],
                "year": today.year,
                "month": today.month,
                "tier": "T1",
                "eur_usd_rate": 1,
                "days": {},
                "offers": [
                    {
                        "name": row["name"],
                        "geo": "CA",
                        "rate": 33,
                        "rate_currency": "EUR",
                        "source_offer_id": row["source_offer_id"],
                        "tags": [{"name": "SOK", "values": {}}],
                    }
                ],
            },
        )
        offers = client.get("/api/v1/offers?manual=true&limit=200").json()["items"]

    offer = [item for item in offers if item["id"] == pulled_offer["offer"]][0]
    assert offer["geo"] == "CA"
    assert Decimal(offer["cpa"]) == Decimal("33")
    assert offer["cpa_currency"] == "EUR"
    # Название справочника правкой книги не переписывается.
    assert offer["name"] == "Оффер для книги"


async def test_an_offer_is_created_with_a_partner_and_a_buyer_at_once(database) -> None:
    """Оффер заводят сразу с партнёркой и баером — это должно работать с первого раза.

    У нового объекта связь `partner` ещё не загружена, и обращение к ней в
    асинхронной сессии роняло запрос `MissingGreenlet`. Из базы оффер приходит
    со связью, поэтому назначение вторым шагом проходило и ошибку прятало.
    """
    today = date.today()
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        # Партнёрка приходит из трекера, поэтому подключение ей обязательно.
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == admin.workspace_id
            )
        )
        if connection is None:
            connection = IntegrationConnection(
                workspace_id=admin.workspace_id,
                name="Партнёрский трекер",
                base_url="https://tracker.example",
                api_key_encrypted=encrypt_secret("offer-pull-key"),
            )
            db.add(connection)
            await db.flush()
        partner = Partner(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id="offer-pull-partner",
            name="Партнёрка сразу",
        )
        db.add(partner)
        await db.commit()
        partner_id, buyer_id = str(partner.id), str(admin.id)
        workspace_id = admin.workspace_id

    try:
        with _admin_client() as client:
            created = client.post(
                "/api/v1/offers",
                json={
                    "name": "Оффер сразу с баером",
                    "geo": "US",
                    "cpa": 20,
                    "partner_id": partner_id,
                    "buyer_ids": [buyer_id],
                },
            )
            book = _book(client, buyer_id, "T1")

        assert created.status_code == 201
        row = [item for item in book["offers"] if item["name"] == "Оффер сразу с баером"][0]
        assert row["partner"] == "Партнёрка сразу"
    finally:
        async with SessionLocal() as db:
            books = select(FinanceBook.id).where(
                FinanceBook.workspace_id == workspace_id,
                FinanceBook.year == today.year,
                FinanceBook.month == today.month,
            )
            offers = select(FinanceBookOffer.id).where(FinanceBookOffer.book_id.in_(books))
            tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(offers))
            await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(tags)))
            await db.execute(
                delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(offers))
            )
            await db.execute(
                delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(books))
            )
            await db.execute(delete(FinanceBook).where(FinanceBook.id.in_(books)))
            catalog = select(Offer.id).where(Offer.name == "Оффер сразу с баером")
            await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id.in_(catalog)))
            await db.execute(delete(Offer).where(Offer.id.in_(catalog)))
            await db.execute(delete(Partner).where(Partner.id == uuid.UUID(partner_id)))
            await db.commit()


async def test_the_book_offers_the_same_partners_as_the_catalog(database) -> None:
    """Партнёрки в книге — те же, что в Офферах: они приходят из Keitaro."""
    today = date.today()
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == admin.workspace_id
            )
        )
        if connection is None:
            connection = IntegrationConnection(
                workspace_id=admin.workspace_id,
                name="Справочник партнёрок",
                base_url="https://tracker.example",
                api_key_encrypted=encrypt_secret("partners-key"),
            )
            db.add(connection)
            await db.flush()
        partner = Partner(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id="scopes-partner",
            name="Партнёрка книги",
        )
        db.add(partner)
        await db.commit()
        partner_id = partner.id

    try:
        with _admin_client() as client:
            scopes = client.get(
                f"/api/v1/finance/scopes?year={today.year}&month={today.month}"
            ).json()

        assert "Партнёрка книги" in scopes["partners"]
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Partner).where(Partner.id == partner_id))
            await db.commit()
