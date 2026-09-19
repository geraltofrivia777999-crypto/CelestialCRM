import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.database import SessionLocal
from app.main import app
from app.models import TaskField, TaskSection, TaskTemplate


def _admin(client: TestClient) -> None:
    login = client.post(
        "/api/v1/auth/login", json={"login": "admin", "password": "test-password"}
    )
    assert login.status_code == 200


async def test_field_with_an_existing_name_is_reused(database) -> None:
    """Поле с тем же названием не ошибка: форма получает уже заведённое поле.

    Поля — общий справочник воркспейса, и «ТЗ» из шаблона — то же «ТЗ», что
    хотят добавить в задачу. Отказ остаётся только при другом типе.
    """
    name = f"ТЗ {uuid.uuid4().hex[:6]}"
    field_id = None
    try:
        with TestClient(app) as client:
            _admin(client)
            first = client.post(
                "/api/v1/workspace/fields",
                json={"name": name, "kind": "text", "show_always": False},
            )
            assert first.status_code == 201, first.text
            field_id = first.json()["id"]

            again = client.post(
                "/api/v1/workspace/fields",
                json={"name": name.upper(), "kind": "text", "show_always": True},
            )
            assert again.status_code in (200, 201), again.text
            assert again.json()["id"] == field_id
            # Завели из задачи — поле стало видно и в задачах без шаблона.
            assert again.json()["show_always"] is True

            other = client.post(
                "/api/v1/workspace/fields", json={"name": name, "kind": "textarea"}
            )
            assert other.status_code == 422
            assert "другим типом" in other.json()["error"]["message"]
    finally:
        if field_id:
            async with SessionLocal() as db:
                await db.execute(delete(TaskField).where(TaskField.id == uuid.UUID(field_id)))
                await db.commit()


async def test_each_section_has_its_own_default_template(database) -> None:
    """Шаблон по умолчанию ставится разделу и снимается у него же."""
    suffix = uuid.uuid4().hex[:6]
    section_id = template_id = None
    try:
        with TestClient(app) as client:
            _admin(client)
            section = client.post(
                "/api/v1/workspace/sections", json={"title": f"Дизайн {suffix}"}
            )
            assert section.status_code == 201, section.text
            section_id = section.json()["id"]
            template = client.post(
                "/api/v1/workspace/task-templates",
                json={"name": f"Бриф {suffix}", "field_ids": [], "custom_values": {}},
            )
            assert template.status_code == 201, template.text
            template_id = template.json()["id"]

            chosen = client.patch(
                f"/api/v1/workspace/sections/{section_id}",
                json={"default_template_id": template_id},
            )
            assert chosen.status_code == 200, chosen.text
            assert chosen.json()["default_template_id"] == template_id

            # Смена названия не сбрасывает шаблон раздела.
            renamed = client.patch(
                f"/api/v1/workspace/sections/{section_id}",
                json={"title": f"Дизайн 2 {suffix}"},
            )
            assert renamed.json()["default_template_id"] == template_id

            cleared = client.patch(
                f"/api/v1/workspace/sections/{section_id}",
                json={"default_template_id": None},
            )
            assert cleared.json()["default_template_id"] is None

            foreign = client.patch(
                f"/api/v1/workspace/sections/{section_id}",
                json={"default_template_id": str(uuid.uuid4())},
            )
            assert foreign.status_code == 404
    finally:
        async with SessionLocal() as db:
            if section_id:
                await db.execute(
                    delete(TaskSection).where(TaskSection.id == uuid.UUID(section_id))
                )
            if template_id:
                await db.execute(
                    delete(TaskTemplate).where(TaskTemplate.id == uuid.UUID(template_id))
                )
            await db.commit()
