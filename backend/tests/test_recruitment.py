"""Раздел «Рекрутинг»: прокси к Recruitment Service.

Сервис фейкается на уровне `RecruitmentClient._request` — HTTP-транспорт не
нужен: контракт проверяется на границе роутера (права, проброс вызовов,
маппинг ошибок сервиса в 502 с человеческим текстом).
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import recruitment as recruitment_module
from tests.test_media_finance import _admin_client


@pytest.fixture
def fake_service(monkeypatch):
    """Перехват запросов к Recruitment Service: ответы + журнал обращений."""
    calls: list[dict] = []
    responses: dict = {}

    async def fake_request(self, method, path, *, params=None, json=None):
        calls.append(
            {"method": method, "path": path, "params": params, "json": json}
        )
        handler = responses.get((method, path))
        if handler is None:
            handler = responses.get(method)
        if isinstance(handler, Exception):
            raise handler
        return handler

    monkeypatch.setattr(
        recruitment_module.RecruitmentClient, "_request", fake_request
    )
    fixture = {"calls": calls, "responses": responses}
    return fixture


async def test_templates_are_proxied_for_admin(fake_service) -> None:
    fake_service["responses"]["GET"] = [
        {"id": "t1", "name": "Media Buyer", "auto_search_enabled": True}
    ]
    with _admin_client() as client:
        response = client.get("/api/v1/recruitment/search-templates")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "Media Buyer"
    assert fake_service["calls"][0]["path"] == "/search-templates"


async def test_section_is_hidden_from_buyer(database) -> None:
    """Раздел только для администратора: у баера права recruitment.view нет."""
    from sqlalchemy import delete, select

    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import Role, User

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        role = await db.scalar(
            select(Role).where(Role.workspace_id == admin.workspace_id, Role.name == "Buyer")
        )
        db.add(
            User(
                workspace_id=admin.workspace_id,
                role_id=role.id,
                name="Recruit Buyer",
                login="recruitment-buyer",
                password_hash=hash_password("test-password"),
            )
        )
        await db.commit()

    try:
        client = TestClient(app)
        login = client.post(
            "/api/v1/auth/login",
            json={"login": "recruitment-buyer", "password": "test-password"},
        )
        assert login.status_code == 200
        with client:
            response = client.get("/api/v1/recruitment/search-templates")
        assert response.status_code == 403
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.login == "recruitment-buyer"))
            await db.commit()


async def test_review_decision_is_forwarded(fake_service) -> None:
    fake_service["responses"][("PATCH", "/external-candidates/abc/review")] = {
        "id": "abc",
        "review_status": "added",
    }
    with _admin_client() as client:
        good = client.patch(
            "/api/v1/recruitment/candidates/abc/review",
            json={"decision": "added"},
        )
        bad = client.patch(
            "/api/v1/recruitment/candidates/abc/review",
            json={"decision": "hire"},
        )
    assert good.status_code == 200
    assert good.json()["review_status"] == "added"
    forwarded = fake_service["calls"][0]["json"]
    assert forwarded["decision"] == "added"
    assert forwarded["reviewed_by"] == "admin"
    # Неизвестное решение отсекаем сами: сервису мусор не уходит.
    assert bad.status_code == 422
    assert len(fake_service["calls"]) == 1


async def test_service_outage_becomes_a_friendly_502(
    fake_service, monkeypatch
) -> None:
    from app.services.recruitment import RecruitmentError

    fake_service["responses"]["GET"] = RecruitmentError("Сервис рекрутинга недоступен")
    with _admin_client() as client:
        response = client.get("/api/v1/recruitment/search-templates")
    assert response.status_code == 502
    assert "недоступен" in response.json()["error"]["message"]


async def test_run_and_candidates_are_proxied(fake_service) -> None:
    fake_service["responses"][("POST", "/search-templates/t1/run")] = {
        "search_run_id": "run-1",
        "status": "queued",
    }
    fake_service["responses"]["GET"] = [
        {"id": "c1", "review_status": "pending", "score": 82}
    ]
    with _admin_client() as client:
        run = client.post("/api/v1/recruitment/search-templates/t1/run")
        pending = client.get(
            "/api/v1/recruitment/candidates?review_status=pending&source=hh&min_score=55"
        )
    assert run.status_code == 202
    assert run.json()["status"] == "queued"
    assert pending.status_code == 200
    assert pending.json()[0]["review_status"] == "pending"
    listing = fake_service["calls"][-1]
    assert listing["params"]["source"] == "hh"
    assert listing["params"]["min_score"] == 55


async def test_hh_status_and_connect_are_proxied(fake_service) -> None:
    fake_service["responses"][("GET", "/providers/hh/status")] = {
        "connected": False,
        "account_id": None,
    }
    fake_service["responses"][("POST", "/providers/hh/connect")] = {
        "authorize_url": "https://hh.ru/oauth/authorize?client_id=x"
    }
    with _admin_client() as client:
        status = client.get("/api/v1/recruitment/hh/status")
        connect = client.post("/api/v1/recruitment/hh/connect")
    assert status.status_code == 200
    assert status.json()["connected"] is False
    assert connect.status_code == 200
    assert "authorize_url" in connect.json()


# --- воронка найма -----------------------------------------------------------
#
# Recruitment Service этапы найма не хранит — они целиком наши. Проверяется то,
# ради чего воронка и заводилась: «Добавить в кандидаты» ставит человека на
# скрининг, этап переживает повторный разбор, а доска открывается даже когда
# сервис молчит.

EXTERNAL = {
    "id": "ext-1",
    "parsed_profile": {
        "position_title": "Media Buyer",
        "total_experience_months": 18,
        "geo": "Санкт-Петербург",
        "salary_expectation": 3000,
        "skills": ["Facebook Ads"],
    },
    "review_status": "added",
    "sources": [
        {"source": "hh", "external_id": "12345",
         "external_url": "https://hh.ru/resume/12345"}
    ],
    "scores": [
        {"search_template_id": "t1", "score": 82, "tier": "high",
         "hard_filters_passed": True}
    ],
}


@pytest.fixture
async def clean_pipeline(database):
    """Пустая доска до и после теста.

    Соседние тесты раздела тоже помечают находки `added`, и теперь это заводит
    строку воронки — без уборки перед тестом чужой кандидат попадает в счёт.
    """
    from sqlalchemy import delete

    from app.core.database import SessionLocal
    from app.models import RecruitmentCandidate

    async def wipe():
        async with SessionLocal() as db:
            await db.execute(delete(RecruitmentCandidate))
            await db.commit()

    await wipe()
    yield
    await wipe()


async def test_adding_a_candidate_puts_them_on_screening(
    fake_service, clean_pipeline
) -> None:
    fake_service["responses"]["PATCH"] = EXTERNAL
    fake_service["responses"]["GET"] = []
    with _admin_client() as client:
        review = client.patch(
            "/api/v1/recruitment/candidates/ext-1/review", json={"decision": "added"}
        )
        board = client.get("/api/v1/recruitment/pipeline")
    assert review.status_code == 200
    payload = board.json()
    assert [stage["key"] for stage in payload["stages"]] == [
        "screening", "interview", "offer", "hired", "rejected"
    ]
    assert len(payload["items"]) == 1
    row = payload["items"][0]
    assert row["stage"] == "screening"
    assert row["position_title"] == "Media Buyer"
    assert row["geo"] == "Санкт-Петербург"
    assert row["tier"] == "high" and row["score"] == 82
    assert row["external_url"] == "https://hh.ru/resume/12345"
    # Счётчик колонки «Скрининг» — единица.
    assert payload["stages"][0]["count"] == 1


async def test_stage_moves_and_survives_a_second_review(
    fake_service, clean_pipeline
) -> None:
    fake_service["responses"]["PATCH"] = EXTERNAL
    fake_service["responses"]["GET"] = []
    with _admin_client() as client:
        client.patch(
            "/api/v1/recruitment/candidates/ext-1/review", json={"decision": "added"}
        )
        row_id = client.get("/api/v1/recruitment/pipeline").json()["items"][0]["id"]
        moved = client.patch(
            f"/api/v1/recruitment/pipeline/{row_id}", json={"stage": "interview"}
        )
        # Повторный разбор той же находки не должен вернуть человека на скрининг.
        client.patch(
            "/api/v1/recruitment/candidates/ext-1/review", json={"decision": "added"}
        )
        after = client.get("/api/v1/recruitment/pipeline").json()
    assert moved.status_code == 200
    assert moved.json()["stage"] == "interview"
    assert moved.json()["stage_label"] == "Интервью"
    assert after["items"][0]["stage"] == "interview"
    assert after["stages"][1]["count"] == 1


async def test_unknown_stage_is_refused(fake_service, clean_pipeline) -> None:
    fake_service["responses"]["PATCH"] = EXTERNAL
    fake_service["responses"]["GET"] = []
    with _admin_client() as client:
        client.patch(
            "/api/v1/recruitment/candidates/ext-1/review", json={"decision": "added"}
        )
        row_id = client.get("/api/v1/recruitment/pipeline").json()["items"][0]["id"]
        bad = client.patch(
            f"/api/v1/recruitment/pipeline/{row_id}", json={"stage": "нанят-наверное"}
        )
    assert bad.status_code == 422


async def test_cancelling_the_decision_takes_them_off_the_board(
    fake_service, clean_pipeline
) -> None:
    fake_service["responses"]["PATCH"] = EXTERNAL
    fake_service["responses"]["GET"] = []
    with _admin_client() as client:
        client.patch(
            "/api/v1/recruitment/candidates/ext-1/review", json={"decision": "added"}
        )
        assert len(client.get("/api/v1/recruitment/pipeline").json()["items"]) == 1
        client.patch(
            "/api/v1/recruitment/candidates/ext-1/review", json={"decision": "pending"}
        )
        after = client.get("/api/v1/recruitment/pipeline").json()
    assert after["items"] == []


async def test_board_imports_people_added_before_the_pipeline_existed(
    fake_service, clean_pipeline
) -> None:
    """Иначе «Кандидаты» пустые при непустом сервисе — ровно то, что и было."""
    fake_service["responses"]["GET"] = [EXTERNAL]
    with _admin_client() as client:
        board = client.get("/api/v1/recruitment/pipeline").json()
    assert board["imported"] == 1
    assert len(board["items"]) == 1
    assert board["items"][0]["stage"] == "screening"
    assert board["service_available"] is True


async def test_board_opens_when_the_service_is_down(
    fake_service, clean_pipeline
) -> None:
    """Человек на интервью не перестаёт существовать, если упал контейнер."""
    fake_service["responses"]["PATCH"] = EXTERNAL
    fake_service["responses"]["GET"] = []
    with _admin_client() as client:
        client.patch(
            "/api/v1/recruitment/candidates/ext-1/review", json={"decision": "added"}
        )
        fake_service["responses"]["GET"] = recruitment_module.RecruitmentError(
            "Сервис рекрутинга не отвечает"
        )
        board = client.get("/api/v1/recruitment/pipeline")
    assert board.status_code == 200
    payload = board.json()
    assert payload["service_available"] is False
    assert len(payload["items"]) == 1
    assert payload["items"][0]["position_title"] == "Media Buyer"


async def test_owner_and_note_are_stored(fake_service, clean_pipeline) -> None:
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import User

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        admin_id = str(admin.id)

    fake_service["responses"]["PATCH"] = EXTERNAL
    fake_service["responses"]["GET"] = []
    with _admin_client() as client:
        client.patch(
            "/api/v1/recruitment/candidates/ext-1/review", json={"decision": "added"}
        )
        row_id = client.get("/api/v1/recruitment/pipeline").json()["items"][0]["id"]
        updated = client.patch(
            f"/api/v1/recruitment/pipeline/{row_id}",
            json={"owner_id": admin_id, "note": "Созвон в четверг"},
        )
    assert updated.status_code == 200
    assert updated.json()["owner_id"] == admin_id
    assert updated.json()["owner_name"] == "Administrator"
    assert updated.json()["note"] == "Созвон в четверг"


async def test_removing_from_the_board_keeps_the_decision_in_the_service(
    fake_service, clean_pipeline
) -> None:
    """Снимаем карточку найма, а не отменяем решение HR: иначе человек снова
    всплыл бы в «Откликах»."""
    fake_service["responses"]["PATCH"] = EXTERNAL
    fake_service["responses"]["GET"] = []
    with _admin_client() as client:
        client.patch(
            "/api/v1/recruitment/candidates/ext-1/review", json={"decision": "added"}
        )
        row_id = client.get("/api/v1/recruitment/pipeline").json()["items"][0]["id"]
        before = len(fake_service["calls"])
        removed = client.delete(f"/api/v1/recruitment/pipeline/{row_id}")
    assert removed.status_code == 200
    # Ни одного обращения к сервису при снятии с доски.
    patches = [
        call for call in fake_service["calls"][before:] if call["method"] == "PATCH"
    ]
    assert patches == []


async def test_incoming_responses_are_asked_by_source(fake_service) -> None:
    """«Отклики» запрашивают только входящие источники.

    Раньше экран брал всё неразобранное, и туда попадали резюме, найденные
    поиском, — именно на это жаловались.
    """
    fake_service["responses"]["GET"] = []
    with _admin_client() as client:
        client.get(
            "/api/v1/recruitment/candidates?review_status=pending&limit=200&source=telegram"
        )
    params = fake_service["calls"][-1]["params"]
    assert params["source"] == "telegram"
    assert params["review_status"] == "pending"
