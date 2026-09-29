import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models import (
    FinanceRecord,
    IntegrationConnection,
    MediaRecord,
    MediaServiceValue,
    MediaSpendValue,
    Offer,
    Service,
    SpendProvider,
    Status,
    User,
)
from app.services.formulas import finance_import_key
from tests.test_media_finance import _admin_client

# The suite shares one database, so these rows live on their own offer and on dates no
# other test touches — otherwise the Keitaro sync tests would pick them up.
OFFER_EXTERNAL_ID = "board-groups-offer"
START = date(2026, 9, 1)
DAYS = 5

# SQLite has no NUMERIC type and hands these sums back as floats. Postgres computes them
# exactly; comparing at the API's own scale keeps the test honest on both.
SCALE = Decimal("0.0001")


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(SCALE)


@pytest.fixture
async def board_rows(database):
    """A buyer × offer with several days of media and finance rows behind it."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == admin.workspace_id
            )
        )
        # A connection created here has to go again: the Keitaro tests sync "the first
        # connection in the table", so a leftover one silently hijacks their run.
        created_connection = connection is None
        if created_connection:
            connection = IntegrationConnection(
                workspace_id=admin.workspace_id,
                name="Board groups fixture",
                base_url="https://tracker.example",
                api_key_encrypted=encrypt_secret("test-key"),
            )
            db.add(connection)
            await db.flush()
        connection_id = connection.id
        offer = Offer(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id=OFFER_EXTERNAL_ID,
            name="Board Groups Offer",
            geo="PL",
        )
        db.add(offer)
        await db.flush()
        service = await db.scalar(
            select(Service).where(Service.workspace_id == admin.workspace_id)
        )
        provider = await db.scalar(
            select(SpendProvider).where(SpendProvider.workspace_id == admin.workspace_id)
        )
        for offset in range(DAYS):
            record_date = START + timedelta(days=offset)
            record = MediaRecord(
                workspace_id=admin.workspace_id,
                record_date=record_date,
                buyer_id=admin.id,
                offer_id=offer.id,
                installs=100 + offset,
                registrations=20 + offset,
                ftd=3 + offset,
                revenue=Decimal("123.4567") * (offset + 1),
            )
            db.add(record)
            await db.flush()
            db.add(
                MediaServiceValue(
                    media_record_id=record.id,
                    service_id=service.id,
                    quantity=Decimal("100") + offset,
                )
            )
            db.add(
                MediaSpendValue(
                    media_record_id=record.id,
                    provider_id=provider.id,
                    base_amount=Decimal("11.1111") * (offset + 1),
                )
            )
            db.add(
                FinanceRecord(
                    workspace_id=admin.workspace_id,
                    record_date=record_date,
                    buyer_id=admin.id,
                    offer_id=offer.id,
                    import_key=finance_import_key(
                        record_date, admin.id, offer.id, f"link-{offset}"
                    ),
                    link=f"link-{offset}",
                    rent=Decimal("7.7777") * (offset + 1),
                    spend=Decimal("13.3333") * (offset + 1),
                    qual=Decimal("50.5"),
                    revenue=Decimal("321.7654") * (offset + 1),
                    salary=Decimal("9.99"),
                )
            )
        await db.commit()
        buyer_id, offer_id = str(admin.id), str(offer.id)

    yield buyer_id, offer_id

    # The suite shares one database and other tests assert on its overall contents
    # (the Keitaro sync test expects exactly one offer), so this leaves no trace.
    async with SessionLocal() as db:
        media_ids = (
            await db.execute(
                select(MediaRecord.id).where(MediaRecord.offer_id == uuid.UUID(offer_id))
            )
        ).scalars().all()
        if media_ids:
            await db.execute(
                delete(MediaServiceValue).where(
                    MediaServiceValue.media_record_id.in_(media_ids)
                )
            )
            await db.execute(
                delete(MediaSpendValue).where(
                    MediaSpendValue.media_record_id.in_(media_ids)
                )
            )
            await db.execute(delete(MediaRecord).where(MediaRecord.id.in_(media_ids)))
        await db.execute(
            delete(FinanceRecord).where(FinanceRecord.offer_id == uuid.UUID(offer_id))
        )
        await db.execute(delete(Offer).where(Offer.id == uuid.UUID(offer_id)))
        if created_connection:
            await db.execute(
                delete(IntegrationConnection).where(
                    IntegrationConnection.id == connection_id
                )
            )
        await db.commit()


def _period(offer_id: str, extra: dict | None = None) -> dict:
    params = {
        "date_from": START.isoformat(),
        "date_to": (START + timedelta(days=DAYS - 1)).isoformat(),
        "offer_id": offer_id,
    }
    params.update(extra or {})
    return params


def _totals(rows: list[dict], fields: tuple[str, ...]) -> dict[str, Decimal]:
    return {
        field: _q(sum(Decimal(str(row[field])) for row in rows if row[field] is not None))
        for field in fields
    }


def _split_totals(rows: list[dict], bucket: str, field: str) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in rows:
        for key, value in row[bucket].items():
            totals[key] = totals.get(key, Decimal("0")) + Decimal(str(value[field]))
    return {key: _q(value) for key, value in totals.items()}


async def test_media_groups_match_the_raw_records(board_rows) -> None:
    """The board reads its totals from the grouped endpoint, never from the records."""
    _buyer_id, offer_id = board_rows
    with _admin_client() as client:
        listed = client.get("/api/v1/media-records", params=_period(offer_id, {"limit": 1000}))
        grouped = client.get("/api/v1/media-records/groups", params=_period(offer_id))
        assert listed.status_code == 200
        assert grouped.status_code == 200
        items = listed.json()["items"]
        payload = grouped.json()

    assert payload["record_count"] == len(items) == DAYS

    fields = ("installs", "registrations", "ftd", "revenue", "rent", "spend")
    assert _totals(items, fields) == _totals(payload["groups"], fields)
    assert _split_totals(items, "services", "quantity") == _split_totals(
        payload["groups"], "services", "quantity"
    )
    assert _split_totals(items, "providers", "amount") == _split_totals(
        payload["groups"], "providers", "amount"
    )


async def test_media_board_hides_records_of_removed_keitaro_offer(board_rows) -> None:
    """История остаётся в базе, но выключенный трекером оффер исчезает с доски."""
    _buyer_id, offer_id = board_rows
    async with SessionLocal() as db:
        offer = await db.get(Offer, uuid.UUID(offer_id))
        offer.keitaro_state = Status.inactive
        await db.commit()

    with _admin_client() as client:
        listed = client.get("/api/v1/media-records", params=_period(offer_id))
        grouped = client.get("/api/v1/media-records/groups", params=_period(offer_id))

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert grouped.status_code == 200
    assert grouped.json()["groups"] == []
    assert grouped.json()["record_count"] == 0


async def test_finance_groups_match_the_raw_records(board_rows) -> None:
    _buyer_id, offer_id = board_rows
    with _admin_client() as client:
        listed = client.get(
            "/api/v1/finance-records", params=_period(offer_id, {"limit": 1000})
        )
        grouped = client.get("/api/v1/finance-records/groups", params=_period(offer_id))
        items = listed.json()["items"]
        payload = grouped.json()

    assert payload["record_count"] == len(items) == DAYS
    fields = ("qual", "rent", "spend", "revenue", "salary")
    assert _totals(items, fields) == _totals(payload["groups"], fields)


async def test_leaf_paging_never_repeats_or_drops_a_record(board_rows) -> None:
    """Lazy leaf loading walks pages by offset, so the ordering has to be total."""
    _buyer_id, offer_id = board_rows
    with _admin_client() as client:
        for endpoint in ("media-records", "finance-records"):
            collected: list[str] = []
            offset = 0
            while True:
                page = client.get(
                    f"/api/v1/{endpoint}",
                    params=_period(offer_id, {"limit": 2, "offset": offset}),
                )
                assert page.status_code == 200
                batch = page.json()["items"]
                if not batch:
                    break
                collected.extend(item["id"] for item in batch)
                offset += len(batch)
            assert len(collected) == DAYS, endpoint
            assert len(set(collected)) == DAYS, endpoint


async def test_groups_carry_the_filters_the_leaves_need(board_rows) -> None:
    """Every grouped dimension must come back as a value the leaf request can filter on."""
    buyer_id, offer_id = board_rows
    with _admin_client() as client:
        grouped = client.get("/api/v1/media-records/groups", params=_period(offer_id))
        group = next(
            item for item in grouped.json()["groups"] if item["buyer_id"] == buyer_id
        )
        leaves = client.get(
            "/api/v1/media-records",
            params=_period(
                offer_id,
                {"buyer_id": group["buyer_id"], "geo": group["geo"], "limit": 1000},
            ),
        )
        assert leaves.status_code == 200
        items = leaves.json()["items"]

    assert group["geo"] == "PL"
    assert len(items) == group["records"]
    assert {item["partner_id"] for item in items} == {group["partner_id"]}


async def test_media_filters_accept_several_values(board_rows) -> None:
    """Медиаборд спрашивает не про одного баера и не про одно GEO сразу.

    Набор значений уходит повторяющимся параметром, поэтому здесь проверяется
    и то, что лишнее значение в наборе ничего не отрезает, и то, что само
    сужение работает: одного чужого GEO хватает, чтобы выдача опустела.
    """
    _buyer_id, offer_id = board_rows
    stranger = str(uuid.uuid4())
    with _admin_client() as client:
        one = client.get("/api/v1/media-records/groups", params=_period(offer_id))
        several = client.get(
            "/api/v1/media-records/groups",
            params=_period(offer_id, {"offer_id": [offer_id, stranger]}),
        )
        both_geo = client.get(
            "/api/v1/media-records/groups",
            params=_period(offer_id, {"geo": ["PL", "DE"]}),
        )
        foreign_geo = client.get(
            "/api/v1/media-records/groups", params=_period(offer_id, {"geo": "DE"})
        )
        leaves = client.get(
            "/api/v1/media-records",
            params=_period(offer_id, {"offer_id": [offer_id, stranger], "limit": 1000}),
        )

    assert several.status_code == 200
    assert several.json() == one.json()
    assert both_geo.json() == one.json()
    assert foreign_geo.json()["record_count"] == 0
    assert len(leaves.json()["items"]) == DAYS


def test_export_rows_repeat_what_the_board_shows() -> None:
    """Профит, ROI и CPD в выгрузке считаются той же формулой, что в таблице."""
    from app.api.routers.analytics import _media_export_rows

    rows = _media_export_rows([
        {"buyer": "EVS", "tier": "T1", "geo": "AR", "partner": "Growe", "offer": "2xBet",
         "records": 3, "installs": 100, "registrations": 40, "ftd": 10,
         "spend": "50", "revenue": "150"},
    ])

    assert rows[0][:5] == ["EVS", "Tier1", "AR", "Growe", "2xBet"]
    assert rows[0][11] == Decimal("100")      # profit = revenue - spend
    assert rows[0][12] == Decimal("200.00")   # roi, %
    assert rows[0][13] == Decimal("5.00")     # cpd = spend / ftd


def test_export_total_sums_every_column() -> None:
    """Строка «Общая» повторяет итог доски, а не пересчитывает его иначе."""
    from app.api.routers.analytics import _media_export_rows, _media_export_total

    rows = _media_export_rows([
        {"buyer": "EVS", "tier": "T1", "geo": "AR", "partner": "P", "offer": "A",
         "records": 3, "installs": 100, "registrations": 40, "ftd": 10,
         "spend": "50", "revenue": "150"},
        {"buyer": "LUKA", "tier": "T23", "geo": "PE", "partner": "P", "offer": "B",
         "records": 2, "installs": 50, "registrations": 20, "ftd": 5,
         "spend": "25", "revenue": "60"},
    ])

    total = _media_export_total(rows)

    assert total[0] == "Общая"
    assert total[5:9] == [5, 150, 60, 15]
    assert total[9] == Decimal("75")
    assert total[11] == Decimal("135")


def test_a_spendless_row_has_no_roi() -> None:
    """Деление на ноль в выгрузке — пустая ячейка, а не бесконечность."""
    from app.api.routers.analytics import _media_export_rows

    rows = _media_export_rows([
        {"buyer": "EVS", "tier": "unassigned", "geo": None, "partner": None,
         "offer": "A", "records": 1, "installs": 0, "registrations": 0, "ftd": 0,
         "spend": "0", "revenue": "0"},
    ])

    assert rows[0][1] == "Без тира"
    assert rows[0][12] == ""
    assert rows[0][13] == ""


async def test_by_date_splits_groups_into_days(board_rows) -> None:
    """Уровень «Дата» на доске — это отдельная строка на каждый день.

    Разбивка приходит только по запросу (`by_date`), поэтому проверяется и то,
    что без него строка по-прежнему одна, и то, что с ним суммы по дням дают
    ровно те же итоги — вместе с разбивками по сервисам и платёжкам, которые
    иначе легли бы не в тот день.
    """
    _buyer_id, offer_id = board_rows
    with _admin_client() as client:
        listed = client.get(
            "/api/v1/media-records", params=_period(offer_id, {"limit": 1000})
        )
        whole = client.get("/api/v1/media-records/groups", params=_period(offer_id))
        daily = client.get(
            "/api/v1/media-records/groups", params=_period(offer_id, {"by_date": "true"})
        )
        assert daily.status_code == 200
        items = listed.json()["items"]
        one_row = whole.json()["groups"]
        rows = daily.json()["groups"]

    assert len(one_row) == 1
    assert one_row[0]["date"] is None
    assert len(rows) == DAYS
    assert [row["date"] for row in rows] == sorted(item["record_date"] for item in items)

    fields = ("installs", "registrations", "ftd", "revenue", "rent", "spend")
    assert _totals(rows, fields) == _totals(one_row, fields)
    assert _split_totals(rows, "services", "quantity") == _split_totals(
        one_row, "services", "quantity"
    )
    assert _split_totals(rows, "providers", "amount") == _split_totals(
        one_row, "providers", "amount"
    )

    # Строка дня должна совпасть с записью этого дня, иначе разбивки склеились.
    by_day = {item["record_date"]: item for item in items}
    for row in rows:
        assert row["records"] == 1
        record = by_day[row["date"]]
        for field in fields:
            assert _q(Decimal(str(row[field]))) == _q(Decimal(str(record[field])))
        assert _split_totals([row], "providers", "amount") == _split_totals(
            [record], "providers", "amount"
        )


async def test_records_carry_the_tier_the_board_groups_by(board_rows) -> None:
    """Уровень «Тир» на доске должен узнавать свои записи.

    Тир не фильтруется параметром запроса — доска сверяет его на своей стороне,
    поэтому запись без поля `tier` в ветку тира не попадает и ветка выглядит
    пустой, хотя группа над ней показывает суммы.
    """
    _buyer_id, offer_id = board_rows
    with _admin_client() as client:
        listed = client.get(
            "/api/v1/media-records", params=_period(offer_id, {"limit": 1000})
        )
        finance = client.get(
            "/api/v1/finance-records", params=_period(offer_id, {"limit": 1000})
        )
        grouped = client.get("/api/v1/media-records/groups", params=_period(offer_id))
        items = listed.json()["items"]
        finance_items = finance.json()["items"]
        group = grouped.json()["groups"][0]

    assert {item["tier"] for item in items} == {group["tier"]}
    assert {item["tier"] for item in finance_items} == {group["tier"]}
