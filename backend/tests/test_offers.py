import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

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


async def test_the_dashboard_widget_shows_each_role_its_own_offers(offer_rows) -> None:
    """Админу — «Не занят», тимлиду — «Активен» у него, баеру — «В работе» у него."""
    ids, admin_id = offer_rows
    free_offer, lead_offer, buyer_offer = (
        ids[IN_GROUP], ids[IN_GROUP_LOWER], ids[OUT_OF_GROUP]
    )
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        roles = {
            name: await db.scalar(
                select(Role).where(
                    Role.workspace_id == admin.workspace_id, Role.name == name
                )
            )
            for name in ("Team Lead", "Buyer")
        }
        people = {
            key: User(
                workspace_id=admin.workspace_id,
                role_id=roles[role].id,
                name=f"Widget {key}",
                login=f"{SCOPED_BUYER}-{key}",
                password_hash=hash_password("test-password"),
            )
            for key, role in (("lead", "Team Lead"), ("buyer", "Buyer"))
        }
        db.add_all(list(people.values()))
        await db.flush()
        db.add_all(
            [
                OfferLead(offer_id=uuid.UUID(lead_offer), user_id=people["lead"].id),
                OfferBuyer(offer_id=uuid.UUID(buyer_offer), user_id=people["buyer"].id),
            ]
        )
        await db.commit()

    def _widget(login: str) -> set[str]:
        client = TestClient(app)
        assert client.post(
            "/api/v1/auth/login", json={"login": login, "password": "test-password"}
        ).status_code == 200
        with client:
            data = client.get("/api/v1/dashboard").json()
        return {row["id"] for row in data["working_offers"]}

    try:
        with _admin_client() as client:
            client.patch(f"/api/v1/offers/{lead_offer}/status", json={"status": "active"})
            client.patch(
                f"/api/v1/offers/{buyer_offer}/status", json={"status": "working"}
            )

        assert free_offer in _widget("admin")
        assert lead_offer not in _widget("admin")

        lead_view = _widget(f"{SCOPED_BUYER}-lead")
        assert lead_view == {lead_offer}

        buyer_view = _widget(f"{SCOPED_BUYER}-buyer")
        assert buyer_view == {buyer_offer}
    finally:
        async with SessionLocal() as db:
            logins = [f"{SCOPED_BUYER}-lead", f"{SCOPED_BUYER}-buyer"]
            people_ids = list(
                (await db.scalars(select(User.id).where(User.login.in_(logins)))).all()
            )
            await db.execute(delete(OfferLead).where(OfferLead.user_id.in_(people_ids)))
            await db.execute(delete(OfferBuyer).where(OfferBuyer.user_id.in_(people_ids)))
            await db.execute(delete(User).where(User.login.in_(logins)))
            await db.commit()


async def test_the_offer_reference_lists_keitaro_geos_and_partners(offer_rows) -> None:
    """GEO и партнёрки для формы берутся из того, что пришло из Keitaro."""
    with _admin_client() as client:
        reference = client.get("/api/v1/offers/reference")

    assert reference.status_code == 200
    payload = reference.json()
    # "INDIA" лежит в базе как есть — в списке она уже кодом.
    assert "IN" in payload["geos"] and "DE" in payload["geos"]
    assert "test" not in payload["statuses"]
    assert payload["statuses"][0] == "active"
    assert isinstance(payload["partners"], list)


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


async def test_offers_narrow_to_the_buyers_own_keitaro_group(offer_rows) -> None:
    """«Оффера этого баера» — его группа Keitaro плюс назначенное лично ему."""
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
            # Группа у баера записана строчными — сверка регистронезависимая.
            assert _names(scoped.json()) == {"CCC Offers Module Out Of Group"}

            # Оффер чужой группы, назначенный лично, тоже его.
            client.put(
                f"/api/v1/offers/{ids[IN_GROUP]}/buyers",
                json={"buyer_ids": [buyer_id]},
            )
            with_assignment = client.get(
                f"/api/v1/offers?for_buyer_id={buyer_id}&limit=500"
            )
            assert "AAA Offers Module In Group" in _names(with_assignment.json())

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


async def test_a_buyer_only_sees_offers_of_their_own_group(offer_rows) -> None:
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
            assert _names(response.json()) == {"CCC Offers Module Out Of Group"}
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.login == SCOPED_BUYER))
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
