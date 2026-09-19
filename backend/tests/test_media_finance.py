import uuid
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.deps import has_full_access
from app.core.security import encrypt_secret
from app.main import app
from app.models import (
    CountryTier,
    FinanceRecord,
    IntegrationConnection,
    MediaRecord,
    MediaServiceValue,
    MediaSpendValue,
    Offer,
    OfferBuyer,
    Permission,
    Role,
    Service,
    SpendProvider,
    User,
)
from app.services.formulas import finance_import_key


async def _fixture_ids() -> tuple[str, str]:
    """A buyer and an offer usable by the analytics endpoints."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == admin.workspace_id
            )
        )
        if not connection:
            connection = IntegrationConnection(
                workspace_id=admin.workspace_id,
                name="Media fixture",
                base_url="https://tracker.example",
                api_key_encrypted=encrypt_secret("test-key"),
            )
            db.add(connection)
            await db.flush()
        offer = await db.scalar(
            select(Offer).where(Offer.workspace_id == admin.workspace_id)
        )
        if not offer:
            offer = Offer(
                workspace_id=admin.workspace_id,
                connection_id=connection.id,
                external_id="fixture-offer",
                name="Fixture Offer",
                geo="DE",
            )
            db.add(offer)
            await db.flush()
        assigned = await db.get(OfferBuyer, (offer.id, admin.id))
        if not assigned:
            db.add(OfferBuyer(offer_id=offer.id, user_id=admin.id))
        await db.commit()
        return str(admin.id), str(offer.id)


def _admin_client() -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": "test-password"},
    )
    assert response.status_code == 200
    return client


async def test_media_record_rejects_offer_not_linked_to_buyer(database) -> None:
    # Гарантируем подключение независимо от порядка запуска тестов.
    await _fixture_ids()
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = await db.scalar(select(IntegrationConnection).limit(1))
        rogue = Offer(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id="unassigned-media-offer",
            name="Unassigned Media Offer",
            group_name="SOMEONE ELSE",
            geo="DE",
        )
        db.add(rogue)
        await db.commit()
        rogue_id = rogue.id

    with _admin_client() as client:
        response = client.post(
            "/api/v1/media-records",
            json={
                "record_date": "2026-03-01",
                "buyer_id": str(admin.id),
                "offer_id": str(rogue_id),
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Оффер не привязан к выбранному баеру"
    async with SessionLocal() as db:
        await db.delete(await db.get(Offer, rogue_id))
        await db.commit()


async def test_media_upsert_preserves_calculated_spend(database) -> None:
    buyer_id, offer_id = await _fixture_ids()
    payload = {
        "record_date": "2026-03-01",
        "buyer_id": buyer_id,
        "offer_id": offer_id,
        "installs": 1000,
    }

    with _admin_client() as client:
        created = client.post("/api/v1/media-records", json=payload)
        assert created.status_code == 200
        record_id = created.json()["id"]

    async with SessionLocal() as db:
        record = await db.get(MediaRecord, uuid.UUID(record_id))
        record.spend_calculated = Decimal("125.5")
        await db.commit()

    # Re-saving the record from the modal must not reset the agents/payments spend.
    with _admin_client() as client:
        again = client.post(
            "/api/v1/media-records", json={**payload, "installs": 1200}
        )
        assert again.status_code == 200
        assert again.json()["created"] is False

    async with SessionLocal() as db:
        record = await db.get(MediaRecord, uuid.UUID(record_id))
        assert record.spend_calculated == Decimal("125.5000")
        assert record.installs == 1200
        assert set(record.manual_fields) == {"installs"}


async def test_clearing_a_media_field_unpins_it(database) -> None:
    buyer_id, offer_id = await _fixture_ids()
    payload = {
        "record_date": "2026-03-02",
        "buyer_id": buyer_id,
        "offer_id": offer_id,
        "ftd": 7,
        "revenue": "50",
    }

    with _admin_client() as client:
        created = client.post("/api/v1/media-records", json=payload)
        assert created.status_code == 200
        record_id = created.json()["id"]
        cleared = client.post(
            "/api/v1/media-records",
            json={**payload, "ftd": None},
        )
        assert cleared.status_code == 200

    async with SessionLocal() as db:
        record = await db.get(MediaRecord, uuid.UUID(record_id))
        assert record.ftd is None
        assert set(record.manual_fields) == {"revenue"}


async def test_media_upsert_leaves_out_fields_it_was_not_given(database) -> None:
    """The Медиаборд modal stopped sending the funnel metrics (ТЗ 3).

    An omitted field is not a cleared field: a manual save must not wipe what
    Keitaro synced. Only an explicit null clears — that is covered above.
    """
    buyer_id, offer_id = await _fixture_ids()
    payload = {
        "record_date": "2026-03-05",
        "buyer_id": buyer_id,
        "offer_id": offer_id,
        "installs": 500,
        "revenue": "75.5",
    }

    with _admin_client() as client:
        created = client.post("/api/v1/media-records", json=payload)
        assert created.status_code == 200
        record_id = created.json()["id"]
        # Exactly what the modal sends now: identity only.
        again = client.post(
            "/api/v1/media-records",
            json={
                "record_date": "2026-03-05",
                "buyer_id": buyer_id,
                "offer_id": offer_id,
                "source": "manual",
            },
        )
        assert again.status_code == 200
        assert again.json()["created"] is False

    async with SessionLocal() as db:
        record = await db.get(MediaRecord, uuid.UUID(record_id))
        assert record.installs == 500
        assert record.revenue == Decimal("75.5000")
        assert set(record.manual_fields) == {"installs", "revenue"}


async def test_media_values_only_replace_the_blocks_it_receives(database) -> None:
    """The modal edits agents/payments only, so services must survive (ТЗ 1, 2)."""
    buyer_id, offer_id = await _fixture_ids()
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        service = await db.scalar(
            select(Service).where(Service.workspace_id == admin.workspace_id)
        )
        provider = await db.scalar(
            select(SpendProvider).where(SpendProvider.workspace_id == admin.workspace_id)
        )
        assert service is not None and provider is not None
        service_id, provider_id = str(service.id), str(provider.id)

    with _admin_client() as client:
        created = client.post(
            "/api/v1/media-records",
            json={
                "record_date": "2026-03-06",
                "buyer_id": buyer_id,
                "offer_id": offer_id,
            },
        )
        record_id = created.json()["id"]
        seeded = client.put(
            f"/api/v1/media-records/{record_id}/values",
            json={
                "services": [{"service_id": service_id, "quantity": "20"}],
                "spend_providers": [{"provider_id": provider_id, "base_amount": "100"}],
            },
        )
        assert seeded.status_code == 200
        rent_before = Decimal(str(seeded.json()["rent"]))
        assert rent_before > 0

        # What the Медиаборд modal now sends: agents/payments, no services key.
        updated = client.put(
            f"/api/v1/media-records/{record_id}/values",
            json={"spend_providers": [{"provider_id": provider_id, "base_amount": "250"}]},
        )
        assert updated.status_code == 200
        # RENT still reported from the untouched service rows.
        assert Decimal(str(updated.json()["rent"])) == rent_before

    async with SessionLocal() as db:
        kept = (
            await db.execute(
                select(MediaServiceValue).where(
                    MediaServiceValue.media_record_id == uuid.UUID(record_id)
                )
            )
        ).scalars().all()
        assert len(kept) == 1
        assert kept[0].quantity == Decimal("20.0000")
        record = await db.get(MediaRecord, uuid.UUID(record_id))
        assert record.spend_calculated > Decimal("250")

    # An explicit empty list still clears a block.
    with _admin_client() as client:
        cleared = client.put(
            f"/api/v1/media-records/{record_id}/values",
            json={"services": []},
        )
        assert cleared.status_code == 200
        assert Decimal(str(cleared.json()["rent"])) == Decimal("0")

    async with SessionLocal() as db:
        remaining = (
            await db.execute(
                select(MediaServiceValue).where(
                    MediaServiceValue.media_record_id == uuid.UUID(record_id)
                )
            )
        ).scalars().all()
        assert remaining == []
        record = await db.get(MediaRecord, uuid.UUID(record_id))
        # Spend was not in that request, so it kept its value.
        assert record.spend_calculated > Decimal("250")


async def test_finance_export_reports_spend_override(database) -> None:
    buyer_id, offer_id = await _fixture_ids()
    record_date = date(2026, 4, 15)

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        db.add(
            FinanceRecord(
                workspace_id=admin.workspace_id,
                record_date=record_date,
                buyer_id=uuid.UUID(buyer_id),
                offer_id=uuid.UUID(offer_id),
                import_key=finance_import_key(
                    record_date, uuid.UUID(buyer_id), uuid.UUID(offer_id), None
                ),
                rent=Decimal("10"),
                spend=Decimal("100"),
                spend_override=Decimal("250"),
                revenue=Decimal("400"),
            )
        )
        await db.commit()

    with _admin_client() as client:
        listed = client.get(
            "/api/v1/finance-records",
            params={"date_from": "2026-04-15", "date_to": "2026-04-15"},
        )
        assert listed.status_code == 200
        assert Decimal(listed.json()["items"][0]["spend"]) == Decimal("250")

        exported = client.get(
            "/api/v1/exports/finance",
            params={"format": "csv", "date_from": "2026-04-15", "date_to": "2026-04-15"},
        )
        assert exported.status_code == 200
        row = exported.text.strip().splitlines()[1].split(",")

    # The export must agree with the table instead of reporting the raw calculation.
    assert Decimal(row[5]) == Decimal("250.0000")


async def test_full_access_is_not_keyed_on_the_role_name(database) -> None:
    async with SessionLocal() as db:
        admin = await db.scalar(
            select(User)
            .where(User.login == "admin")
            .execution_options(populate_existing=True)
        )
        permissions = list((await db.execute(select(Permission))).scalars())
        renamed = Role(
            workspace_id=admin.workspace_id,
            name="Главный администратор",
            data_scope="all",
            permissions=permissions,
        )
        db.add(renamed)
        await db.flush()
        renamed_id = renamed.id
        await db.commit()

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        admin.role_id = renamed_id
        await db.commit()

    async with SessionLocal() as db:
        from sqlalchemy.orm import selectinload

        admin = await db.scalar(
            select(User)
            .where(User.login == "admin")
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        assert admin.role.name == "Главный администратор"
        assert await has_full_access(db, admin) is True

async def test_board_spend_lands_in_the_book_of_its_tier(database) -> None:
    """Расход дня расходится по Tier1 и Tier2/3 так же, как офферы.

    Тир — это страна оффера по справочнику, и у баера под каждый тир своя
    таблица: значит, один и тот же день попадает в обе книги разными частями.
    """
    from app.models import FinanceBook, FinanceBookDay, MediaRecord, Offer, OfferStatus, User
    from app.services.finance_spend import pull_spend_to_books

    made = {"offers": [], "books": [], "records": []}
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        offers = [
            Offer(workspace_id=admin.workspace_id, name="Спенд US", geo="US",
                  status=OfferStatus.active),
            Offer(workspace_id=admin.workspace_id, name="Спенд BR", geo="BR",
                  status=OfferStatus.active),
            Offer(workspace_id=admin.workspace_id, name="Спенд без гео",
                  status=OfferStatus.active),
        ]
        db.add_all(offers)
        await db.flush()
        books = {
            tier: FinanceBook(workspace_id=admin.workspace_id, buyer_id=admin.id,
                              year=2031, month=9, tier=tier)
            for tier in ("T1", "T23")
        }
        db.add_all(books.values())
        await db.flush()
        for offer, spend in zip(offers, ("100", "40", "7"), strict=True):
            record = MediaRecord(
                workspace_id=admin.workspace_id, buyer_id=admin.id, offer_id=offer.id,
                record_date=date(2031, 9, 4), spend_calculated=Decimal(spend),
                source="manual",
            )
            db.add(record)
            made["records"].append(record)
        await db.commit()
        made["offers"] = [offer.id for offer in offers]
        made["books"] = [book.id for book in books.values()]
        made["records"] = [record.id for record in made["records"]]

        result = await pull_spend_to_books(db, admin.workspace_id, admin.id, 2031, 9)
        await db.commit()

        assert result["written"] == 2
        # Оффер без гео не приписан ни к одному тиру — он возвращается отдельно.
        assert result["unassigned"] == Decimal("7")
        for tier, expected in (("T1", "100"), ("T23", "40")):
            day = await db.scalar(
                select(FinanceBookDay).where(
                    FinanceBookDay.book_id == books[tier].id, FinanceBookDay.day == 4
                )
            )
            assert day.spend_buyer == Decimal(expected)

        # Второй проход ничего не меняет: значение перезаписывается, а не растёт.
        again = await pull_spend_to_books(db, admin.workspace_id, admin.id, 2031, 9)
        await db.commit()
        assert again["written"] == 0

    await _drop_spend_fixture(made)


async def _drop_spend_fixture(made: dict) -> None:
    """Убрать за собой ровно свои строки: база у тестов общая."""
    from sqlalchemy import delete as sql_delete

    from app.models import FinanceBook, FinanceBookDay, MediaRecord, Offer

    async with SessionLocal() as db:
        await db.execute(
            sql_delete(MediaRecord).where(MediaRecord.id.in_(made["records"]))
        )
        await db.execute(
            sql_delete(FinanceBookDay).where(FinanceBookDay.book_id.in_(made["books"]))
        )
        await db.execute(sql_delete(FinanceBook).where(FinanceBook.id.in_(made["books"])))
        await db.execute(sql_delete(Offer).where(Offer.id.in_(made["offers"])))
        await db.commit()


async def _own_offer(external_id: str, name: str, geo: str) -> str:
    """Оффер вне группы «Оффера»: только такие и видит доска.

    `_fixture_ids()` берёт первый попавшийся оффер воркспейса, а он может
    оказаться из группы одноимённого модуля — её Медиаборд не показывает, и
    тест бы падал в зависимости от того, что успели создать соседние тесты.
    """
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        offer = await db.scalar(
            select(Offer).where(
                Offer.workspace_id == admin.workspace_id,
                Offer.external_id == external_id,
            )
        )
        if not offer:
            connection = await db.scalar(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == admin.workspace_id
                )
            )
            offer = Offer(
                workspace_id=admin.workspace_id,
                connection_id=connection.id,
                external_id=external_id,
                name=name,
                geo=geo,
                group_name="Nutra",
            )
            db.add(offer)
            await db.flush()
        assigned = await db.get(OfferBuyer, (offer.id, admin.id))
        if not assigned:
            db.add(OfferBuyer(offer_id=offer.id, user_id=admin.id))
        await db.commit()
        return str(offer.id)


async def _second_offer_id() -> str:
    """Ещё один оффер того же воркспейса — чтобы было между чем делить."""
    return await _own_offer("day-spend-offer", "Day spend offer", "IT")


async def test_day_spend_is_split_evenly_between_the_offers_of_that_day(database) -> None:
    """Баер вводит расход на день — сервер раскладывает его по офферам дня."""
    buyer_id, first_offer = await _fixture_ids()
    second_offer = await _second_offer_id()
    day = "2026-04-01"

    with _admin_client() as client:
        for offer_id in (first_offer, second_offer):
            created = client.post(
                "/api/v1/media-records",
                json={"record_date": day, "buyer_id": buyer_id, "offer_id": offer_id},
            )
            assert created.status_code == 200
        spread = client.post(
            "/api/v1/media-records/day-spend",
            json={"record_date": day, "buyer_id": buyer_id, "spend": "100"},
        )
        assert spread.status_code == 200, spread.text
        assert spread.json()["records"] == 2

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        records = list(
            (
                await db.execute(
                    select(MediaRecord).where(
                        MediaRecord.workspace_id == admin.workspace_id,
                        MediaRecord.record_date == date(2026, 4, 1),
                        MediaRecord.buyer_id == uuid.UUID(buyer_id),
                    )
                )
            ).scalars()
        )
        assert len(records) == 2
        assert sum(record.spend_override for record in records) == Decimal("100")
        for record in records:
            assert record.spend_override == Decimal("50.0000")
            assert "spend_override" in record.manual_fields


async def test_day_spend_keeps_every_cent_when_the_split_is_uneven(database) -> None:
    """Сто на трёх — это 33.3334 и два раза по 33.3333, а не потерянный цент."""
    buyer_id, first_offer = await _fixture_ids()
    second_offer = await _second_offer_id()
    day = "2026-04-02"
    third_offer = await _own_offer("day-spend-third", "Third offer", "ES")

    with _admin_client() as client:
        for offer_id in (first_offer, second_offer, third_offer):
            client.post(
                "/api/v1/media-records",
                json={"record_date": day, "buyer_id": buyer_id, "offer_id": offer_id},
            )
        spread = client.post(
            "/api/v1/media-records/day-spend",
            json={"record_date": day, "buyer_id": buyer_id, "spend": "100"},
        )
        assert spread.status_code == 200

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        shares = [
            record.spend_override
            for record in (
                await db.execute(
                    select(MediaRecord).where(
                        MediaRecord.workspace_id == admin.workspace_id,
                        MediaRecord.record_date == date(2026, 4, 2),
                        MediaRecord.buyer_id == uuid.UUID(buyer_id),
                    )
                )
            ).scalars()
        ]
        assert len(shares) == 3
        assert sum(shares) == Decimal("100")


async def test_day_spend_by_agents_replaces_the_split_and_drops_the_override(database) -> None:
    """Расход по агентам ложится долями на офферы и снимает ручную фиксацию."""
    buyer_id, first_offer = await _fixture_ids()
    second_offer = await _second_offer_id()
    day = "2026-04-03"
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        provider = await db.scalar(
            select(SpendProvider).where(SpendProvider.workspace_id == admin.workspace_id)
        )
        assert provider is not None
        provider_id, commission = str(provider.id), provider.commission_pct

    with _admin_client() as client:
        for offer_id in (first_offer, second_offer):
            client.post(
                "/api/v1/media-records",
                json={"record_date": day, "buyer_id": buyer_id, "offer_id": offer_id},
            )
        client.post(
            "/api/v1/media-records/day-spend",
            json={"record_date": day, "buyer_id": buyer_id, "spend": "80"},
        )
        by_agents = client.post(
            "/api/v1/media-records/day-spend",
            json={
                "record_date": day,
                "buyer_id": buyer_id,
                "providers": [{"provider_id": provider_id, "base_amount": "60"}],
            },
        )
        assert by_agents.status_code == 200, by_agents.text

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        records = list(
            (
                await db.execute(
                    select(MediaRecord).where(
                        MediaRecord.workspace_id == admin.workspace_id,
                        MediaRecord.record_date == date(2026, 4, 3),
                        MediaRecord.buyer_id == uuid.UUID(buyer_id),
                    )
                )
            ).scalars()
        )
        assert len(records) == 2
        for record in records:
            assert record.spend_override is None
            assert "spend_override" not in record.manual_fields
            values = list(
                (
                    await db.execute(
                        select(MediaSpendValue).where(
                            MediaSpendValue.media_record_id == record.id
                        )
                    )
                ).scalars()
            )
            assert len(values) == 1
            assert values[0].base_amount == Decimal("30.0000")
        expected = (Decimal("30") * (commission / 100 + 1)).quantize(Decimal("0.0001"))
        assert all(record.spend_calculated == expected for record in records)


async def test_day_spend_needs_records_and_exactly_one_kind_of_input(database) -> None:
    buyer_id, _ = await _fixture_ids()
    with _admin_client() as client:
        empty_day = client.post(
            "/api/v1/media-records/day-spend",
            json={"record_date": "2026-04-20", "buyer_id": buyer_id, "spend": "10"},
        )
        assert empty_day.status_code == 422
        both = client.post(
            "/api/v1/media-records/day-spend",
            json={
                "record_date": "2026-04-01",
                "buyer_id": buyer_id,
                "spend": "10",
                "providers": [],
            },
        )
        assert both.status_code == 422


async def test_offer_spend_wins_over_the_day_split_and_moves_the_day_total(database) -> None:
    """День — это сумма офферов, поэтому правка оффера должна её менять.

    Расход дня ложится на офферы ручной фиксацией. Если потом кто-то правит
    расход одного оффера через агентов, фиксация обязана уйти: иначе введённая
    сумма не доходит до доски и день остаётся прежним.
    """
    buyer_id, _ = await _fixture_ids()
    first_offer = await _own_offer("day-link-offer", "Day link offer", "FR")
    second_offer = await _second_offer_id()
    day = "2026-04-10"

    with _admin_client() as client:
        ids = {}
        for offer_id in (first_offer, second_offer):
            created = client.post(
                "/api/v1/media-records",
                json={"record_date": day, "buyer_id": buyer_id, "offer_id": offer_id},
            )
            ids[offer_id] = created.json()["id"]
        client.post(
            "/api/v1/media-records/day-spend",
            json={"record_date": day, "buyer_id": buyer_id, "spend": "200"},
        )
        async with SessionLocal() as db:
            admin = await db.scalar(select(User).where(User.login == "admin"))
            provider = await db.scalar(
                select(SpendProvider).where(
                    SpendProvider.workspace_id == admin.workspace_id
                )
            )
            provider_id, commission = str(provider.id), provider.commission_pct
        changed = client.put(
            f"/api/v1/media-records/{ids[first_offer]}/values",
            json={
                "spend_providers": [{"provider_id": provider_id, "base_amount": "30"}]
            },
        )
        assert changed.status_code == 200, changed.text
        rows = client.get(
            "/api/v1/media-records/groups",
            params={"date_from": day, "date_to": day, "by_date": "true"},
        ).json()["groups"]

    expected_offer = (Decimal("30") * (commission / 100 + 1)).quantize(Decimal("0.0001"))
    by_offer = {row["offer_id"]: Decimal(str(row["spend"])) for row in rows}
    assert by_offer[first_offer] == expected_offer
    assert by_offer[second_offer] == Decimal("100.0000")
    # День — сумма офферов, отдельного числа для дня нет и рассинхрону взяться неоткуда.
    assert sum(by_offer.values()) == expected_offer + Decimal("100")


async def test_day_spend_can_be_limited_to_one_tier(database) -> None:
    """Расход дня можно отнести к тиру — и он не уедет в чужую книгу.

    У баера в финансах книга на каждый тир. Раскладка «на весь день» делит
    сумму между офферами обоих тиров поровну, и деньги, потраченные на Tier1,
    наполовину приезжали в книгу Tier2/3.
    """
    buyer_id, _ = await _fixture_ids()
    # DE — Tier1 по справочнику, IT/ES — Tier2/3.
    tier1_offer = await _own_offer("tier-split-de", "Tier1 offer", "DE")
    tier23_offer = await _own_offer("tier-split-br", "Tier2/3 offer", "BR")
    day = "2026-04-25"

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        await db.execute(
            CountryTier.__table__.delete().where(
                CountryTier.workspace_id == admin.workspace_id,
                CountryTier.code.in_(["DE", "BR"]),
            )
        )
        db.add(CountryTier(workspace_id=admin.workspace_id, code="DE", tier="T1"))
        db.add(CountryTier(workspace_id=admin.workspace_id, code="BR", tier="T23"))
        await db.commit()

    with _admin_client() as client:
        for offer_id in (tier1_offer, tier23_offer):
            client.post(
                "/api/v1/media-records",
                json={"record_date": day, "buyer_id": buyer_id, "offer_id": offer_id},
            )
        spread = client.post(
            "/api/v1/media-records/day-spend",
            json={
                "record_date": day,
                "buyer_id": buyer_id,
                "tier": "T1",
                "spend": "100",
            },
        )
        assert spread.status_code == 200, spread.text
        assert spread.json()["records"] == 1
        rows = client.get(
            "/api/v1/media-records/groups",
            params={"date_from": day, "date_to": day},
        ).json()["groups"]

    by_offer = {row["offer_id"]: Decimal(str(row["spend"])) for row in rows}
    assert by_offer[tier1_offer] == Decimal("100.0000")
    assert by_offer[tier23_offer] == Decimal("0.0000")


async def test_day_spend_for_a_tier_without_offers_is_refused(database) -> None:
    """Тир без записей за день делить не по чему — 422, а не тихий ноль."""
    buyer_id, _ = await _fixture_ids()
    tier1_offer = await _own_offer("tier-only-de", "Only Tier1", "DE")
    day = "2026-04-26"

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        await db.execute(
            CountryTier.__table__.delete().where(
                CountryTier.workspace_id == admin.workspace_id,
                CountryTier.code == "DE",
            )
        )
        db.add(CountryTier(workspace_id=admin.workspace_id, code="DE", tier="T1"))
        await db.commit()

    with _admin_client() as client:
        client.post(
            "/api/v1/media-records",
            json={"record_date": day, "buyer_id": buyer_id, "offer_id": tier1_offer},
        )
        refused = client.post(
            "/api/v1/media-records/day-spend",
            json={
                "record_date": day,
                "buyer_id": buyer_id,
                "tier": "T23",
                "spend": "50",
            },
        )
    assert refused.status_code == 422
    assert "Tier2/3" in refused.json()["error"]["message"]
