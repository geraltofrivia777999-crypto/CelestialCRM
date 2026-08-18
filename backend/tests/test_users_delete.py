import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    AuditEvent,
    FinanceBook,
    FinanceBookDay,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceRecord,
    FinanceServiceValue,
    FinanceSpendValue,
    FinanceTagDay,
    MediaRecord,
    MediaServiceValue,
    MediaSpendValue,
    Offer,
    OfferBuyer,
    OfferStatus,
    Role,
    Service,
    SpendProvider,
    User,
    UserParent,
    UserPreference,
)
from app.models import (
    Session as SessionModel,
)
from tests.test_media_finance import _admin_client, _fixture_ids

DOOMED = "doomed-buyer"
KEEPER = "doomed-subordinate"
PURGE_OFFER = "user-delete-cascade-offer"


async def _make_user(login: str, name: str) -> str:
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        role = await db.scalar(
            select(Role).where(Role.workspace_id == admin.workspace_id, Role.name == "Buyer")
        )
        user = User(
            workspace_id=admin.workspace_id,
            role_id=role.id,
            name=name,
            login=login,
            password_hash=hash_password("test-password"),
        )
        db.add(user)
        await db.commit()
        return str(user.id)


async def _cleanup_user_rows(db, user: User) -> None:
    """Best-effort teardown for the shared SQLite test database."""
    media_records = select(MediaRecord.id).where(MediaRecord.buyer_id == user.id)
    await db.execute(
        delete(MediaServiceValue).where(
            MediaServiceValue.media_record_id.in_(media_records)
        )
    )
    await db.execute(
        delete(MediaSpendValue).where(MediaSpendValue.media_record_id.in_(media_records))
    )
    await db.execute(delete(MediaRecord).where(MediaRecord.buyer_id == user.id))

    finance_records = select(FinanceRecord.id).where(FinanceRecord.buyer_id == user.id)
    await db.execute(
        delete(FinanceServiceValue).where(
            FinanceServiceValue.finance_record_id.in_(finance_records)
        )
    )
    await db.execute(
        delete(FinanceSpendValue).where(
            FinanceSpendValue.finance_record_id.in_(finance_records)
        )
    )
    await db.execute(delete(FinanceRecord).where(FinanceRecord.buyer_id == user.id))

    books = select(FinanceBook.id).where(FinanceBook.buyer_id == user.id)
    offers = select(FinanceBookOffer.id).where(FinanceBookOffer.book_id.in_(books))
    tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(offers))
    await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(tags)))
    await db.execute(delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(offers)))
    await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(books)))
    await db.execute(delete(FinanceBookDay).where(FinanceBookDay.book_id.in_(books)))
    await db.execute(delete(FinanceBook).where(FinanceBook.buyer_id == user.id))

    await db.execute(
        update(AuditEvent).where(AuditEvent.user_id == user.id).values(user_id=None)
    )
    await db.execute(
        delete(UserParent).where(
            (UserParent.user_id == user.id) | (UserParent.parent_id == user.id)
        )
    )
    await db.execute(delete(UserPreference).where(UserPreference.user_id == user.id))
    await db.execute(delete(OfferBuyer).where(OfferBuyer.user_id == user.id))
    await db.execute(delete(SessionModel).where(SessionModel.user_id == user.id))
    await db.delete(user)


@pytest.fixture
async def doomed_user(database):
    """A throwaway buyer. Whatever the test leaves behind is cleaned up here."""
    user_id = await _make_user(DOOMED, "Doomed Buyer")
    yield user_id
    async with SessionLocal() as db:
        for login in (DOOMED, KEEPER):
            user = await db.scalar(select(User).where(User.login == login))
            if not user:
                continue
            await _cleanup_user_rows(db, user)
        await db.execute(delete(Offer).where(Offer.external_id == PURGE_OFFER))
        await db.commit()


async def test_deleting_an_empty_user_removes_it(doomed_user) -> None:
    with _admin_client() as client:
        response = client.delete(f"/api/v1/users/{doomed_user}")
        assert response.status_code == 204
        assert client.get(f"/api/v1/users/{doomed_user}").status_code == 404

    async with SessionLocal() as db:
        assert await db.get(User, uuid.UUID(doomed_user)) is None


async def test_deleting_a_user_takes_its_preferences_with_it(doomed_user) -> None:
    async with SessionLocal() as db:
        db.add(
            UserPreference(
                user_id=uuid.UUID(doomed_user),
                key="mediaboard.display",
                value={"period": "day"},
            )
        )
        await db.commit()

    with _admin_client() as client:
        assert client.delete(f"/api/v1/users/{doomed_user}").status_code == 204

    async with SessionLocal() as db:
        left = await db.scalar(
            select(UserPreference).where(UserPreference.user_id == uuid.UUID(doomed_user))
        )
        assert left is None


async def test_a_user_with_media_records_is_kept(doomed_user) -> None:
    """The buyer's numbers must not disappear with the account."""
    _, offer_id = await _fixture_ids()
    async with SessionLocal() as db:
        offer = await db.get(Offer, uuid.UUID(offer_id))
        db.add(
            MediaRecord(
                workspace_id=offer.workspace_id,
                record_date=date(2026, 5, 4),
                buyer_id=uuid.UUID(doomed_user),
                offer_id=offer.id,
                revenue=Decimal("100"),
            )
        )
        await db.commit()

    with _admin_client() as client:
        response = client.delete(f"/api/v1/users/{doomed_user}")
        assert response.status_code == 409
        assert "Медиаборде" in response.json()["error"]["message"]

    async with SessionLocal() as db:
        assert await db.get(User, uuid.UUID(doomed_user)) is not None


async def test_a_user_with_a_finance_book_is_kept(doomed_user) -> None:
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        db.add(
            FinanceBook(
                workspace_id=admin.workspace_id,
                buyer_id=uuid.UUID(doomed_user),
                year=2026,
                month=5,
            )
        )
        await db.commit()

    with _admin_client() as client:
        response = client.delete(f"/api/v1/users/{doomed_user}")
        assert response.status_code == 409
        assert "финансовых книг" in response.json()["error"]["message"]


async def test_a_user_with_subordinates_is_kept(doomed_user) -> None:
    """Deleting a lead would silently detach its branch — so it is refused."""
    child_id = await _make_user(KEEPER, "Doomed Subordinate")
    async with SessionLocal() as db:
        db.add(
            UserParent(user_id=uuid.UUID(child_id), parent_id=uuid.UUID(doomed_user))
        )
        await db.commit()

    with _admin_client() as client:
        response = client.delete(f"/api/v1/users/{doomed_user}")
        assert response.status_code == 409
        assert "подчинённых" in response.json()["error"]["message"]


async def test_purge_removes_the_complete_user_graph(doomed_user) -> None:
    """Confirmed deletion removes owned data but keeps shared catalogs and users."""
    keeper_id = uuid.UUID(await _make_user(KEEPER, "Doomed Subordinate"))
    doomed_id = uuid.UUID(doomed_user)
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        fixture_offer = await db.scalar(select(Offer).where(Offer.workspace_id == admin.workspace_id))
        service = await db.scalar(
            select(Service).where(Service.workspace_id == admin.workspace_id)
        )
        provider = await db.scalar(
            select(SpendProvider).where(SpendProvider.workspace_id == admin.workspace_id)
        )
        assert fixture_offer is not None and service is not None and provider is not None

        offer = Offer(
            workspace_id=admin.workspace_id,
            connection_id=fixture_offer.connection_id,
            external_id=PURGE_OFFER,
            name="User Delete Cascade Offer",
            geo="DE",
            status=OfferStatus.working,
        )
        db.add(offer)
        await db.flush()
        db.add(OfferBuyer(offer_id=offer.id, user_id=doomed_id))

        media = MediaRecord(
            workspace_id=admin.workspace_id,
            record_date=date(2031, 1, 1),
            buyer_id=doomed_id,
            offer_id=offer.id,
            revenue=Decimal("100"),
        )
        keeper_media = MediaRecord(
            workspace_id=admin.workspace_id,
            record_date=date(2031, 1, 1),
            buyer_id=keeper_id,
            offer_id=offer.id,
            revenue=Decimal("200"),
        )
        db.add_all([media, keeper_media])
        await db.flush()
        media_service = MediaServiceValue(
            media_record_id=media.id,
            service_id=service.id,
            quantity=Decimal("3"),
        )
        media_spend = MediaSpendValue(
            media_record_id=media.id,
            provider_id=provider.id,
            base_amount=Decimal("25"),
        )
        db.add_all([media_service, media_spend])

        finance = FinanceRecord(
            workspace_id=admin.workspace_id,
            record_date=date(2031, 1, 1),
            buyer_id=doomed_id,
            offer_id=offer.id,
            import_key=f"purge-{uuid.uuid4()}",
        )
        db.add(finance)
        await db.flush()
        finance_service = FinanceServiceValue(
            finance_record_id=finance.id,
            service_id=service.id,
            quantity=Decimal("2"),
        )
        finance_spend = FinanceSpendValue(
            finance_record_id=finance.id,
            provider_id=provider.id,
            base_amount=Decimal("15"),
        )
        db.add_all([finance_service, finance_spend])

        book = FinanceBook(
            workspace_id=admin.workspace_id,
            buyer_id=doomed_id,
            year=2031,
            month=1,
        )
        second_book = FinanceBook(
            workspace_id=admin.workspace_id,
            buyer_id=doomed_id,
            year=2031,
            month=2,
        )
        db.add_all([book, second_book])
        await db.flush()
        book_day = FinanceBookDay(book_id=book.id, day=1, spend_buyer=Decimal("12"))
        book_offer = FinanceBookOffer(book_id=book.id, name="Private Offer")
        db.add_all([book_day, book_offer])
        await db.flush()
        book_tag = FinanceOfferTag(offer_id=book_offer.id, name="SOK")
        db.add(book_tag)
        await db.flush()
        tag_day = FinanceTagDay(tag_id=book_tag.id, day=1, deposits=Decimal("4"))
        db.add(tag_day)

        audit_event = AuditEvent(
            workspace_id=admin.workspace_id,
            user_id=doomed_id,
            event_type="test.user_action",
            description="Action by the doomed user",
        )
        session = SessionModel(
            user_id=doomed_id,
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        preference = UserPreference(
            user_id=doomed_id,
            key="purge-test",
            value={"enabled": True},
        )
        db.add_all(
            [
                audit_event,
                session,
                preference,
                UserParent(user_id=keeper_id, parent_id=doomed_id),
            ]
        )
        await db.commit()

        removed_ids = {
            User: doomed_id,
            MediaRecord: media.id,
            MediaServiceValue: media_service.id,
            MediaSpendValue: media_spend.id,
            FinanceRecord: finance.id,
            FinanceServiceValue: finance_service.id,
            FinanceSpendValue: finance_spend.id,
            FinanceBook: book.id,
            FinanceBookDay: book_day.id,
            FinanceBookOffer: book_offer.id,
            FinanceOfferTag: book_tag.id,
            FinanceTagDay: tag_day.id,
            SessionModel: session.id,
            UserPreference: preference.id,
        }
        second_book_id = second_book.id
        audit_id = audit_event.id
        offer_id = offer.id
        service_id = service.id
        provider_id = provider.id
        keeper_media_id = keeper_media.id

    with _admin_client() as client:
        blocked = client.delete(f"/api/v1/users/{doomed_user}")
        assert blocked.status_code == 409
        message = blocked.json()["error"]["message"]
        assert "записей в Медиаборде — 1" in message
        assert "строк в Финансах — 1" in message
        assert "финансовых книг — 2" in message
        assert "подчинённых — 1" in message

        removed = client.delete(f"/api/v1/users/{doomed_user}?purge=true")
        assert removed.status_code == 204

    async with SessionLocal() as db:
        for model, ident in removed_ids.items():
            assert await db.get(model, ident) is None
        assert await db.get(FinanceBook, second_book_id) is None

        kept_audit = await db.get(AuditEvent, audit_id)
        assert kept_audit is not None and kept_audit.user_id is None
        assert await db.get(User, keeper_id) is not None
        assert await db.get(MediaRecord, keeper_media_id) is not None
        assert await db.get(Service, service_id) is not None
        assert await db.get(SpendProvider, provider_id) is not None

        kept_offer = await db.get(Offer, offer_id)
        assert kept_offer is not None and kept_offer.status == OfferStatus.free
        assert await db.scalar(
            select(OfferBuyer).where(OfferBuyer.user_id == doomed_id)
        ) is None
        detached = await db.scalar(
            select(UserParent).where(
                (UserParent.user_id == doomed_id) | (UserParent.parent_id == doomed_id)
            )
        )
        assert detached is None


async def test_you_cannot_delete_yourself(database) -> None:
    with _admin_client() as client:
        me = client.get("/api/v1/auth/me").json()
        for suffix in ("", "?purge=true"):
            response = client.delete(f"/api/v1/users/{me['id']}{suffix}")
            assert response.status_code == 422

    async with SessionLocal() as db:
        assert await db.scalar(select(User).where(User.login == "admin")) is not None
