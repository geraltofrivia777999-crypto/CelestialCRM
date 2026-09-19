"""Media spend remains authoritative across imports, edits, tiers and summaries."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models import FinanceBook, FinanceBookDay, MediaRecord, Offer, OfferStatus, User
from app.services.finance_spend import pull_spend_to_books, refresh_for_record, refresh_workspace
from tests.test_finance_book import buyer_id  # noqa: F401
from tests.test_media_finance import _admin_client

YEAR, MONTH, DAY = 2037, 9, 5


@pytest.fixture
async def spend_case(buyer_id):  # noqa: F811
    async with SessionLocal() as db:
        user = await db.get(User, uuid.UUID(buyer_id))
        offer = Offer(workspace_id=user.workspace_id, name='Auto spend regression',
                      geo='US', status=OfferStatus.active)
        db.add(offer)
        await db.flush()
        record = MediaRecord(workspace_id=user.workspace_id, buyer_id=user.id,
                             offer_id=offer.id, record_date=date(YEAR, MONTH, DAY),
                             spend_calculated=Decimal('100'), source='manual')
        db.add(record)
        await db.commit()
        case = {'buyer': user.id, 'workspace': user.workspace_id, 'offer': offer.id, 'record': record.id}
    yield case
    async with SessionLocal() as db:
        await db.execute(delete(MediaRecord).where(MediaRecord.id == case['record']))
        await db.execute(delete(Offer).where(Offer.id == case['offer']))
        await db.commit()


async def _sync(db, case):
    return await pull_spend_to_books(db, case['workspace'], case['buyer'], YEAR, MONTH)


async def _day(db, case, tier='T1'):
    return await db.scalar(select(FinanceBookDay).join(FinanceBook).where(
        FinanceBook.buyer_id == case['buyer'], FinanceBook.year == YEAR,
        FinanceBook.month == MONTH, FinanceBook.tier == tier, FinanceBookDay.day == DAY,
    ))


def _url(case, tier='T1'):
    return f'/api/v1/finance/book?buyer_id={case["buyer"]}&year={YEAR}&month={MONTH}&tier={tier}'


def _payload(case, entry):
    return {'buyer_id': str(case['buyer']), 'year': YEAR, 'month': MONTH, 'tier': 'T1',
            'days': {str(DAY): entry}, 'offers': []}


async def test_creates_missing_book_and_repeated_sync_is_idempotent(spend_case):
    async with SessionLocal() as db:
        result = await _sync(db, spend_case)
        row = await _day(db, spend_case)
        assert result['written'] == 1
        assert row.media_spend == row.spend_buyer == Decimal('100')
        assert row.manual_spend is None
        assert (await _sync(db, spend_case))['written'] == 0
        await db.commit()


@pytest.mark.parametrize('remove', [False, True])
async def test_zero_or_removed_source_clears_imported_amount(spend_case, remove):
    async with SessionLocal() as db:
        await _sync(db, spend_case)
        record = await db.get(MediaRecord, spend_case['record'])
        if remove:
            await db.delete(record)
        else:
            record.spend_calculated = Decimal('0')
        await db.flush()
        await _sync(db, spend_case)
        row = await _day(db, spend_case)
        assert row.media_spend == row.spend_buyer == Decimal('0')
        await db.commit()


async def test_geo_change_moves_amount_without_duplicate_spend(spend_case):
    async with SessionLocal() as db:
        await _sync(db, spend_case)
        offer = await db.get(Offer, spend_case['offer'])
        offer.geo = 'BR'
        await refresh_workspace(db, spend_case['workspace'])
        assert (await _day(db, spend_case, 'T1')).spend_buyer == Decimal('0')
        assert (await _day(db, spend_case, 'T23')).spend_buyer == Decimal('100')
        offer.geo = None
        result = await _sync(db, spend_case)
        assert result['unassigned'] == Decimal('100')
        assert (await _day(db, spend_case, 'T23')).spend_buyer == Decimal('0')
        await db.commit()


async def test_reads_backfill_personal_book_and_all_summaries(spend_case):
    with _admin_client() as client:
        # Summary comes first: no financial book has been created manually.
        for scope in ('all', 'tier1'):
            response = client.get(f'/api/v1/finance/summary?year={YEAR}&month={MONTH}&scope={scope}')
            assert response.status_code == 200, response.text
            body = response.json()
            assert Decimal(str(body['cards']['spend'])) == Decimal('100')
            if scope == 'tier1':
                assert any(r['buyer_id'] == str(spend_case['buyer']) for r in body['buyers'])
        personal = client.get(_url(spend_case)).json()
        assert Decimal(personal['days'][str(DAY)]['media_spend']) == Decimal('100')
        overview = client.get(f'/api/v1/finance/buyer-overview?buyer_id={spend_case["buyer"]}&year={YEAR}&month={MONTH}').json()
        assert Decimal(str(overview['cards']['spend'])) == Decimal('100')


async def test_old_browser_cannot_overwrite_auto_spend_and_explicit_override_can_reset(spend_case):
    with _admin_client() as client:
        client.get(_url(spend_case))
        stale = _payload(spend_case, {'spend_buyer': '20', 'costs': '7'})
        response = client.put('/api/v1/finance/book', json=stale)
        assert response.status_code == 200, response.text
        day = response.json()['days'][str(DAY)]
        assert Decimal(day['spend_buyer']) == Decimal('100')
        assert day['manual_spend'] is None
        response = client.put('/api/v1/finance/book', json=_payload(spend_case, {'manual_spend': '50', 'costs': '7'}))
        assert Decimal(response.json()['days'][str(DAY)]['spend_buyer']) == Decimal('50')
    async with SessionLocal() as db:
        record = await db.get(MediaRecord, spend_case['record'])
        record.spend_calculated = Decimal('200')
        await refresh_for_record(db, spend_case['workspace'], spend_case['buyer'], record.record_date)
        row = await _day(db, spend_case)
        assert row.media_spend == Decimal('200')
        assert row.spend_buyer == Decimal('50')
        assert row.costs == Decimal('7')
        await db.commit()
    with _admin_client() as client:
        for manual, expected in [(None, '200'), ('0', '0'), (None, '200')]:
            response = client.put('/api/v1/finance/book', json=_payload(spend_case, {'manual_spend': manual}))
            assert response.status_code == 200, response.text
            assert Decimal(response.json()['days'][str(DAY)]['spend_buyer']) == Decimal(expected)


async def test_legacy_manual_value_is_preserved_until_explicit_reset(spend_case):
    async with SessionLocal() as db:
        book = FinanceBook(workspace_id=spend_case['workspace'], buyer_id=spend_case['buyer'], year=YEAR, month=MONTH, tier='T1')
        db.add(book)
        await db.flush()
        db.add(FinanceBookDay(book_id=book.id, day=DAY, spend_buyer=Decimal('75')))
        await db.flush()
        await _sync(db, spend_case)
        row = await _day(db, spend_case)
        assert row.manual_spend == row.spend_buyer == Decimal('75')
        assert row.media_spend == Decimal('100')
        await db.commit()
    with _admin_client() as client:
        response = client.put('/api/v1/finance/book', json=_payload(spend_case, {'manual_spend': None}))
        assert Decimal(response.json()['days'][str(DAY)]['spend_buyer']) == Decimal('100')


async def test_auto_change_updates_carry_into_following_month(spend_case):
    async with SessionLocal() as db:
        following = FinanceBook(workspace_id=spend_case['workspace'], buyer_id=spend_case['buyer'], year=YEAR, month=MONTH+1, tier='T1')
        db.add(following)
        await _sync(db, spend_case)
        assert following.prev_minus == Decimal('100')
        record = await db.get(MediaRecord, spend_case['record'])
        record.spend_calculated = Decimal('0')
        await _sync(db, spend_case)
        assert following.prev_minus == Decimal('0')
        await db.commit()


async def test_sync_failure_is_not_silently_swallowed(spend_case, monkeypatch):
    import app.services.finance_spend as service
    async def broken(*args, **kwargs):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(service, 'pull_spend_to_books', broken)
    async with SessionLocal() as db:
        with pytest.raises(RuntimeError, match='database unavailable'):
            await refresh_for_record(db, spend_case['workspace'], spend_case['buyer'], date(YEAR,MONTH,DAY))


async def test_unedited_manual_override_survives_another_browser_save(spend_case):
    with _admin_client() as client:
        first = client.put('/api/v1/finance/book', json=_payload(spend_case, {'manual_spend': '65'}))
        assert first.status_code == 200, first.text
        # A second tab changes only costs, having loaded before the override.
        stale = client.put('/api/v1/finance/book', json=_payload(spend_case, {'costs': '8'}))
        assert stale.status_code == 200, stale.text
        day = stale.json()['days'][str(DAY)]
        assert Decimal(day['manual_spend']) == Decimal('65')
        assert Decimal(day['spend_buyer']) == Decimal('65')
        assert Decimal(day['media_spend']) == Decimal('100')
        # Even an omitted day must not erase its explicit manual override.
        omitted = _payload(spend_case, {})
        omitted['days'] = {}
        again = client.put('/api/v1/finance/book', json=omitted)
        assert Decimal(again.json()['days'][str(DAY)]['manual_spend']) == Decimal('65')
