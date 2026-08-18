"""Card-level permissions and portable Workspace task search."""

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
    TaskField,
    TaskStatus,
    TaskTemplate,
    User,
    UserParent,
    Workspace,
)


@pytest.fixture
async def workspace_task_users(database):
    suffix = uuid.uuid4().hex[:8]
    password = "workspace-password"

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        view = await db.scalar(
            select(Permission).where(Permission.code == "workspace.view")
        )
        manage = await db.scalar(
            select(Permission).where(Permission.code == "workspace.manage")
        )
        assert admin is not None and view is not None and manage is not None

        # Each test starts with a clean board, independent of execution order.
        await db.execute(delete(TaskAssignee))
        await db.execute(delete(Task).where(Task.workspace_id == admin.workspace_id))
        await db.execute(
            delete(TaskTemplate).where(TaskTemplate.workspace_id == admin.workspace_id)
        )
        await db.execute(
            delete(TaskField).where(TaskField.workspace_id == admin.workspace_id)
        )
        await db.execute(
            delete(TaskStatus).where(
                TaskStatus.workspace_id == admin.workspace_id,
                TaskStatus.is_system.is_(False),
            )
        )

        viewer_role = Role(
            workspace_id=admin.workspace_id,
            name=f"Workspace viewer {suffix}",
            permissions=[view],
        )
        manager_role = Role(
            workspace_id=admin.workspace_id,
            name=f"Workspace manager {suffix}",
            permissions=[view, manage],
        )
        db.add_all([viewer_role, manager_role])
        await db.flush()

        users = {}
        for key, role in (
            ("creator", viewer_role),
            ("assignee", viewer_role),
            ("stranger", viewer_role),
            ("manager", manager_role),
        ):
            user = User(
                workspace_id=admin.workspace_id,
                role_id=role.id,
                name=key.title(),
                login=f"workspace-{key}-{suffix}",
                password_hash=hash_password(password),
            )
            db.add(user)
            users[key] = user

        await db.commit()
        result = {
            "password": password,
            "workspace_id": admin.workspace_id,
            "users": {
                key: {"id": user.id, "login": user.login}
                for key, user in users.items()
            },
            "role_ids": [viewer_role.id, manager_role.id],
        }

    yield result

    user_ids = [row["id"] for row in result["users"].values()]
    async with SessionLocal() as db:
        await db.execute(delete(TaskAssignee))
        await db.execute(
            delete(Task).where(Task.workspace_id == result["workspace_id"])
        )
        await db.execute(
            delete(TaskTemplate).where(
                TaskTemplate.workspace_id == result["workspace_id"]
            )
        )
        await db.execute(
            delete(TaskField).where(TaskField.workspace_id == result["workspace_id"])
        )
        await db.execute(
            delete(TaskStatus).where(
                TaskStatus.workspace_id == result["workspace_id"],
                TaskStatus.is_system.is_(False),
            )
        )
        await db.execute(delete(Session).where(Session.user_id.in_(user_ids)))
        await db.execute(delete(AuditEvent).where(AuditEvent.user_id.in_(user_ids)))
        await db.execute(
            delete(UserParent).where(
                or_(
                    UserParent.user_id.in_(user_ids),
                    UserParent.parent_id.in_(user_ids),
                )
            )
        )
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.execute(
            delete(RolePermission).where(
                RolePermission.role_id.in_(result["role_ids"])
            )
        )
        await db.execute(delete(Role).where(Role.id.in_(result["role_ids"])))
        await db.commit()


def _client_for(user: dict, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"login": user["login"], "password": password},
    )
    assert response.status_code == 200
    return client


async def test_title_search_casefolds_cyrillic_on_sqlite_and_postgres(
    workspace_task_users,
) -> None:
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        ).status_code == 200
        client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Собрать КРЕАТИВЫ для запуска"},
        )
        found = client.get(
            "/api/v1/workspace/board", params={"search": "креативы"}
        )

    assert found.status_code == 200
    assert found.json()["total"] == 1
    assert found.json()["columns"][0]["tasks"][0]["title"] == (
        "Собрать КРЕАТИВЫ для запуска"
    )


async def test_viewer_can_create_but_cannot_change_someone_elses_task(
    workspace_task_users,
) -> None:
    users = workspace_task_users["users"]
    password = workspace_task_users["password"]

    with TestClient(app) as admin:
        admin.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        )
        foreign = admin.post(
            "/api/v1/workspace/tasks", json={"title": "Чужая задача"}
        ).json()
        status_id = admin.get("/api/v1/workspace/board").json()["columns"][1]["id"]

    with _client_for(users["stranger"], password) as stranger:
        row = stranger.get("/api/v1/workspace/board").json()["columns"][0]["tasks"][0]
        assert row["can_edit"] is False
        assert row["can_delete"] is False
        assert stranger.patch(
            f"/api/v1/workspace/tasks/{foreign['id']}",
            json={"title": "Попытка изменения"},
        ).status_code == 403
        assert stranger.post(
            f"/api/v1/workspace/tasks/{foreign['id']}/move",
            json={"status_id": status_id, "position": 0},
        ).status_code == 403
        assert stranger.delete(
            f"/api/v1/workspace/tasks/{foreign['id']}"
        ).status_code == 403

        own = stranger.post(
            "/api/v1/workspace/tasks", json={"title": "Своя задача"}
        )
        assert own.status_code == 201
        assert own.json()["can_edit"] is True
        assert own.json()["can_delete"] is True


async def test_creator_assignee_and_manager_can_change_a_task(
    workspace_task_users,
) -> None:
    users = workspace_task_users["users"]
    password = workspace_task_users["password"]

    with _client_for(users["creator"], password) as creator:
        own = creator.post(
            "/api/v1/workspace/tasks", json={"title": "Задача автора"}
        ).json()
        assert creator.patch(
            f"/api/v1/workspace/tasks/{own['id']}",
            json={"title": "Автор изменил"},
        ).status_code == 200

    with TestClient(app) as admin:
        admin.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        )
        assigned = admin.post(
            "/api/v1/workspace/tasks",
            json={
                "title": "Задача исполнителя",
                "assignee_ids": [str(users["assignee"]["id"])],
            },
        ).json()
        managed = admin.post(
            "/api/v1/workspace/tasks", json={"title": "Задача для менеджера"}
        ).json()
        done_id = next(
            column["id"]
            for column in admin.get("/api/v1/workspace/board").json()["columns"]
            if column["code"] == "done"
        )

    with _client_for(users["assignee"], password) as assignee:
        assigned_row = next(
            task
            for column in assignee.get("/api/v1/workspace/board").json()["columns"]
            for task in column["tasks"]
            if task["id"] == assigned["id"]
        )
        assert assigned_row["can_edit"] is True
        assert assigned_row["can_delete"] is True
        assert assignee.patch(
            f"/api/v1/workspace/tasks/{assigned['id']}",
            json={"title": "Исполнитель изменил"},
        ).status_code == 200
        assert assignee.post(
            f"/api/v1/workspace/tasks/{assigned['id']}/move",
            json={"status_id": done_id, "position": 0},
        ).status_code == 200
        assert assignee.delete(
            f"/api/v1/workspace/tasks/{assigned['id']}"
        ).status_code == 200

    with _client_for(users["manager"], password) as manager:
        managed_row = next(
            task
            for column in manager.get("/api/v1/workspace/board").json()["columns"]
            for task in column["tasks"]
            if task["id"] == managed["id"]
        )
        assert managed_row["can_edit"] is True
        assert managed_row["can_delete"] is True
        assert manager.patch(
            f"/api/v1/workspace/tasks/{managed['id']}",
            json={"title": "Менеджер изменил"},
        ).status_code == 200
        assert manager.delete(
            f"/api/v1/workspace/tasks/{managed['id']}"
        ).status_code == 200


async def test_template_rejects_fields_from_another_workspace(
    workspace_task_users,
) -> None:
    """Шаблон задаёт набор полей — чужое поле в нём открыло бы чужие значения."""
    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as db:
        foreign_workspace = Workspace(name=f"Foreign Workspace {suffix}")
        db.add(foreign_workspace)
        await db.flush()
        foreign_field = TaskField(
            workspace_id=foreign_workspace.id,
            name=f"Чужое поле {suffix}",
            kind="text",
        )
        db.add(foreign_field)
        await db.commit()
        foreign = {
            "workspace_id": foreign_workspace.id,
            "field_id": foreign_field.id,
        }

    try:
        with TestClient(app) as admin:
            admin.post(
                "/api/v1/auth/login",
                json={"login": "admin", "password": "test-password"},
            )
            assert admin.post(
                "/api/v1/workspace/task-templates",
                json={"name": "Чужое поле", "field_ids": [str(foreign["field_id"])]},
            ).status_code == 422

            valid = admin.post(
                "/api/v1/workspace/task-templates", json={"name": "Безопасный шаблон"}
            )
            assert valid.status_code == 201
            template_id = valid.json()["id"]
            assert admin.patch(
                f"/api/v1/workspace/task-templates/{template_id}",
                json={"name": "Безопасный шаблон", "field_ids": [str(foreign["field_id"])]},
            ).status_code == 422

            stored = next(
                row
                for row in admin.get("/api/v1/workspace/task-templates").json()["items"]
                if row["id"] == template_id
            )
            assert stored["field_ids"] == []
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(TaskField).where(TaskField.id == foreign["field_id"]))
            await db.execute(
                delete(Workspace).where(Workspace.id == foreign["workspace_id"])
            )
            await db.commit()


async def test_workspace_names_and_task_titles_cannot_be_whitespace_only(
    workspace_task_users,
) -> None:
    with TestClient(app) as admin:
        admin.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        )
        board = admin.get("/api/v1/workspace/board").json()
        status_id = board["columns"][0]["id"]

        assert admin.post(
            "/api/v1/workspace/tasks", json={"title": "   "}
        ).status_code == 422
        task = admin.post(
            "/api/v1/workspace/tasks", json={"title": "Нормальная задача"}
        ).json()
        assert admin.patch(
            f"/api/v1/workspace/tasks/{task['id']}", json={"title": "\t  "}
        ).status_code == 422

        assert admin.post(
            "/api/v1/workspace/statuses", json={"name": "   "}
        ).status_code == 422
        assert admin.patch(
            f"/api/v1/workspace/statuses/{status_id}", json={"name": "\n  "}
        ).status_code == 422

        assert admin.post(
            "/api/v1/workspace/fields", json={"name": "   ", "kind": "text"}
        ).status_code == 422
        field = admin.post(
            "/api/v1/workspace/fields", json={"name": "Нормальное поле", "kind": "text"}
        ).json()
        assert admin.patch(
            f"/api/v1/workspace/fields/{field['id']}", json={"name": "\t"}
        ).status_code == 422

        assert admin.post(
            "/api/v1/workspace/task-templates", json={"name": "   "}
        ).status_code == 422
        template = admin.post(
            "/api/v1/workspace/task-templates", json={"name": "Нормальный шаблон"}
        ).json()
        assert admin.patch(
            f"/api/v1/workspace/task-templates/{template['id']}",
            json={"name": "\n\t"},
        ).status_code == 422
