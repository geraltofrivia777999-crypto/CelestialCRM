import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import encrypt_secret, hash_password
from app.main import app
from app.models import (
    IntegrationConnection,
    MediaRecord,
    Offer,
    OfferBuyer,
    OfferLead,
    OfferStatus,
    Partner,
    Role,
    Status,
    User,
)
from app.services.geo import normalize_geo
from app.services.keitaro_sync import _resolve_offer_partner
from tests.test_media_finance import _admin_client

# The suite shares one database and the Keitaro tests assert on its overall
# contents, so everything here lives on its own offers and is removed again.
IN_GROUP = "offers-module-in-group"
IN_GROUP_LOWER = "offers-module-in-group-lowercase"
OUT_OF_GROUP = "offers-module-out-of-group"
SCOPED_BUYER = "offers-module-scoped-buyer"
SPEND_SCOPED_BUYER = "spend-module-scoped-buyer"


@pytest.fixture
async def offer_rows(database):
    """Three offers: two inside the Keitaro OFFERS group, one outside it."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == admin.workspace_id
            )
        )
        # A connection created here has to go again: the Keitaro tests sync "the
        # first connection in the table", so a leftover one hijacks their run.
        created_connection = connection is None
        if created_connection:
            connection = IntegrationConnection(
                workspace_id=admin.workspace_id,
                name="Offers fixture",
                base_url="https://tracker.example",
                api_key_encrypted=encrypt_secret("test-key"),
            )
            db.add(connection)
            await db.flush()
        connection_id = connection.id
        offers = {
            IN_GROUP: Offer(
                workspace_id=admin.workspace_id,
                connection_id=connection.id,
                external_id=IN_GROUP,
                name="AAA Offers Module In Group",
                geo="INDIA",
                group_name=settings.keitaro_offers_group,
            ),
            IN_GROUP_LOWER: Offer(
                workspace_id=admin.workspace_id,
                connection_id=connection.id,
                external_id=IN_GROUP_LOWER,
                name="BBB Offers Module In Group Lowercase",
                geo="DE",
                group_name=settings.keitaro_offers_group.lower(),
            ),
            OUT_OF_GROUP: Offer(
                workspace_id=admin.workspace_id,
                connection_id=connection.id,
                external_id=OUT_OF_GROUP,
                name="CCC Offers Module Out Of Group",
                geo="PL",
                group_name="Nutra",
            ),
        }
        db.add_all(list(offers.values()))
        await db.commit()
        ids = {key: str(offer.id) for key, offer in offers.items()}
        admin_id = str(admin.id)

    yield ids, admin_id

    async with SessionLocal() as db:
        offer_ids = [uuid.UUID(value) for value in ids.values()]
        await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id.in_(offer_ids)))
        await db.execute(delete(OfferLead).where(OfferLead.offer_id.in_(offer_ids)))
        await db.execute(delete(Offer).where(Offer.id.in_(offer_ids)))
        if created_connection:
            await db.execute(
                delete(IntegrationConnection).where(
                    IntegrationConnection.id == connection_id
                )
            )
        await db.commit()


def _names(payload: dict) -> set[str]:
    return {item["name"] for item in payload["items"]}


async def test_offers_module_is_limited_to_the_keitaro_offers_group(offer_rows) -> None:
    with _admin_client() as client:
        scoped = client.get("/api/v1/offers?only_offers_group=true&limit=500")
        everything = client.get("/api/v1/offers?limit=500")

    assert scoped.status_code == 200
    scoped_names = _names(scoped.json())
    assert "AAA Offers Module In Group" in scoped_names
    # The group name is matched case-insensitively — Keitaro lets people type it.
    assert "BBB Offers Module In Group Lowercase" in scoped_names
    assert "CCC Offers Module Out Of Group" not in scoped_names
    # Other modules still need every offer, so the scope is opt-in.
    assert "CCC Offers Module Out Of Group" in _names(everything.json())


async def test_offer_geo_is_reported_as_alpha2(offer_rows) -> None:
    with _admin_client() as client:
        response = client.get("/api/v1/offers?only_offers_group=true&limit=500")

    assert response.status_code == 200
    geos = {
        item["name"]: item["geo"] for item in response.json()["items"]
    }
    # Stored as "INDIA" by an older sync; the API answers with the code anyway.
    assert geos["AAA Offers Module In Group"] == "IN"
    assert geos["BBB Offers Module In Group Lowercase"] == "DE"


def test_normalize_geo_understands_names_codes_and_lists() -> None:
    assert normalize_geo("INDIA") == "IN"
    assert normalize_geo("India") == "IN"
    assert normalize_geo("IND") == "IN"
    assert normalize_geo("in") == "IN"
    assert normalize_geo("Индия") == "IN"
    assert normalize_geo(["DE", "AT"]) == "DE"
    assert normalize_geo("DE,AT") == "DE"
    assert normalize_geo("United Kingdom") == "GB"
    assert normalize_geo("worldwide") == "WW"
    assert normalize_geo("") is None
    assert normalize_geo(None) is None
    # Unknown values stay visible instead of being dropped.
    assert normalize_geo("Atlantis") == "ATLANTIS"


async def test_star_can_be_toggled_and_sorts_first(offer_rows) -> None:
    ids, _ = offer_rows
    with _admin_client() as client:
        starred = client.patch(
            f"/api/v1/offers/{ids[IN_GROUP_LOWER]}/star",
            json={"is_starred": True},
        )
        assert starred.status_code == 200
        assert starred.json()["is_starred"] is True
        listing = client.get("/api/v1/offers?only_offers_group=true&limit=500")
        rows = listing.json()["items"]
        positions = {row["id"]: index for index, row in enumerate(rows)}
        # "BBB" sorts after "AAA" by name, so being first can only come from the star.
        assert positions[ids[IN_GROUP_LOWER]] < positions[ids[IN_GROUP]]

        cleared = client.patch(
            f"/api/v1/offers/{ids[IN_GROUP_LOWER]}/star",
            json={"is_starred": False},
        )
        assert cleared.json()["is_starred"] is False
        rows = client.get("/api/v1/offers?only_offers_group=true&limit=500").json()["items"]
        positions = {row["id"]: index for index, row in enumerate(rows)}
        assert positions[ids[IN_GROUP]] < positions[ids[IN_GROUP_LOWER]]

        only_starred = client.get("/api/v1/offers?starred=true&limit=500")
        assert ids[IN_GROUP_LOWER] not in {row["id"] for row in only_starred.json()["items"]}


async def test_buyers_drive_free_and_working_status(offer_rows) -> None:
    ids, admin_id = offer_rows
    offer_id = ids[IN_GROUP]

    with _admin_client() as client:
        assigned = client.put(
            f"/api/v1/offers/{offer_id}/buyers",
            json={"buyer_ids": [admin_id]},
        )
        assert assigned.status_code == 200
        # "Не занят" is the state before buyers exist, so the first one starts work.
        assert assigned.json()["status"] == "working"

        cleared = client.put(f"/api/v1/offers/{offer_id}/buyers", json={"buyer_ids": []})
        assert cleared.json()["status"] == "free"

    async with SessionLocal() as db:
        offer = await db.get(Offer, uuid.UUID(offer_id))
        assert offer.status == OfferStatus.free


async def test_hold_and_stop_survive_every_reassignment(offer_rows) -> None:
    """Холд и Стоп ставит человек — назначение их не перебивает и не снимает."""
    ids, admin_id = offer_rows
    offer_id = ids[IN_GROUP]

    with _admin_client() as client:
        held = client.patch(f"/api/v1/offers/{offer_id}/status", json={"status": "hold"})
        assert held.json()["status"] == "hold"

        assigned = client.put(
            f"/api/v1/offers/{offer_id}/leads", json={"lead_ids": [admin_id]}
        )
        assert assigned.json()["status"] == "hold"
        working = client.put(
            f"/api/v1/offers/{offer_id}/buyers", json={"buyer_ids": [admin_id]}
        )
        assert working.json()["status"] == "hold"
        # И снятие последнего баера тоже: оффер остановлен по решению команды,
        # а не потому, что на нём никого нет.
        emptied = client.put(f"/api/v1/offers/{offer_id}/buyers", json={"buyer_ids": []})
        assert emptied.json()["status"] == "hold"

        released = client.patch(
            f"/api/v1/offers/{offer_id}/status", json={"status": "free"}
        )
        assert released.json()["status"] == "free"


async def test_the_offer_walks_from_free_through_the_lead_to_the_buyers(
    offer_rows,
) -> None:
    """Не занят → Активен у ТЛа → В работе у баеров, и обратно тем же путём."""
    ids, admin_id = offer_rows
    offer_id = ids[IN_GROUP]

    with _admin_client() as client:
        to_lead = client.put(
            f"/api/v1/offers/{offer_id}/leads", json={"lead_ids": [admin_id]}
        )
        assert to_lead.status_code == 200
        assert to_lead.json()["status"] == "active"

        to_buyers = client.put(
            f"/api/v1/offers/{offer_id}/buyers", json={"buyer_ids": [admin_id]}
        )
        assert to_buyers.json()["status"] == "working"

        # Тимлид снял баеров — оффер вернулся к нему, а не в общий пул.
        back = client.put(f"/api/v1/offers/{offer_id}/buyers", json={"buyer_ids": []})
        assert back.json()["status"] == "active"

        free = client.put(f"/api/v1/offers/{offer_id}/leads", json={"lead_ids": []})
        assert free.json()["status"] == "free"

        listing = client.get(f"/api/v1/offers?lead_id={admin_id}&limit=500")
        assert offer_id not in {row["id"] for row in listing.json()["items"]}


async def test_a_manual_offer_is_created_edited_and_removed(offer_rows) -> None:
    """Оффер заводится руками: у него нет ни трекера, ни внешнего id."""
    _, admin_id = offer_rows
    with _admin_client() as client:
        created = client.post(
            "/api/v1/offers",
            json={
                "name": "Offers Module Manual",
                "geo": "Германия",
                "cap": "300 FTD в день",
                "lead_ids": [admin_id],
            },
        )
        assert created.status_code == 201
        # Назначенный тимлид сразу переводит оффер в «Активен».
        assert created.json()["status"] == "active"
        offer_id = created.json()["id"]

        try:
            manual = client.get("/api/v1/offers?manual=true&limit=500").json()["items"]
            row = next(item for item in manual if item["id"] == offer_id)
            # Название страны из формы приводится к коду, как и при синхронизации.
            assert row["geo"] == "DE"
            assert row["cap"] == "300 FTD в день"
            assert row["is_manual"] is True
            assert [lead["id"] for lead in row["leads"]] == [admin_id]
            # Синхронизированные строки в этот список не попадают.
            assert all(item["is_manual"] for item in manual)

            updated = client.put(
                f"/api/v1/offers/{offer_id}",
                json={
                    "name": "Offers Module Manual v2",
                    "cap": "",
                    "lead_ids": [admin_id],
                    "buyer_ids": [admin_id],
                },
            )
            assert updated.json()["status"] == "working"
        finally:
            removed = client.delete(f"/api/v1/offers/{offer_id}")
            assert removed.status_code == 200

    async with SessionLocal() as db:
        assert await db.get(Offer, uuid.UUID(offer_id)) is None


async def test_kpi_and_comment_survive_the_round_trip(offer_rows) -> None:
    """Длинный текст оффера: в таблице его нет, но он не должен теряться."""
    _, admin_id = offer_rows
    with _admin_client() as client:
        offer_id = client.post(
            "/api/v1/offers",
            json={
                "name": "Offers Module KPI",
                "kpi": "FTD от 25$, апрув от 40%",
                "comment": "Партнёрка режет трафик\nс Android 9 и ниже",
            },
        ).json()["id"]
        try:
            row = next(
                item
                for item in client.get("/api/v1/offers?manual=true&limit=500").json()["items"]
                if item["id"] == offer_id
            )
            assert row["kpi"] == "FTD от 25$, апрув от 40%"
            assert "Android 9" in row["comment"]

            client.put(
                f"/api/v1/offers/{offer_id}",
                json={"name": "Offers Module KPI", "kpi": "", "comment": "только это"},
            )
            row = next(
                item
                for item in client.get("/api/v1/offers?manual=true&limit=500").json()["items"]
                if item["id"] == offer_id
            )
            # Пустая строка — это «стёрли», а не «оставили как было».
            assert row["kpi"] is None
            assert row["comment"] == "только это"
        finally:
            client.delete(f"/api/v1/offers/{offer_id}")


async def test_a_synced_offer_cannot_be_edited_or_deleted(offer_rows) -> None:
    """Имя и GEO синхронизированной строки принадлежат трекеру."""
    ids, _ = offer_rows
    offer_id = ids[IN_GROUP]
    with _admin_client() as client:
        edited = client.put(f"/api/v1/offers/{offer_id}", json={"name": "Hijacked"})
        assert edited.status_code == 409
        assert client.delete(f"/api/v1/offers/{offer_id}").status_code == 409

    async with SessionLocal() as db:
        offer = await db.get(Offer, uuid.UUID(offer_id))
        assert offer.name == "AAA Offers Module In Group"


async def test_the_offer_reference_lists_keitaro_geos_and_partners(offer_rows) -> None:
    """GEO и партнёрки для формы берутся из того, что пришло из Keitaro."""
    with _admin_client() as client:
        reference = client.get("/api/v1/offers/reference")

    assert reference.status_code == 200
    payload = reference.json()
    # "INDIA" лежит в базе как есть — в списке она уже кодом.
    assert "IN" in payload["geos"] and "DE" in payload["geos"]
    # В форме — все страны, даже те, по которым ещё не было ни одного оффера.
    countries = {row["code"]: row for row in payload["countries"]}
    assert countries["VE"]["ru"] == "Венесуэла"
    assert "test" not in payload["statuses"]
    assert payload["statuses"][0] == "active"
    assert isinstance(payload["partners"], list)


async def test_manual_partner_can_be_created_and_used_without_keitaro(database) -> None:
    """A new network need not exist in Keitaro before the first CRM offer."""
    partner_id = None
    offer_id = None
    try:
        with _admin_client() as client:
            first = client.post("/api/v1/partners", json={"name": "  Huffson   Test  "})
            assert first.status_code == 200
            partner_id = first.json()["id"]
            assert first.json()["name"] == "Huffson Test"

            repeated = client.post("/api/v1/partners", json={"name": "huffson test"})
            assert repeated.status_code == 200
            assert repeated.json()["id"] == partner_id

            reference = client.get("/api/v1/offers/reference")
            assert reference.status_code == 200
            assert any(row["id"] == partner_id for row in reference.json()["partners"])

            created = client.post(
                "/api/v1/offers",
                json={"name": "Manual partner offer", "partner_id": partner_id},
            )
            assert created.status_code == 201
            offer_id = created.json()["id"]

        async with SessionLocal() as db:
            partner = await db.get(Partner, uuid.UUID(partner_id))
            offer = await db.get(Offer, uuid.UUID(offer_id))
            assert partner.connection_id is None
            assert partner.status == Status.active
            assert offer.partner_id == partner.id
    finally:
        if offer_id:
            with _admin_client() as client:
                client.delete(f"/api/v1/offers/{offer_id}")
        if partner_id:
            async with SessionLocal() as db:
                await db.execute(delete(Partner).where(Partner.id == uuid.UUID(partner_id)))
                await db.commit()


async def test_offer_partner_falls_back_to_the_name_on_the_keitaro_row(
    offer_rows,
) -> None:
    ids, _ = offer_rows
    async with SessionLocal() as db:
        offer = await db.get(Offer, uuid.UUID(ids[IN_GROUP]))
        config = {"id": offer.connection_id, "workspace_id": offer.workspace_id}
        # No affiliate_network_id at all: the tracker only names the network.
        partner_id = await _resolve_offer_partner(
            db,
            config,
            {"affiliate_network": {"name": "Offers Module Network"}},
            {},
        )
        assert partner_id is not None
        # The same name resolves to the same partner instead of piling up rows.
        again = await _resolve_offer_partner(
            db,
            config,
            {"affiliate_network_name": "offers module network"},
            {},
        )
        assert again == partner_id
        assert await _resolve_offer_partner(db, config, {}, {}) is None
        await db.execute(delete(Partner).where(Partner.id == partner_id))
        await db.commit()


async def test_mediaboard_offers_drop_the_offers_group(offer_rows) -> None:
    """Группа «Оффера» — витрина одноимённого модуля, в Медиаборде её нет."""
    with _admin_client() as client:
        response = client.get("/api/v1/offers?exclude_offers_group=true&limit=500")

    names = _names(response.json())
    assert "AAA Offers Module In Group" not in names
    assert "BBB Offers Module In Group Lowercase" not in names
    assert "CCC Offers Module Out Of Group" in names


async def test_mediaboard_offers_hide_removed_keitaro_rows(offer_rows) -> None:
    """Удалённый в Keitaro оффер хранится для истории, но в фильтр не попадает."""
    ids, _ = offer_rows
    async with SessionLocal() as db:
        offer = await db.get(Offer, uuid.UUID(ids[OUT_OF_GROUP]))
        offer.keitaro_state = Status.inactive
        await db.commit()

    with _admin_client() as client:
        response = client.get("/api/v1/offers?exclude_offers_group=true&limit=500")

    assert response.status_code == 200
    assert "CCC Offers Module Out Of Group" not in _names(response.json())


async def test_offers_narrow_to_what_the_buyer_was_given(offer_rows) -> None:
    """«Оффера этого баера» — только назначенные ему.

    Группа Keitaro видимость больше не расширяет: по ней баеру показывались
    офферы всей группы, а не выданные лично, и «свои офферы» означало не то,
    что человек ожидает увидеть.
    """
    ids, _ = offer_rows
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        role = await db.scalar(
            select(Role).where(Role.workspace_id == admin.workspace_id, Role.name == "Buyer")
        )
        buyer = User(
            workspace_id=admin.workspace_id,
            role_id=role.id,
            name="Scoped Buyer",
            login=SCOPED_BUYER,
            password_hash=hash_password("test-password"),
            keitaro_offer_group="nutra",
        )
        db.add(buyer)
        await db.commit()
        buyer_id = str(buyer.id)

    try:
        with _admin_client() as client:
            scoped = client.get(
                f"/api/v1/offers?for_buyer_id={buyer_id}&exclude_offers_group=true&limit=500"
            )
            assert scoped.status_code == 200
            # Ничего не назначено — и группа Keitaro тут не помогает.
            assert _names(scoped.json()) == set()

            client.put(
                f"/api/v1/offers/{ids[IN_GROUP]}/buyers",
                json={"buyer_ids": [buyer_id]},
            )
            with_assignment = client.get(
                f"/api/v1/offers?for_buyer_id={buyer_id}&limit=500"
            )
            assert _names(with_assignment.json()) == {"AAA Offers Module In Group"}

            # Полный доступ не сужаем: администратору нужен весь справочник.
            everything = client.get("/api/v1/offers?scope_offers=true&limit=500")
            assert "BBB Offers Module In Group Lowercase" in _names(everything.json())
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(OfferBuyer).where(OfferBuyer.user_id == uuid.UUID(buyer_id))
            )
            await db.execute(delete(User).where(User.login == SCOPED_BUYER))
            await db.commit()


async def test_a_buyer_only_sees_offers_assigned_to_them(offer_rows) -> None:
    """Тот же список, но глазами самого баера — без явного `for_buyer_id`."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        role = await db.scalar(
            select(Role).where(Role.workspace_id == admin.workspace_id, Role.name == "Buyer")
        )
        db.add(
            User(
                workspace_id=admin.workspace_id,
                role_id=role.id,
                name="Self Scoped Buyer",
                login=SCOPED_BUYER,
                password_hash=hash_password("test-password"),
                keitaro_offer_group="Nutra",
            )
        )
        await db.commit()

    try:
        client = TestClient(app)
        login = client.post(
            "/api/v1/auth/login",
            json={"login": SCOPED_BUYER, "password": "test-password"},
        )
        assert login.status_code == 200
        with client:
            response = client.get("/api/v1/offers?scope_offers=true&limit=500")
            assert response.status_code == 200
            # Группа совпадает, но ни один оффер ему не выдан — список пуст.
            assert _names(response.json()) == set()
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.login == SCOPED_BUYER))
            await db.commit()


async def test_spend_scope_hides_the_offers_group_for_everyone(offer_rows) -> None:
    """В фиксировании расхода группы OFFERS нет — даже у её владельцев.

    Баер, чья группа Keitaro совпадает со служебной, без запрета видел бы весь
    справочник OFFERS; полный доступ администратора тоже не пробивает запрет.
    """
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        role = await db.scalar(
            select(Role).where(Role.workspace_id == admin.workspace_id, Role.name == "Buyer")
        )
        buyer = User(
            workspace_id=admin.workspace_id,
            role_id=role.id,
            name="Spend Scoped Buyer",
            login=SPEND_SCOPED_BUYER,
            password_hash=hash_password("test-password"),
            keitaro_offer_group="offers",
        )
        db.add(buyer)
        await db.commit()
        buyer_id = str(buyer.id)

    try:
        with _admin_client() as client:
            spend_view = client.get("/api/v1/offers?for_spend=true&limit=500")
            assert spend_view.status_code == 200
            names = _names(spend_view.json())
            assert "CCC Offers Module Out Of Group" in names
            assert "AAA Offers Module In Group" not in names
            assert "BBB Offers Module In Group Lowercase" not in names

            scoped = client.get(
                f"/api/v1/offers?for_buyer_id={buyer_id}&for_spend=true&limit=500"
            )
            assert scoped.status_code == 200
            # Вся его группа служебная и скрыта, личных назначений нет.
            assert _names(scoped.json()) == set()
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.login == SPEND_SCOPED_BUYER))
            await db.commit()


async def test_mediaboard_rows_skip_the_offers_group(offer_rows) -> None:
    """Записи на офферах группы «Оффера» в Медиаборд не попадают."""
    ids, admin_id = offer_rows
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        db.add_all(
            [
                MediaRecord(
                    workspace_id=admin.workspace_id,
                    record_date=date(2026, 6, 1),
                    buyer_id=admin.id,
                    offer_id=uuid.UUID(ids[IN_GROUP]),
                    revenue=Decimal("500"),
                ),
                MediaRecord(
                    workspace_id=admin.workspace_id,
                    record_date=date(2026, 6, 1),
                    buyer_id=admin.id,
                    offer_id=uuid.UUID(ids[OUT_OF_GROUP]),
                    revenue=Decimal("700"),
                ),
            ]
        )
        await db.commit()

    with _admin_client() as client:
        board = client.get("/api/v1/media-records/groups?date_from=2026-06-01&date_to=2026-06-01")
        listing = client.get("/api/v1/media-records?date_from=2026-06-01&date_to=2026-06-01")

    offers_on_board = {group["offer"] for group in board.json()["groups"]}
    assert offers_on_board == {"CCC Offers Module Out Of Group"}
    assert {row["offer"] for row in listing.json()["items"]} == {
        "CCC Offers Module Out Of Group"
    }


def test_geo_resolves_new_countries_and_old_truncations() -> None:
    assert normalize_geo("Antigua and Barbuda") == "AG"
    assert normalize_geo("ATG") == "AG"
    # Так значение уже лежит в базе: обрезано до появления страны в таблице.
    assert normalize_geo("ANTIGUA AND ") == "AG"
    assert normalize_geo("TRINIDAD AND") == "TT"
    assert normalize_geo("Jamaica") == "JM"
    # Неоднозначный префикс не угадывается — значение остаётся как есть.
    assert normalize_geo("UNITED") == "UNITED"


async def test_a_team_lead_gets_his_own_cap_on_the_offer(database) -> None:
    """Общий лимит партнёрки тимлиды делят между собой.

    Поэтому капа живёт на связке «оффер + тимлид», а не на самом оффере: в
    списке каждый тимлид должен видеть свою цифру, а не чужую.
    """
    async with SessionLocal() as db:
        admin_id = str(
            (await db.scalar(select(User).where(User.login == "admin"))).id
        )

    with _admin_client() as client:
        created = client.post(
            "/api/v1/offers",
            json={"name": "Cap per lead", "cap": "300 FTD / день"},
        )
        assert created.status_code == 201
        offer_id = created.json()["id"]

        assigned = client.put(
            f"/api/v1/offers/{offer_id}/leads",
            json={"lead_ids": [admin_id], "caps": {admin_id: "120 FTD"}},
        )
        assert assigned.status_code == 200

        listed = client.get("/api/v1/offers?manual=true").json()["items"]
        row = next(item for item in listed if item["id"] == offer_id)
        assert row["leads"][0]["cap"] == "120 FTD"
        # Капа тимлида не подменяет общий лимит оффера — они живут рядом.
        assert row["cap"] == "300 FTD / день"

        # Сохранение карточки переписывает назначения целиком: капа при этом
        # не должна теряться, её ставят другой ручкой.
        client.put(
            f"/api/v1/offers/{offer_id}",
            json={"name": "Cap per lead", "cap": "300 FTD / день",
                  "lead_ids": [admin_id], "buyer_ids": []},
        )
        again = client.get("/api/v1/offers?manual=true").json()["items"]
        kept = next(item for item in again if item["id"] == offer_id)
        assert kept["leads"][0]["cap"] == "120 FTD"

        # Снятый тимлид уносит свою капу с собой.
        client.put(f"/api/v1/offers/{offer_id}/leads", json={"lead_ids": []})
        cleared = client.get("/api/v1/offers?manual=true").json()["items"]
        assert next(item for item in cleared if item["id"] == offer_id)["leads"] == []
        client.delete(f"/api/v1/offers/{offer_id}")


async def test_an_offer_belongs_to_one_partner_integration(database) -> None:
    """Номер оффера принадлежит одной программе.

    Пока интеграция одна, оффер без выбора достаётся ей — иначе обновление
    сломало бы уже работающие связки. Со второй такой оффер не уходит никому:
    тот же номер у другой ПП значит другой оффер, и депозиты приехали бы не туда.
    """
    from app.models import PartnerIntegration
    from app.services.partner_sync import mapped_offers, unassigned_offers

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        first = PartnerIntegration(
            workspace_id=admin.workspace_id, name=f"PP one {uuid.uuid4().hex[:6]}",
            partner_name="Affise", base_url="http://pp.test",
            api_key_encrypted=encrypt_secret("key"),
        )
        db.add(first)
        await db.flush()
        first_id = first.id
        await db.commit()

    with _admin_client() as client:
        reference = client.get("/api/v1/offers/reference").json()
        assert any(row["id"] == str(first_id) for row in reference["partner_integrations"])

        created = client.post(
            "/api/v1/offers",
            json={"name": "Scoped offer", "external_id": "77177",
                  "partner_integration_id": str(first_id)},
        )
        assert created.status_code == 201
        offer_id = created.json()["id"]

        listed = client.get("/api/v1/offers?manual=true").json()["items"]
        row = next(item for item in listed if item["id"] == offer_id)
        assert row["partner_integration_id"] == str(first_id)
        assert row["partner_integration"] == "Affise"

        # Чужая интеграция не принимается.
        alien = client.put(
            f"/api/v1/offers/{offer_id}",
            json={"name": "Scoped offer", "external_id": "77177",
                  "partner_integration_id": str(uuid.uuid4())},
        )
        assert alien.status_code == 422

    async with SessionLocal() as db:
        first = await db.get(PartnerIntegration, first_id)
        second = PartnerIntegration(
            workspace_id=first.workspace_id, name=f"PP two {uuid.uuid4().hex[:6]}",
            partner_name="Fame LATAM", base_url="http://pp2.test",
            api_key_encrypted=encrypt_secret("key"),
        )
        db.add(second)
        await db.flush()
        second_id = second.id
        mine = [row.external_id for row in await mapped_offers(db, first)]
        theirs = [row.external_id for row in await mapped_offers(db, second)]
        orphans = await unassigned_offers(db, first.workspace_id)

    assert "77177" in mine
    assert "77177" not in theirs
    assert orphans >= 0

    async with SessionLocal() as db:
        await db.execute(delete(Offer).where(Offer.id == uuid.UUID(offer_id)))
        await db.execute(
            delete(PartnerIntegration).where(PartnerIntegration.id.in_([first_id, second_id]))
        )
        await db.commit()


async def test_the_dashboard_widget_shows_the_offers_section(database) -> None:
    """Виджет «Ваши оффера» берёт справочник раздела, а не трекер.

    Раньше он показывал офферы, синхронизированные из Keitaro, и на дашборде
    человек видел один список, а в разделе — другой. Плюс видимость: админ и
    носитель `offers.view_all` видят весь справочник, тимлид и баер — только
    назначенное им.

    У каждого запроса свой период: ответ дашборда кешируется на пять минут, и
    без разных периодов второй вызов вернул бы первый же ответ.
    """
    suffix = uuid.uuid4().hex[:6]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == admin.workspace_id
            )
        )
        manual = Offer(workspace_id=admin.workspace_id, name=f"Manual {suffix}", geo="AR")
        db.add(manual)
        if connection:
            db.add(
                Offer(
                    workspace_id=admin.workspace_id, name=f"Tracked {suffix}", geo="AR",
                    connection_id=connection.id, external_id=f"kt-{suffix}",
                )
            )
        await db.flush()
        manual_id = manual.id
        roles = {
            name: await db.scalar(
                select(Role).where(
                    Role.workspace_id == admin.workspace_id, Role.name == name
                )
            )
            for name in ("Team Lead", "Buyer")
        }
        people = {
            "lead": User(
                workspace_id=admin.workspace_id, role_id=roles["Team Lead"].id,
                name="Widget Lead", login=f"widget-lead-{suffix}",
                password_hash=hash_password("test-password"),
            ),
            "buyer": User(
                workspace_id=admin.workspace_id, role_id=roles["Buyer"].id,
                name="Widget Buyer", login=f"widget-buyer-{suffix}",
                password_hash=hash_password("test-password"),
            ),
        }
        db.add_all(list(people.values()))
        await db.flush()
        person_ids = {key: row.id for key, row in people.items()}
        await db.commit()

    def _widget(login: str, day: str) -> set[str]:
        client = TestClient(app)
        assert client.post(
            "/api/v1/auth/login", json={"login": login, "password": "test-password"}
        ).status_code == 200
        with client:
            data = client.get(
                f"/api/v1/dashboard?date_from=2019-{day}-01&date_to=2019-{day}-02"
            ).json()
        return {row["name"] for row in data["working_offers"]}

    try:
        with _admin_client() as client:
            dashboard = client.get(
                "/api/v1/dashboard?date_from=2019-01-01&date_to=2019-01-02"
            ).json()
            names = {row["name"] for row in dashboard["working_offers"]}
        async with SessionLocal() as db:
            expected_total = await db.scalar(
                select(func.count()).select_from(Offer).where(
                    Offer.workspace_id == admin.workspace_id,
                    Offer.connection_id.is_(None),
                )
            )
        assert dashboard["working_offers_total"] == expected_total
        assert f"Manual {suffix}" in names
        # Строки трекера в раздел не входят — не должно их быть и в виджете.
        assert f"Tracked {suffix}" not in names

        # Ничего не назначено — пусто у обоих.
        assert _widget(f"widget-lead-{suffix}", "02") == set()
        assert _widget(f"widget-buyer-{suffix}", "03") == set()

        async with SessionLocal() as db:
            db.add(OfferLead(offer_id=manual_id, user_id=person_ids["lead"]))
            db.add(OfferBuyer(offer_id=manual_id, user_id=person_ids["buyer"]))
            await db.commit()

        assert _widget(f"widget-lead-{suffix}", "04") == {f"Manual {suffix}"}
        assert _widget(f"widget-buyer-{suffix}", "05") == {f"Manual {suffix}"}
    finally:
        async with SessionLocal() as db:
            ids = list(person_ids.values())
            await db.execute(delete(OfferLead).where(OfferLead.user_id.in_(ids)))
            await db.execute(delete(OfferBuyer).where(OfferBuyer.user_id.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.execute(
                delete(Offer).where(Offer.name.in_([f"Manual {suffix}", f"Tracked {suffix}"]))
            )
            await db.commit()


async def test_widget_shows_a_lead_the_offers_of_his_buyers(database) -> None:
    """Раздел и виджет обязаны показывать одно и то же.

    Оффер сначала отдают тимлиду, тот раздаёт его баерам. По одному своему id
    тимлид не видел в виджете офферы, которые сам же раздал, — а в разделе они
    есть, и список на дашборде выглядел неполным.
    """
    from app.api.routers.analytics import _offers_widget
    from app.models import Offer, OfferBuyer, OfferStatus, Role, User, UserParent

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        lead_role = await db.scalar(select(Role).where(Role.name == "Team Lead"))
        lead = User(workspace_id=admin.workspace_id, role_id=lead_role.id,
                    name="Виджет ТЛ", login="widget-lead",
                    password_hash=hash_password("widget-password"))
        db.add(lead)
        await db.flush()
        buyer = User(workspace_id=admin.workspace_id, role_id=buyer_role.id,
                     name="Виджет баер", login="widget-buyer",
                     password_hash=hash_password("widget-password"))
        offer = Offer(workspace_id=admin.workspace_id, name="Оффер баера",
                      status=OfferStatus.active)
        db.add_all([buyer, offer])
        await db.flush()
        # Иерархия живёт отдельной таблицей: баер подчинён тимлиду.
        db.add(UserParent(user_id=buyer.id, parent_id=lead.id))
        db.add(OfferBuyer(offer_id=offer.id, user_id=buyer.id))
        await db.commit()

        seen = await _offers_widget(db, lead)

        assert offer.id in seen, "тимлид должен видеть оффер своего баера"

        await db.delete(offer)
        await db.delete(buyer)
        await db.delete(lead)
        await db.commit()


async def test_a_lead_assigns_buyers_but_does_not_create_offers(database) -> None:
    """Тимлид раздаёт выданное ему, а справочник ведёт администратор.

    Раньше это было одно право `offers.manage`: раздача баерам тянула за собой
    и кнопку «Новый оффер», хотя заводить офферы тимлид не должен.
    """
    from app.models import Offer, OfferLead, OfferStatus, Permission, Role, User

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        codes = ["offers.view", "offers.assign", "team.view", "dashboard.view"]
        rights = list(
            (await db.execute(select(Permission).where(Permission.code.in_(codes)))).scalars()
        )
        assert {right.code for right in rights} == set(codes), "право offers.assign не заведено"
        role = Role(workspace_id=admin.workspace_id, name="ТЛ без справочника",
                    permissions=rights)
        db.add(role)
        await db.flush()
        lead = User(workspace_id=admin.workspace_id, role_id=role.id,
                    name="Раздающий ТЛ", login="assign-lead",
                    password_hash=hash_password("assign-password"))
        offer = Offer(workspace_id=admin.workspace_id, name="Оффер тимлида",
                      status=OfferStatus.active)
        db.add_all([lead, offer])
        await db.flush()
        db.add(OfferLead(offer_id=offer.id, user_id=lead.id))
        await db.commit()
        offer_id, lead_id, role_id = offer.id, lead.id, role.id

    with TestClient(app) as client:
        client.post("/api/v1/auth/login",
                    json={"login": "assign-lead", "password": "assign-password"})
        created = client.post("/api/v1/offers", json={"name": "Свой оффер"})
        assert created.status_code == 403, "тимлид не должен заводить офферы"
        assigned = client.put(
            f"/api/v1/offers/{offer_id}/buyers", json={"buyer_ids": []}
        )
        assert assigned.status_code == 200, assigned.text
        leads = client.put(f"/api/v1/offers/{offer_id}/leads", json={"lead_ids": []})
        # Владельца оффера меняет тот, кто ведёт справочник.
        assert leads.status_code == 403

    async with SessionLocal() as db:
        await db.delete(await db.get(Offer, offer_id))
        await db.delete(await db.get(User, lead_id))
        await db.delete(await db.get(Role, role_id))
        await db.commit()


async def test_the_board_offers_are_listed_for_a_buyer(database) -> None:
    """Медиаборд показывает трекерные офферы своей команды, но не чужой."""
    from app.models import IntegrationConnection, Offer, OfferStatus, Role, User

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id, name="Трекер доски",
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("board-key"), timezone="UTC",
        )
        db.add(connection)
        await db.flush()
        offer = Offer(workspace_id=admin.workspace_id, connection_id=connection.id,
                      name="Трекерный оффер", external_id="board-1",
                      group_name="BOARD TEAM", status=OfferStatus.active)
        foreign_offer = Offer(workspace_id=admin.workspace_id, connection_id=connection.id,
                              name="Чужой трекерный оффер", external_id="board-2",
                              group_name="FOREIGN TEAM", status=OfferStatus.active)
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        buyer = User(workspace_id=admin.workspace_id, role_id=buyer_role.id,
                     name="Баер доски", login="board-buyer",
                     password_hash=hash_password("board-password"),
                     keitaro_offer_group="board team")
        db.add_all([offer, foreign_offer, buyer])
        await db.commit()
        offer_id, foreign_offer_id = offer.id, foreign_offer.id
        buyer_id, connection_id = buyer.id, connection.id

    with TestClient(app) as client:
        client.post("/api/v1/auth/login",
                    json={"login": "board-buyer", "password": "board-password"})
        answer = client.get("/api/v1/offers?exclude_offers_group=true&scope_offers=true")

    assert answer.status_code == 200, answer.text
    names = [row["name"] for row in answer.json()["items"]]
    assert "Трекерный оффер" in names
    assert "Чужой трекерный оффер" not in names

    async with SessionLocal() as db:
        await db.delete(await db.get(Offer, offer_id))
        await db.delete(await db.get(Offer, foreign_offer_id))
        await db.delete(await db.get(User, buyer_id))
        await db.delete(await db.get(IntegrationConnection, connection_id))
        await db.commit()
