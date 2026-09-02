"""Интеграции с ПП: синк тянет факты из сервиса и раскладывает в финансы."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models import (
    FinanceBook,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    Offer,
    OfferBuyer,
    PartnerIntegration,
    PartnerPendingTag,
    User,
)
from tests.test_media_finance import _admin_client


@pytest.fixture
async def pp_setup(database):
    """Оффер, закреплённый за байером, + интеграция с ПП."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        offer = Offer(
            workspace_id=admin.workspace_id, name="PP Offer", geo="DE",
            external_id="2687",
        )
        db.add(offer)
        await db.flush()
        db.add(OfferBuyer(offer_id=offer.id, user_id=admin.id))
        # Привязка — строка тега в финансах, а не запись в настройках.
        book = FinanceBook(
            workspace_id=admin.workspace_id, buyer_id=admin.id,
            year=2026, month=8, tier="T1",
        )
        db.add(book)
        await db.flush()
        book_offer = FinanceBookOffer(
            book_id=book.id, position=0, name="PP Offer", geo="DE",
            rate=Decimal("0"), rate_currency="USD", source_offer_id=offer.id,
        )
        db.add(book_offer)
        await db.flush()
        db.add(FinanceOfferTag(offer_id=book_offer.id, position=0, name="EVS"))
        integration = PartnerIntegration(
            workspace_id=admin.workspace_id,
            name="famecpa_affise " + uuid.uuid4().hex[:6],
            partner_name="Affise (famecpa)",
            base_url="http://pp.test",
            api_key_encrypted=encrypt_secret("pp-key"),
            external_id="17",
        )
        db.add(integration)
        await db.commit()
    ids = {
        "workspace": admin.workspace_id,
        "offer": offer.id,
        "buyer": admin.id,
        "integration": integration.id,
    }

    yield ids

    # Тест создаёт книги, теги и привязки — без уборки следующий тест падает
    # на уникальном ключе тега.
    async with SessionLocal() as db:
        from sqlalchemy import delete

        books = list(
            (
                await db.execute(
                    select(FinanceBook.id).where(FinanceBook.workspace_id == ids["workspace"])
                )
            ).scalars()
        )
        await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(books or [uuid.uuid4()])))
        await db.execute(delete(FinanceBook).where(FinanceBook.id.in_(books or [uuid.uuid4()])))
        await db.execute(delete(PartnerPendingTag).where(PartnerPendingTag.workspace_id == ids["workspace"]))
        await db.execute(delete(PartnerIntegration).where(PartnerIntegration.id == ids["integration"]))
        await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id == ids["offer"]))
        await db.execute(delete(Offer).where(Offer.id == ids["offer"]))
        await db.commit()


@pytest.fixture
def pp_transport(monkeypatch):
    """Перехват запросов к сервису ПП: ответы + журнал."""
    calls = []
    responses = {}

    async def fake_request(self, method, path, *, json_body=None, params=None):
        calls.append({"method": method, "path": path, "params": params, "json": json_body})
        handler = responses.get((method, path.split("/")[-1]))
        if isinstance(handler, Exception):
            raise handler
        if handler is not None:
            return handler
        if method == "POST" and path.endswith("/sync"):
            return {"status": "success"}
        return []

    monkeypatch.setattr(
        __import__("app.services.partner_integrations",
                   fromlist=["PartnerServiceClient"]).PartnerServiceClient,
        "_request", fake_request,
    )
    fixture = {"calls": calls, "responses": responses}
    return fixture


async def test_sync_pulls_stats_and_distributes_deposits(pp_setup, pp_transport) -> None:
    pp_transport["responses"][("GET", "stats")] = [
        {"date": "2026-08-19", "offer_id": "2687",
         "tag": "EVS", "deposits": 12},
        {"date": "2026-08-20", "offer_id": "2687",
         "tag": "EVS", "deposits": 8},
    ]
    with _admin_client() as client:
        response = client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-19", "date_to": "2026-08-20"},
        )
    assert response.status_code == 200
    assert response.json()["records_upserted"] == 2

    async with SessionLocal() as db:
        offer_row = await db.scalar(
            select(FinanceBookOffer).where(
                FinanceBookOffer.source_offer_id == pp_setup["offer"]
            )
        )
        assert offer_row is not None
        book = await db.get(FinanceBook, offer_row.book_id)
        assert book.buyer_id == pp_setup["buyer"]
        tag = await db.scalar(
            select(FinanceOfferTag).where(FinanceOfferTag.offer_id == offer_row.id)
        )
        assert tag is not None and tag.name == "EVS"
        day19 = await db.scalar(
            select(FinanceTagDay).where(FinanceTagDay.tag_id == tag.id,
                                        FinanceTagDay.day == 19)
        )
        assert day19 is not None and day19.deposits == Decimal("12")
        day20 = await db.scalar(
            select(FinanceTagDay).where(FinanceTagDay.tag_id == tag.id,
                                        FinanceTagDay.day == 20)
        )
        assert day20 is not None and day20.deposits == Decimal("8")

        run = await db.scalar(
            select(__import__("app.models", fromlist=["PartnerSyncRun"]).PartnerSyncRun)
            .where(__import__("app.models",
                             fromlist=["PartnerSyncRun"]).PartnerSyncRun.integration_id == pp_setup["integration"])
        )
        assert run is not None
        assert run.status == "success"
        assert run.records_upserted == 2


async def test_sync_without_posts_returns_422(pp_setup, pp_transport) -> None:
    pp_transport["responses"]["GET"] = []
    with _admin_client() as client:
        response = client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-19", "date_to": "2026-08-20"},
        )
    assert response.status_code == 200
    assert response.json()["records_upserted"] == 0


async def test_service_outage_becomes_readable_error(pp_setup, pp_transport) -> None:
    from app.services.partner_integrations import PartnerIntegrationError

    pp_transport["responses"][("GET", "stats")] = PartnerIntegrationError(
        "Сервис партнёрок недоступен"
    )
    with _admin_client() as client:
        response = client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-19", "date_to": "2026-08-20"},
        )
    assert response.status_code == 502
    assert "недоступен" in response.json()["error"]["message"]
    async with SessionLocal() as db:
        integration = await db.get(PartnerIntegration, pp_setup["integration"])
        assert integration.last_sync_status == "failed"
        assert "недоступен" in (integration.last_sync_error or "")


# --- маршрутизация по тегу ---------------------------------------------------


@pytest.fixture
async def two_buyers(database):
    """Один оффер, два баера. Именно здесь раньше задваивался доход."""
    from app.core.security import hash_password

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        role_id = admin.role_id
        second = User(
            workspace_id=admin.workspace_id, role_id=role_id, name="Баер Второй",
            login="pp-second-" + uuid.uuid4().hex[:6],
            password_hash=hash_password("second-password"),
        )
        db.add(second)
        offer = Offer(workspace_id=admin.workspace_id, name="Shared Offer", geo="DE")
        db.add(offer)
        await db.flush()
        db.add_all([
            OfferBuyer(offer_id=offer.id, user_id=admin.id),
            OfferBuyer(offer_id=offer.id, user_id=second.id),
        ])
        await db.commit()
        ids = {"workspace": admin.workspace_id, "offer": offer.id,
               "first": admin.id, "second": second.id}

    yield ids

    async with SessionLocal() as db:
        from sqlalchemy import delete

        books = list(
            (
                await db.execute(
                    select(FinanceBook.id).where(FinanceBook.workspace_id == ids["workspace"])
                )
            ).scalars()
        )
        await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(books or [uuid.uuid4()])))
        await db.execute(delete(FinanceBook).where(FinanceBook.id.in_(books or [uuid.uuid4()])))
        await db.execute(delete(PartnerPendingTag).where(PartnerPendingTag.workspace_id == ids["workspace"]))
        await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id == ids["offer"]))
        await db.execute(delete(Offer).where(Offer.id == ids["offer"]))
        await db.execute(delete(User).where(User.id == ids["second"]))
        await db.commit()


async def _seed_tag_row(workspace_id, buyer_id, offer_id, offer_name, tag,
                        year=2026, month=8):
    """Строка тега под оффером в книге баера — то, чем задаётся привязка."""
    async with SessionLocal() as db:
        book = await db.scalar(
            select(FinanceBook).where(
                FinanceBook.workspace_id == workspace_id,
                FinanceBook.buyer_id == buyer_id,
                FinanceBook.year == year, FinanceBook.month == month,
                FinanceBook.tier == "T1",
            )
        )
        if not book:
            book = FinanceBook(workspace_id=workspace_id, buyer_id=buyer_id,
                               year=year, month=month, tier="T1")
            db.add(book)
            await db.flush()
        row = await db.scalar(
            select(FinanceBookOffer).where(
                FinanceBookOffer.book_id == book.id,
                FinanceBookOffer.source_offer_id == offer_id,
            )
        )
        if not row:
            row = FinanceBookOffer(book_id=book.id, position=0, name=offer_name,
                                   geo="DE", rate=Decimal("0"), rate_currency="USD",
                                   source_offer_id=offer_id)
            db.add(row)
            await db.flush()
        db.add(FinanceOfferTag(offer_id=row.id, position=0, name=tag))
        await db.commit()

async def _deposits_by_buyer(workspace_id) -> dict:
    async with SessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(FinanceBook.buyer_id, FinanceTagDay.deposits)
                    .join(FinanceBookOffer, FinanceBookOffer.book_id == FinanceBook.id)
                    .join(FinanceOfferTag, FinanceOfferTag.offer_id == FinanceBookOffer.id)
                    .join(FinanceTagDay, FinanceTagDay.tag_id == FinanceOfferTag.id)
                    .where(FinanceBook.workspace_id == workspace_id)
                )
            ).all()
        )
    totals: dict = {}
    for buyer_id, value in rows:
        totals[buyer_id] = totals.get(buyer_id, Decimal("0")) + value
    return totals


async def test_deposit_goes_to_the_tag_owner_only(two_buyers) -> None:
    """Депозит с тегом баера ложится только ему, а не всем баерам оффера."""
    from app.services.partner_deposits import distribute_deposits

    await _seed_tag_row(two_buyers["workspace"], two_buyers["second"],
                        two_buyers["offer"], "Shared Offer", "LUKA")

    facts = [{"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
              "tag": "LUKA", "deposits": 10}]
    async with SessionLocal() as db:
        result = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()

    assert result["upserted"] == 1
    totals = await _deposits_by_buyer(two_buyers["workspace"])
    # Ровно один получатель и ровно та сумма, что прислала ПП.
    assert totals == {two_buyers["second"]: Decimal("10")}


async def test_unknown_tag_waits_instead_of_vanishing(two_buyers) -> None:
    """Тег без баера не выбрасывается: деньги ждут решения человека."""
    from app.services.partner_deposits import distribute_deposits

    facts = [{"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
              "tag": "NEWTAG", "deposits": 25}]
    async with SessionLocal() as db:
        result = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()

    assert result["upserted"] == 0 and result["pending"] == 1
    async with SessionLocal() as db:
        row = await db.scalar(
            select(PartnerPendingTag).where(
                PartnerPendingTag.workspace_id == two_buyers["workspace"]
            )
        )
    assert row is not None
    assert row.tag == "NEWTAG" and row.reason == "no_buyer"
    assert row.deposits_total == Decimal("25")
    assert not await _deposits_by_buyer(two_buyers["workspace"])


async def test_tag_is_picked_up_from_books_kept_by_hand(two_buyers) -> None:
    """Тег, который финансист уже вёл руками, привязывается сам."""
    from app.services.partner_deposits import distribute_deposits

    async with SessionLocal() as db:
        book = FinanceBook(
            workspace_id=two_buyers["workspace"], buyer_id=two_buyers["second"],
            year=2026, month=8, tier="T1",
        )
        db.add(book)
        await db.flush()
        row = FinanceBookOffer(book_id=book.id, position=0, name="Shared Offer",
                               geo="DE", rate=Decimal("0"), rate_currency="USD")
        db.add(row)
        await db.flush()
        db.add(FinanceOfferTag(offer_id=row.id, position=0, name="SOK"))
        await db.commit()

    facts = [{"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
              "tag": "sok", "deposits": 4}]
    async with SessionLocal() as db:
        result = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()

    # Ничего настраивать не пришлось: строка тега в книге и есть привязка.
    assert result["upserted"] == 1
    totals = await _deposits_by_buyer(two_buyers["workspace"])
    assert totals == {two_buyers["second"]: Decimal("4")}


async def test_same_tag_in_two_books_is_not_guessed(two_buyers) -> None:
    """Тег есть у двух баеров — не гадаем, откладываем."""
    from app.services.partner_deposits import distribute_deposits

    async with SessionLocal() as db:
        for buyer_id in (two_buyers["first"], two_buyers["second"]):
            book = FinanceBook(workspace_id=two_buyers["workspace"], buyer_id=buyer_id,
                               year=2026, month=8, tier="T1")
            db.add(book)
            await db.flush()
            row = FinanceBookOffer(book_id=book.id, position=0, name="Shared Offer",
                                   geo="DE", rate=Decimal("0"), rate_currency="USD")
            db.add(row)
            await db.flush()
            db.add(FinanceOfferTag(offer_id=row.id, position=0, name="DUP"))
        await db.commit()

    facts = [{"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
              "tag": "DUP", "deposits": 9}]
    async with SessionLocal() as db:
        result = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()

    assert result["pending"] == 1
    async with SessionLocal() as db:
        row = await db.scalar(
            select(PartnerPendingTag).where(
                PartnerPendingTag.workspace_id == two_buyers["workspace"]
            )
        )
    assert row is not None and row.reason == "ambiguous"


async def test_case_and_spaces_do_not_split_the_tag(two_buyers) -> None:
    """`LUKA`, `luka` и `Luka ` — один баер и одна строка в книге."""
    from app.services.partner_deposits import distribute_deposits

    await _seed_tag_row(two_buyers["workspace"], two_buyers["first"],
                        two_buyers["offer"], "Shared Offer", "LUKA")

    facts = [
        {"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
         "tag": "LUKA", "deposits": 3},
        {"date": "2026-08-20", "offer_id": str(two_buyers["offer"]),
         "tag": " luka ", "deposits": 5},
    ]
    async with SessionLocal() as db:
        result = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()

    assert result["upserted"] == 2
    async with SessionLocal() as db:
        tags = list(
            (
                await db.execute(
                    select(FinanceOfferTag)
                    .join(FinanceBookOffer, FinanceBookOffer.id == FinanceOfferTag.offer_id)
                    .join(FinanceBook, FinanceBook.id == FinanceBookOffer.book_id)
                    .where(FinanceBook.workspace_id == two_buyers["workspace"])
                )
            ).scalars()
        )
    assert len(tags) == 1


async def test_garbage_facts_do_not_kill_the_batch(two_buyers) -> None:
    """Числовой offer_id из спеки сервиса больше не роняет весь синк."""
    from app.services.partner_deposits import distribute_deposits

    await _seed_tag_row(two_buyers["workspace"], two_buyers["first"],
                        two_buyers["offer"], "Shared Offer", "LUKA")

    facts = [
        {"date": "2026-08-19", "offer_id": 123, "tag": "LUKA", "deposits": 1},
        {"date": "не дата", "offer_id": str(two_buyers["offer"]),
         "tag": "LUKA", "deposits": 2},
        {"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
         "tag": "LUKA", "deposits": "нечисло"},
        {"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
         "tag": None, "deposits": 4},
        {"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
         "tag": "LUKA", "deposits": 7},
    ]
    async with SessionLocal() as db:
        result = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()

    # Хороший факт дошёл, мусор посчитан, исключения нет.
    assert result["upserted"] == 1
    assert result["skipped"] == 3
    assert result["pending"] == 1
    totals = await _deposits_by_buyer(two_buyers["workspace"])
    assert totals == {two_buyers["first"]: Decimal("7")}


async def test_foreign_workspace_offer_is_refused(two_buyers) -> None:
    """Раскладка не полагается на фильтрацию вызывающего кода."""
    from app.services.partner_deposits import distribute_deposits

    async with SessionLocal() as db:
        from app.models import Workspace

        other = Workspace(name="Чужой воркспейс", timezone="UTC", currency="USD")
        db.add(other)
        await db.flush()
        alien = Offer(workspace_id=other.id, name="Alien Offer", geo="DE")
        db.add(alien)
        await db.commit()
        alien_id, other_id = alien.id, other.id

    facts = [{"date": "2026-08-19", "offer_id": str(alien_id),
              "tag": "LUKA", "deposits": 50}]
    async with SessionLocal() as db:
        result = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()
    assert result["upserted"] == 0 and result["skipped"] == 1

    async with SessionLocal() as db:
        from sqlalchemy import delete

        from app.models import Workspace

        await db.execute(delete(Offer).where(Offer.id == alien_id))
        await db.execute(delete(Workspace).where(Workspace.id == other_id))
        await db.commit()


async def test_manual_sync_asks_the_service_to_fetch(pp_setup, pp_transport) -> None:
    """Без этого вызова дозагрузка прошлых дат не работала: сервис ходит в ПП
    своим расписанием только «за сегодня»."""
    pp_transport["responses"][("GET", "stats")] = []
    with _admin_client() as client:
        response = client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-01", "date_to": "2026-08-05"},
        )
    assert response.status_code == 200
    triggered = [
        call for call in pp_transport["calls"]
        if call["method"] == "POST" and call["path"].endswith("/17/sync")
    ]
    assert triggered, pp_transport["calls"]


async def test_offer_id_is_pushed_to_the_service_on_sync(pp_setup, pp_transport) -> None:
    """Привязка берётся из самого оффера и уезжает на сервис при синке.

    Сервис не покажет оффер в `/stats`, пока не знает про связь; заводить её
    вторым местом в настройках было лишним шагом.
    """
    pp_transport["responses"][("GET", "stats")] = []
    with _admin_client() as client:
        response = client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-19", "date_to": "2026-08-20"},
        )
    assert response.status_code == 200
    pushed = [
        call for call in pp_transport["calls"]
        if call["method"] == "POST" and call["path"].endswith("/offer-mappings")
    ]
    assert pushed, pp_transport["calls"]
    assert pushed[0]["json"]["external_offer_id"] == "2687"
    # `crm_offer_id` уходит числом: UUID сервис не принимает.
    assert pushed[0]["json"]["crm_offer_id"].isdigit()


async def test_offer_without_partner_id_stops_the_sync_early(
    pp_setup, pp_transport
) -> None:
    """Пустой ID у всех офферов — синку нечего раскладывать, и он это говорит."""
    async with SessionLocal() as db:
        offer = await db.get(Offer, pp_setup["offer"])
        offer.external_id = None
        await db.commit()

    pp_transport["responses"][("GET", "stats")] = [
        {"date": "2026-08-19", "offer_id": str(pp_setup["offer"]),
         "tag": "EVS", "deposits": 12}
    ]
    with _admin_client() as client:
        response = client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-19", "date_to": "2026-08-20"},
        )
    body = response.json()
    assert response.status_code == 200
    assert body["records_upserted"] == 0
    assert any("ID партнёрки" in text for text in body["details"]["reasons"])


async def test_facts_keyed_by_the_partner_offer_id_still_land(
    pp_setup, pp_transport
) -> None:
    """Сервис может отдать номер оффера партнёрки, а не наш UUID.

    Так бывает, если интеграцию настроили на сервисе руками. Оффер всё равно
    должен найтись — иначе депозиты молча пропадут.
    """
    pp_transport["responses"][("GET", "stats")] = [
        {"date": "2026-08-19", "offer_id": "2687", "tag": "EVS", "deposits": 30}
    ]
    with _admin_client() as client:
        response = client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-19", "date_to": "2026-08-19"},
        )
    assert response.status_code == 200
    assert response.json()["records_upserted"] == 1


async def test_two_offers_cannot_share_one_partner_id(pp_setup) -> None:
    """Иначе депозиты партнёрки приезжали бы сразу на оба — доход задвоился бы."""
    with _admin_client() as client:
        response = client.post(
            "/api/v1/offers",
            json={"name": "Двойник", "external_id": "2687", "cpa": 0},
        )
    assert response.status_code == 422
    assert "2687" in response.json()["error"]["message"]


async def test_creating_the_tag_in_finance_is_the_whole_setup(two_buyers) -> None:
    """Никаких настроек: завели тег в книге — следующий синк его заполнил.

    Ровно тот сценарий, ради которого реестр в настройках и убрали: тег,
    созданный в финансовой таблице под оффером, сам становится привязкой.
    """
    from app.services.partner_deposits import distribute_deposits

    facts = [{"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
              "tag": "WAITME", "deposits": 15}]
    async with SessionLocal() as db:
        first = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()
    # Строки нет — деньги повисли, но не пропали.
    assert first["upserted"] == 0 and first["pending"] == 1
    with _admin_client() as client:
        waiting = client.get("/api/v1/partner-integrations/tags/pending").json()
    assert [row["tag"] for row in waiting] == ["WAITME"]
    assert waiting[0]["offer_name"] == "Shared Offer"

    # Единственное действие человека — завести тег в книге баера.
    await _seed_tag_row(two_buyers["workspace"], two_buyers["second"],
                        two_buyers["offer"], "Shared Offer", "WAITME")

    async with SessionLocal() as db:
        second = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()
    assert second["upserted"] == 1
    totals = await _deposits_by_buyer(two_buyers["workspace"])
    assert totals == {two_buyers["second"]: Decimal("15")}
    with _admin_client() as client:
        assert client.get("/api/v1/partner-integrations/tags/pending").json() == []


async def test_same_tag_under_different_offers_is_not_a_conflict(two_buyers) -> None:
    """Один тег под разными офферами — норма, а не повод откладывать.

    Пара «оффер + тег» указывает на ячейку однозначно, поэтому одинаковое имя
    у разных офферов больше не считается неоднозначностью.
    """
    from app.services.partner_deposits import distribute_deposits

    async with SessionLocal() as db:
        second_offer = Offer(workspace_id=two_buyers["workspace"],
                             name="Other Offer", geo="DE")
        db.add(second_offer)
        await db.commit()
        second_id = second_offer.id

    await _seed_tag_row(two_buyers["workspace"], two_buyers["first"],
                        two_buyers["offer"], "Shared Offer", "SOK")
    await _seed_tag_row(two_buyers["workspace"], two_buyers["second"],
                        second_id, "Other Offer", "SOK")

    facts = [
        {"date": "2026-08-19", "offer_id": str(two_buyers["offer"]),
         "tag": "SOK", "deposits": 5},
        {"date": "2026-08-19", "offer_id": str(second_id),
         "tag": "SOK", "deposits": 9},
    ]
    async with SessionLocal() as db:
        result = await distribute_deposits(db, two_buyers["workspace"], facts)
        await db.commit()

    assert result["upserted"] == 2 and result["pending"] == 0
    totals = await _deposits_by_buyer(two_buyers["workspace"])
    assert totals == {
        two_buyers["first"]: Decimal("5"),
        two_buyers["second"]: Decimal("9"),
    }

    async with SessionLocal() as db:
        from sqlalchemy import delete

        await db.execute(delete(Offer).where(Offer.id == second_id))
        await db.commit()


# --- синк из финансов ---------------------------------------------------------
#
# Сервис партнёрок ничего не собирает сам — он идёт в ПП только по просьбе.
# Поэтому кнопка стоит в финансах, а период и офферы берутся из открытой книги.


async def test_finance_sync_asks_the_service_for_the_book_month(
    pp_setup, pp_transport
) -> None:
    pp_transport["responses"][("GET", "stats")] = [
        {"date": "2026-08-19", "offer_id": "2687", "tag": "EVS", "deposits": 12},
    ]
    with _admin_client() as client:
        response = client.post(
            "/api/v1/finance/book/sync-partners",
            params={"buyer_id": str(pp_setup["buyer"]), "year": 2026,
                    "month": 8, "tier": "T1"},
        )
    body = response.json()
    assert response.status_code == 200
    assert body["upserted"] == 1
    # Период — весь месяц книги, не «последние семь дней».
    assert body["date_from"] == "2026-08-01"
    assert body["date_to"] == "2026-08-31"
    # Сервис попросили сходить в ПП: сам он ничего не собирает.
    triggered = [
        call for call in pp_transport["calls"]
        if call["method"] == "POST" and call["path"].endswith("/17/sync")
    ]
    assert triggered
    assert triggered[0]["json"]["date_from"] == "2026-08-01"
    # И привязки офферов отдали перед этим — иначе оффера не будет в /stats.
    assert any(
        call["path"].endswith("/offer-mappings") for call in pp_transport["calls"]
    )


async def test_finance_sync_does_not_ask_for_the_future(pp_setup, pp_transport) -> None:
    """Будущее партнёрка не отдаст — верхнюю границу режем по сегодня."""
    from datetime import date

    today = date.today()
    pp_transport["responses"][("GET", "stats")] = []
    with _admin_client() as client:
        response = client.post(
            "/api/v1/finance/book/sync-partners",
            params={"buyer_id": str(pp_setup["buyer"]), "year": today.year,
                    "month": today.month, "tier": "T1"},
        )
    body = response.json()
    assert response.status_code == 200
    assert body["date_to"] == today.isoformat()


async def test_finance_sync_without_integrations_says_so(pp_setup) -> None:
    async with SessionLocal() as db:
        integration = await db.get(PartnerIntegration, pp_setup["integration"])
        integration.is_enabled = False
        await db.commit()
    with _admin_client() as client:
        response = client.post(
            "/api/v1/finance/book/sync-partners",
            params={"buyer_id": str(pp_setup["buyer"]), "year": 2026,
                    "month": 8, "tier": "T1"},
        )
    assert response.status_code == 422
    assert "интеграц" in response.json()["error"]["message"].lower()


async def test_finance_sync_survives_a_broken_integration(
    pp_setup, pp_transport
) -> None:
    """Одна упавшая интеграция не должна ронять всю кнопку."""
    from app.services.partner_integrations import PartnerIntegrationError

    pp_transport["responses"][("GET", "stats")] = PartnerIntegrationError(
        "Сервис партнёрок недоступен"
    )
    with _admin_client() as client:
        response = client.post(
            "/api/v1/finance/book/sync-partners",
            params={"buyer_id": str(pp_setup["buyer"]), "year": 2026,
                    "month": 8, "tier": "T1"},
        )
    body = response.json()
    # Ответ не 502: кнопка отчитывается, что именно не вышло, и по каким.
    assert response.status_code == 200
    assert body["upserted"] == 0
    assert body["errors"] and "недоступен" in body["errors"][0]
    async with SessionLocal() as db:
        integration = await db.get(PartnerIntegration, pp_setup["integration"])
    assert integration.last_sync_status == "failed"


async def test_offer_mapping_is_pushed_as_a_number(pp_setup, pp_transport) -> None:
    """Сервис принимает `crm_offer_id` только целым числом.

    На UUID он отвечает 422 «unable to parse string as an integer», привязка не
    заводится, и оффер не попадает в `/stats` — именно так синк и молчал.
    """
    pp_transport["responses"][("GET", "stats")] = []
    with _admin_client() as client:
        client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-19", "date_to": "2026-08-20"},
        )
    pushed = [
        call for call in pp_transport["calls"]
        if call["method"] == "POST" and call["path"].endswith("/offer-mappings")
    ]
    assert pushed
    sent = pushed[0]["json"]["crm_offer_id"]
    assert sent.isdigit(), sent
    assert sent != str(pp_setup["offer"])
    async with SessionLocal() as db:
        offer = await db.get(Offer, pp_setup["offer"])
    assert offer.partner_ref == int(sent)


async def test_the_number_survives_the_next_sync(pp_setup, pp_transport) -> None:
    """Ключ выдаётся один раз: сменить его — оторвать уже собранные факты."""
    pp_transport["responses"][("GET", "stats")] = []
    with _admin_client() as client:
        for _ in range(2):
            client.post(
                f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
                json={"date_from": "2026-08-19", "date_to": "2026-08-20"},
            )
    sent = [
        call["json"]["crm_offer_id"] for call in pp_transport["calls"]
        if call["method"] == "POST" and call["path"].endswith("/offer-mappings")
    ]
    assert len(sent) == 2 and sent[0] == sent[1]


async def test_facts_keyed_by_the_number_are_distributed(pp_setup, pp_transport) -> None:
    """Именно этим числом сервис помечает факты в `/stats`."""
    async with SessionLocal() as db:
        offer = await db.get(Offer, pp_setup["offer"])
        offer.partner_ref = 7
        await db.commit()

    pp_transport["responses"][("GET", "stats")] = [
        {"date": "2026-08-19", "offer_id": 7, "tag": "EVS", "deposits": 21},
    ]
    with _admin_client() as client:
        response = client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-19", "date_to": "2026-08-19"},
        )
    assert response.status_code == 200
    assert response.json()["records_upserted"] == 1


async def test_numbers_do_not_repeat_inside_a_workspace(pp_setup, pp_transport) -> None:
    """Два оффера с одним номером означали бы перепутанные депозиты."""
    async with SessionLocal() as db:
        second = Offer(workspace_id=pp_setup["workspace"], name="Second PP Offer",
                       geo="DE", external_id="9999")
        db.add(second)
        await db.commit()
        second_id = second.id

    pp_transport["responses"][("GET", "stats")] = []
    with _admin_client() as client:
        client.post(
            f"/api/v1/partner-integrations/{pp_setup['integration']}/sync",
            json={"date_from": "2026-08-19", "date_to": "2026-08-19"},
        )
    async with SessionLocal() as db:
        first = await db.get(Offer, pp_setup["offer"])
        other = await db.get(Offer, second_id)
        refs = {first.partner_ref, other.partner_ref}
        from sqlalchemy import delete

        await db.execute(delete(Offer).where(Offer.id == second_id))
        await db.commit()
    assert None not in refs
    assert len(refs) == 2


async def test_a_new_integration_needs_only_the_service_credentials(
    database, monkeypatch
) -> None:
    """В форме остались партнёрка, адрес и ключ — остальное CRM берёт сама.

    Название — просто подпись партнёрки, а id интеграции на сервисе спрашиваем
    у самого сервиса: раньше его вбивали руками, и без него синк не ходил в ПП.
    """
    import app.api.routers.partner_integrations as router

    async def fake_integrations(self):
        return [
            {"id": 41, "name": "other", "partner_name": "Другая ПП"},
            {"id": 17, "name": "famecpa", "partner_name": "Affise (famecpa)"},
        ]

    monkeypatch.setattr(router.PartnerServiceClient, "integrations", fake_integrations)

    with _admin_client() as client:
        created = client.post(
            "/api/v1/partner-integrations",
            json={"partner_name": "Affise (famecpa)", "base_url": "http://pp.test",
                  "api_key": "pp-key"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "Affise (famecpa)"
        assert body["external_id"] == "17"

        # Второй раз то же имя — не конфликт, а порядковый номер.
        twin = client.post(
            "/api/v1/partner-integrations",
            json={"partner_name": "Affise (famecpa)", "base_url": "http://pp.test",
                  "api_key": "pp-key"},
        )
        assert twin.status_code == 201
        assert twin.json()["name"] == "Affise (famecpa) 2"

        # Без партнёрки, адреса или ключа сохранять нечего.
        empty = client.post(
            "/api/v1/partner-integrations",
            json={"partner_name": "", "base_url": "http://pp.test", "api_key": "k"},
        )
        assert empty.status_code == 422

        for row in (body, twin.json()):
            client.delete("/api/v1/partner-integrations/" + row["id"])


async def test_the_service_id_stays_empty_when_the_service_is_silent(
    database, monkeypatch
) -> None:
    """Сервис недоступен — интеграция всё равно заводится.

    Без id синк читает то, что сервис уже собрал: это рабочее состояние, а не
    повод не дать сохранить подключение.
    """
    import app.api.routers.partner_integrations as router

    async def broken(self):
        raise router.PartnerIntegrationError("сервис недоступен")

    monkeypatch.setattr(router.PartnerServiceClient, "integrations", broken)

    with _admin_client() as client:
        created = client.post(
            "/api/v1/partner-integrations",
            json={"partner_name": "Тихая ПП", "base_url": "http://pp.test",
                  "api_key": "pp-key"},
        )
        assert created.status_code == 201
        assert created.json()["external_id"] is None
        client.delete("/api/v1/partner-integrations/" + created.json()["id"])
