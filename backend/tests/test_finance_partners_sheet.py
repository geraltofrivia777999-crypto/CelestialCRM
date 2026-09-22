"""Сводка «Партнёрки»: общий лист, который пишет прямо в книги баеров."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models import (
    FinanceBook,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    Offer,
    OfferBuyer,
    User,
)
from tests.test_media_finance import _admin_client

SHEET = "/api/v1/finance/partners"


@pytest.fixture
async def assigned_offer(database):
    """Ручной оффер на Колумбию, раздан администратору как баеру."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        offer = Offer(
            workspace_id=admin.workspace_id,
            name="Jugabet sheet",
            geo="CO",
            cpa=Decimal("16"),
            cpa_currency="USD",
        )
        db.add(offer)
        await db.flush()
        db.add(OfferBuyer(offer_id=offer.id, user_id=admin.id))
        await db.commit()
        ids = {
            "offer": str(offer.id), "offer_id": offer.id,
            "buyer": str(admin.id), "buyer_id": admin.id, "workspace": admin.workspace_id,
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


def _sheet(client) -> dict:
    today = date.today()
    response = client.get(f"{SHEET}?year={today.year}&month={today.month}")
    assert response.status_code == 200, response.text
    return response.json()


async def test_assigned_offers_appear_and_typing_lands_in_the_buyer_book(assigned_offer) -> None:
    today = date.today()
    with _admin_client() as client:
        sheet = _sheet(client)
        buyer = next(row for row in sheet["buyers"] if row["id"] == assigned_offer["buyer"])
        row = next(
            item for item in buyer["offers"]
            if item["source_offer_id"] == assigned_offer["offer"]
        )
        # Оффер раздан, но строки в книге ещё нет — она заводится при вводе.
        assert row["book_offer_id"] is None
        assert (row["name"], row["geo"]) == ("Jugabet sheet", "CO")
        assert Decimal(str(row["rate"])) == Decimal("16")

        saved = client.put(SHEET, json={
            "year": today.year, "month": today.month,
            "tags": [{
                "buyer_id": assigned_offer["buyer"],
                "source_offer_id": assigned_offer["offer"],
                "name": "mx | co",
                "values": {"3": "5", "4": "2"},
            }],
        })
        assert saved.status_code == 200, saved.text
        written = saved.json()["saved"][0]
        assert written["tier"] in {"T1", "T23"}

        # Та же строка теперь видна баеру в его книге.
        book = client.get(
            f"/api/v1/finance/book?buyer_id={assigned_offer['buyer']}"
            f"&year={today.year}&month={today.month}&tier={written['tier']}"
        ).json()
        offer = next(item for item in book["offers"] if item["name"] == "Jugabet sheet")
        assert offer["tags"][0]["name"] == "mx | co"
        assert Decimal(offer["tags"][0]["values"]["3"]) == Decimal("5")
        # Доход книги считается по ставке оффера: 7 депозитов × 16.
        assert Decimal(str(book["totals"]["total"]["income"])) == Decimal("112")

        # Повторное сохранение меняет уже существующую строку, а не плодит новые.
        again = client.put(SHEET, json={
            "year": today.year, "month": today.month,
            "tags": [{
                "buyer_id": assigned_offer["buyer"],
                "book_offer_id": written["book_offer_id"],
                "tag_id": written["tag_id"],
                "name": "mx | co",
                "values": {"3": "9", "4": "0"},
            }],
        })
        assert again.status_code == 200, again.text
        sheet = _sheet(client)
        buyer = next(row for row in sheet["buyers"] if row["id"] == assigned_offer["buyer"])
        row = next(
            item for item in buyer["offers"]
            if item["book_offer_id"] == written["book_offer_id"]
        )
        assert len(row["tags"]) == 1
        # Ноль стирает день, а не пишет ноль: пустая ячейка и есть «не было».
        assert list(row["tags"][0]["values"]) == ["3"]
        assert Decimal(str(row["tags"][0]["values"]["3"])) == Decimal("9")

        dropped = client.put(SHEET, json={
            "year": today.year, "month": today.month,
            "tags": [{
                "buyer_id": assigned_offer["buyer"],
                "book_offer_id": written["book_offer_id"],
                "tag_id": written["tag_id"],
                "drop": True,
            }],
        })
        assert dropped.status_code == 200
        book = client.get(
            f"/api/v1/finance/book?buyer_id={assigned_offer['buyer']}"
            f"&year={today.year}&month={today.month}&tier={written['tier']}"
        ).json()
        offer = next(item for item in book["offers"] if item["name"] == "Jugabet sheet")
        assert offer["tags"] == []


async def test_a_stranger_book_is_out_of_reach(assigned_offer) -> None:
    """Чужой баер недоступен: сводка ограничена той же областью, что и книги."""
    today = date.today()
    with _admin_client() as client:
        response = client.put(SHEET, json={
            "year": today.year, "month": today.month,
            "tags": [{
                "buyer_id": "00000000-0000-0000-0000-000000000000",
                "source_offer_id": assigned_offer["offer"],
                "name": "чужой",
                "values": {"1": "1"},
            }],
        })
    assert response.status_code == 404


async def test_typing_into_a_row_that_already_exists_in_the_book(assigned_offer) -> None:
    """Оффер уже в книге баера (его завело назначение), тега ещё нет.

    Сводка присылает строку книги и пустое имя тега — он должен завестись,
    а не отвечать «строка оффера не найдена».
    """
    today = date.today()
    with _admin_client() as client:
        # Назначение уже создало строку в книге: открываем книгу, чтобы она
        # точно существовала, и берём её id из сводки.
        client.put("/api/v1/offers/" + assigned_offer["offer"] + "/buyers",
                   json={"buyer_ids": [assigned_offer["buyer"]]})
        sheet = _sheet(client)
        buyer = next(row for row in sheet["buyers"] if row["id"] == assigned_offer["buyer"])
        row = next(
            item for item in buyer["offers"]
            if item["source_offer_id"] == assigned_offer["offer"]
        )
        assert row["book_offer_id"], "строка книги должна приехать в сводку"
        first = client.put(SHEET, json={
            "year": today.year, "month": today.month,
            "tags": [{
                "buyer_id": assigned_offer["buyer"],
                "book_offer_id": row["book_offer_id"],
                "source_offer_id": row["source_offer_id"],
                "tag_id": (row["tags"][0]["id"] if row["tags"] else None),
                "name": "EVS",
                "values": {"1": "10"},
            }],
        })
        assert first.status_code == 200, first.text
        written = first.json()["saved"][0]

        book = client.get(
            f"/api/v1/finance/book?buyer_id={assigned_offer['buyer']}"
            f"&year={today.year}&month={today.month}&tier={written['tier']}"
        ).json()
        offer = next(item for item in book["offers"] if item["name"] == "Jugabet sheet")
        assert offer["tags"][0]["name"] == "EVS"
        assert Decimal(offer["tags"][0]["values"]["1"]) == Decimal("10")


async def test_stale_ids_do_not_lose_the_input(assigned_offer) -> None:
    """Книгу пересохранили — id строк сменились, а ввод всё равно доезжает.

    Сохранение книги удаляет её строки и заводит заново, поэтому id, с
    которыми открыта сводка, живут недолго. Строку находим по офферу, тег — по
    названию, и заводим, если такого у баера нет.
    """
    today = date.today()
    with _admin_client() as client:
        first = client.put(SHEET, json={
            "year": today.year, "month": today.month,
            "tags": [{
                "buyer_id": assigned_offer["buyer"],
                "source_offer_id": assigned_offer["offer"],
                "name": "EVS",
                "values": {"2": "3"},
            }],
        })
        written = first.json()["saved"][0]

        # Баер сохранил свою книгу: строки пересозданы, id прежние недействительны.
        book = client.get(
            f"/api/v1/finance/book?buyer_id={assigned_offer['buyer']}"
            f"&year={today.year}&month={today.month}&tier={written['tier']}"
        ).json()
        resaved = client.put("/api/v1/finance/book", json={
            "buyer_id": assigned_offer["buyer"], "year": today.year, "month": today.month,
            "tier": written["tier"], "eur_usd_rate": book["eur_usd_rate"],
            "days": {}, "offers": [
                {
                    "name": offer["name"], "partner": offer["partner"], "geo": offer["geo"],
                    "rate": offer["rate"], "rate_currency": offer["rate_currency"],
                    "source_offer_id": offer["source_offer_id"],
                    "locked_fields": offer.get("locked_fields") or [],
                    "tags": [
                        {"name": tag["name"], "values": tag["values"]}
                        for tag in offer["tags"]
                    ],
                }
                for offer in book["offers"]
            ],
        })
        assert resaved.status_code == 200, resaved.text

        # Сводка ещё держит старые id — ввод должен лечь в ту же строку и тег.
        again = client.put(SHEET, json={
            "year": today.year, "month": today.month,
            "tags": [{
                "buyer_id": assigned_offer["buyer"],
                "book_offer_id": written["book_offer_id"],
                "source_offer_id": assigned_offer["offer"],
                "tag_id": written["tag_id"],
                "name": "EVS",
                "values": {"2": "8"},
            }],
        })
        assert again.status_code == 200, again.text

        book = client.get(
            f"/api/v1/finance/book?buyer_id={assigned_offer['buyer']}"
            f"&year={today.year}&month={today.month}&tier={written['tier']}"
        ).json()
        offer = next(item for item in book["offers"] if item["name"] == "Jugabet sheet")
        assert [tag["name"] for tag in offer["tags"]] == ["EVS"]
        assert Decimal(offer["tags"][0]["values"]["2"]) == Decimal("8")


async def test_naming_lands_on_the_row_the_buyer_left_unnamed(assigned_offer) -> None:
    """Баер завёл строку, не назвав её. Название из сводки — имя той же строки.

    Иначе рядом с его депозитами появлялась бы вторая, пустая строка.
    """
    today = date.today()
    with _admin_client() as client:
        # Баер вводит депозиты, не называя тег.
        client.put(SHEET, json={
            "year": today.year, "month": today.month,
            "tags": [{
                "buyer_id": assigned_offer["buyer"],
                "source_offer_id": assigned_offer["offer"],
                "name": "",
                "values": {"5": "4"},
            }],
        })
        # Из сводки ту же строку называют — id тега сводка ещё не знает.
        named = client.put(SHEET, json={
            "year": today.year, "month": today.month,
            "tags": [{
                "buyer_id": assigned_offer["buyer"],
                "source_offer_id": assigned_offer["offer"],
                "name": "mx | co",
                "values": {"5": "4"},
            }],
        })
        assert named.status_code == 200, named.text
        written = named.json()["saved"][0]
        book = client.get(
            f"/api/v1/finance/book?buyer_id={assigned_offer['buyer']}"
            f"&year={today.year}&month={today.month}&tier={written['tier']}"
        ).json()
        offer = next(item for item in book["offers"] if item["name"] == "Jugabet sheet")
        assert [tag["name"] for tag in offer["tags"]] == ["mx | co"]
        assert Decimal(offer["tags"][0]["values"]["5"]) == Decimal("4")
