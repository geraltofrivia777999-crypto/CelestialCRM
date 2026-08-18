"""Разделы доски задач и доступ к ним — ТЗ 8.1."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import (
    AuditEvent,
    Permission,
    Role,
    RolePermission,
    Session,
    Task,
    TaskAssignee,
    TaskSection,
    TaskSectionAccess,
    User,
    UserParent,
)


@pytest.fixture
async def sections(database):
    """Два человека с разными ролями и чистая доска с одним разделом."""
    suffix = uuid.uuid4().hex[:8]
    password = "sections-password"

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        view = await db.scalar(select(Permission).where(Permission.code == "workspace.view"))
        assert admin is not None and view is not None

        await db.execute(delete(TaskAssignee))
        await db.execute(delete(Task).where(Task.workspace_id == admin.workspace_id))
        await db.execute(
            delete(TaskSectionAccess).where(
                TaskSectionAccess.workspace_id == admin.workspace_id
            )
        )
        # Разделы, оставшиеся от других тестов, убираем — кроме созданного seed.
        await db.execute(
            delete(TaskSection).where(
                TaskSection.workspace_id == admin.workspace_id,
                TaskSection.title != "Общие задачи",
            )
        )

        roles = {}
        users = {}
        for key in ("design", "buying"):
            role = Role(
                workspace_id=admin.workspace_id,
                name=f"{key.title()} {suffix}",
                permissions=[view],
            )
            db.add(role)
            await db.flush()
            roles[key] = role.id
            user = User(
                workspace_id=admin.workspace_id,
                role_id=role.id,
                name=key.title(),
                login=f"sections-{key}-{suffix}",
                password_hash=hash_password(password),
            )
            db.add(user)
            users[key] = user
        await db.commit()

        result = {
            "password": password,
            "workspace_id": admin.workspace_id,
            "roles": {key: str(value) for key, value in roles.items()},
            "users": {
                key: {"id": str(user.id), "login": user.login}
                for key, user in users.items()
            },
        }

    yield result

    user_ids = [uuid.UUID(row["id"]) for row in result["users"].values()]
    role_ids = [uuid.UUID(value) for value in result["roles"].values()]
    async with SessionLocal() as db:
        await db.execute(delete(TaskAssignee))
        await db.execute(delete(Task).where(Task.workspace_id == result["workspace_id"]))
        await db.execute(
            delete(TaskSectionAccess).where(
                TaskSectionAccess.workspace_id == result["workspace_id"]
            )
        )
        await db.execute(
            delete(TaskSection).where(
                TaskSection.workspace_id == result["workspace_id"],
                TaskSection.title != "Общие задачи",
            )
        )
        await db.execute(delete(Session).where(Session.user_id.in_(user_ids)))
        await db.execute(delete(AuditEvent).where(AuditEvent.user_id.in_(user_ids)))
        await db.execute(
            delete(UserParent).where(
                or_(UserParent.user_id.in_(user_ids), UserParent.parent_id.in_(user_ids))
            )
        )
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.execute(delete(RolePermission).where(RolePermission.role_id.in_(role_ids)))
        await db.execute(delete(Role).where(Role.id.in_(role_ids)))
        await db.commit()


def _login(login: str, password: str) -> TestClient:
    client = TestClient(app)
    assert client.post(
        "/api/v1/auth/login", json={"login": login, "password": password}
    ).status_code == 200
    return client


def _admin() -> TestClient:
    return _login("admin", "test-password")


def _open(client: TestClient, section_id: str | None = None) -> dict:
    params = {"section_id": section_id} if section_id else {}
    response = client.get("/api/v1/workspace/board", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_board_starts_with_one_section(sections) -> None:
    with _admin() as client:
        board = _open(client)
        titles = [row["title"] for row in board["sections"]]
        created = client.post("/api/v1/workspace/tasks", json={"title": "Без раздела"})

    assert titles == ["Общие задачи"]
    assert board["section_id"] == board["sections"][0]["id"]
    # Клиент может ничего не знать про разделы — задача всё равно ложится на
    # доску, а не отбивается ошибкой.
    assert created.status_code == 201
    assert created.json()["section_id"] == board["sections"][0]["id"]


async def test_a_section_shows_only_its_own_tasks(sections) -> None:
    with _admin() as client:
        base = _open(client)["sections"][0]["id"]
        design = client.post(
            "/api/v1/workspace/sections", json={"title": "Дизайнеры"}
        ).json()["id"]
        client.post("/api/v1/workspace/tasks", json={"title": "Баннер", "section_id": design})
        client.post("/api/v1/workspace/tasks", json={"title": "Пролив", "section_id": base})

        design_board = _open(client, design)
        base_board = _open(client, base)

    assert [task["title"] for column in design_board["columns"] for task in column["tasks"]] == [
        "Баннер"
    ]
    assert [task["title"] for column in base_board["columns"] for task in column["tasks"]] == [
        "Пролив"
    ]
    # Счётчик на вкладке считает весь раздел, а не только видимую колонку.
    assert {row["title"]: row["tasks"] for row in design_board["sections"]} == {
        "Общие задачи": 1,
        "Дизайнеры": 1,
    }


async def test_a_named_rule_closes_the_section_for_everyone_else(sections) -> None:
    password = sections["password"]
    with _admin() as client:
        design = client.post(
            "/api/v1/workspace/sections", json={"title": "Дизайнеры"}
        ).json()["id"]
        client.post(
            "/api/v1/workspace/tasks", json={"title": "Креатив", "section_id": design}
        )
        assert client.put(
            f"/api/v1/workspace/sections/{design}/access",
            json={
                "rules": [
                    {"role_id": sections["roles"]["design"], "can_view": True, "can_create": True}
                ]
            },
        ).status_code == 200

    with _login(sections["users"]["design"]["login"], password) as designer:
        allowed = _open(designer, design)
    with _login(sections["users"]["buying"]["login"], password) as buyer:
        visible = _open(buyer)
        denied = buyer.get("/api/v1/workspace/board", params={"section_id": design})
        forbidden = buyer.post(
            "/api/v1/workspace/tasks", json={"title": "Чужой раздел", "section_id": design}
        )

    assert [row["title"] for row in allowed["sections"]] == ["Общие задачи", "Дизайнеры"]
    # Баер раздел не видит вовсе — ни во вкладках, ни по прямой ссылке.
    assert [row["title"] for row in visible["sections"]] == ["Общие задачи"]
    assert denied.status_code == 403
    assert forbidden.status_code == 403


async def test_the_section_right_lets_a_lead_edit_other_cards(sections) -> None:
    password = sections["password"]
    with _admin() as client:
        design = client.post(
            "/api/v1/workspace/sections", json={"title": "Дизайнеры"}
        ).json()["id"]
        foreign = client.post(
            "/api/v1/workspace/tasks", json={"title": "Чужая карточка", "section_id": design}
        ).json()["id"]
        client.put(
            f"/api/v1/workspace/sections/{design}/access",
            json={
                "rules": [
                    {
                        "user_id": sections["users"]["design"]["id"],
                        "can_view": True,
                        "can_create": True,
                        "can_edit": True,
                    },
                    {
                        "user_id": sections["users"]["buying"]["id"],
                        "can_view": True,
                        "can_create": True,
                    },
                ]
            },
        )

    with _login(sections["users"]["design"]["login"], password) as lead:
        renamed = lead.patch(
            f"/api/v1/workspace/tasks/{foreign}", json={"title": "Переписал"}
        )
        removed = lead.delete(f"/api/v1/workspace/tasks/{foreign}")
    with _login(sections["users"]["buying"]["login"], password) as buyer:
        rejected = buyer.patch(
            f"/api/v1/workspace/tasks/{foreign}", json={"title": "И я тоже"}
        )

    assert renamed.status_code == 200
    # Правка чужих карточек не даёт их удалять: это отдельное право.
    assert removed.status_code == 403
    assert rejected.status_code == 403


async def test_moving_a_card_needs_the_right_to_create_in_the_target(sections) -> None:
    password = sections["password"]
    with _admin() as client:
        base = _open(client)["sections"][0]["id"]
        design = client.post(
            "/api/v1/workspace/sections", json={"title": "Дизайнеры"}
        ).json()["id"]
        client.put(
            f"/api/v1/workspace/sections/{design}/access",
            json={
                "rules": [
                    {"role_id": sections["roles"]["design"], "can_view": True, "can_create": True}
                ]
            },
        )

    with _login(sections["users"]["buying"]["login"], password) as buyer:
        own = buyer.post(
            "/api/v1/workspace/tasks", json={"title": "Своя", "section_id": base}
        ).json()["id"]
        blocked = buyer.patch(
            f"/api/v1/workspace/tasks/{own}", json={"section_id": design}
        )
    with _admin() as client:
        moved = client.patch(f"/api/v1/workspace/tasks/{own}", json={"section_id": design})

    assert blocked.status_code == 403
    assert moved.status_code == 200
    assert moved.json()["section_id"] == design


async def test_a_section_with_tasks_is_not_deleted_silently(sections) -> None:
    with _admin() as client:
        base = _open(client)["sections"][0]["id"]
        design = client.post(
            "/api/v1/workspace/sections", json={"title": "Дизайнеры"}
        ).json()["id"]
        client.post("/api/v1/workspace/tasks", json={"title": "Баннер", "section_id": design})

        refused = client.delete(f"/api/v1/workspace/sections/{design}")
        moved = client.delete(
            f"/api/v1/workspace/sections/{design}", params={"move_to": base}
        )
        board = _open(client, base)
        last = client.delete(f"/api/v1/workspace/sections/{base}")

    assert refused.status_code == 422
    assert "1 задач" in refused.json()["error"]["message"]
    assert moved.status_code == 200 and moved.json()["moved"] == 1
    assert [task["title"] for column in board["columns"] for task in column["tasks"]] == [
        "Баннер"
    ]
    # Последний раздел не удаляется: карточке некуда было бы лечь.
    assert last.status_code == 422


async def test_two_sections_cannot_share_a_title(sections) -> None:
    with _admin() as client:
        first = client.post("/api/v1/workspace/sections", json={"title": "Дизайнеры"})
        again = client.post("/api/v1/workspace/sections", json={"title": "дизайнеры"})

    assert first.status_code == 201
    assert again.status_code == 422
