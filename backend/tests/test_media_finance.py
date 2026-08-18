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
    FinanceRecord,
    IntegrationConnection,
    MediaRecord,
    MediaServiceValue,
    Offer,
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
