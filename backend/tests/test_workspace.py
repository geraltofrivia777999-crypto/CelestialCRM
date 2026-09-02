"""Workspace: канбан-доска и база знаний (ТЗ 8)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    ArticleStatus,
    KnowledgeAccess,
    KnowledgeArticle,
    KnowledgeAttachment,
    KnowledgeSection,
    Role,
    Session,
    Task,
    TaskAssignee,
    TaskField,
    TaskStatus,
    TaskTemplate,
    User,
    UserParent,
)
from app.services.knowledge import blocks_to_text, normalize_blocks
from tests.test_media_finance import _admin_client


@pytest.fixture
async def workspace(database):
    """Чистая доска и пустая база знаний для каждого теста."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        ids = {"workspace": admin.workspace_id, "admin": admin.id}

    yield ids

    async with SessionLocal() as db:
        await db.execute(
            delete(KnowledgeAttachment).where(
                KnowledgeAttachment.workspace_id == ids["workspace"]
            )
        )
        await db.execute(
            delete(KnowledgeAccess).where(KnowledgeAccess.workspace_id == ids["workspace"])
        )
        await db.execute(
            delete(KnowledgeArticle).where(KnowledgeArticle.workspace_id == ids["workspace"])
        )
        await db.execute(
            delete(KnowledgeSection).where(KnowledgeSection.workspace_id == ids["workspace"])
        )
        await db.execute(delete(TaskAssignee))
        await db.execute(delete(Task).where(Task.workspace_id == ids["workspace"]))
        await db.execute(
            delete(TaskTemplate).where(TaskTemplate.workspace_id == ids["workspace"])
        )
        await db.execute(delete(TaskField).where(TaskField.workspace_id == ids["workspace"]))
        await db.execute(
            delete(TaskStatus).where(
                TaskStatus.workspace_id == ids["workspace"],
                TaskStatus.is_system.is_(False),
            )
        )
        await db.commit()


async def _columns(client) -> list[dict]:
    return client.get("/api/v1/workspace/board").json()["columns"]


async def test_the_five_default_columns_exist(workspace) -> None:
    with _admin_client() as client:
        columns = await _columns(client)
    assert [column["code"] for column in columns] == [
        "open", "in_progress", "review", "done", "archive"
    ]
    # Готово и Архив завершают задачу, остальные — нет.
    assert [column["is_terminal"] for column in columns] == [False, False, False, True, True]


async def test_a_task_lands_in_the_first_column(workspace) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Собрать креативы", "priority": "high"},
        )
        assert response.status_code == 201
        columns = await _columns(client)

    assert response.json()["title"] == "Собрать креативы"
    assert columns[0]["count"] == 1
    assert columns[0]["tasks"][0]["priority"] == "high"


async def test_dragging_a_card_reorders_the_column(workspace) -> None:
    with _admin_client() as client:
        for title in ("Первая", "Вторая", "Третья"):
            client.post("/api/v1/workspace/tasks", json={"title": title})
        columns = await _columns(client)
        open_column = columns[0]
        assert [task["title"] for task in open_column["tasks"]] == [
            "Первая", "Вторая", "Третья"
        ]

        third = open_column["tasks"][2]["id"]
        moved = client.post(
            f"/api/v1/workspace/tasks/{third}/move",
            json={"status_id": open_column["id"], "position": 0},
        )
        assert moved.status_code == 200
        after = (await _columns(client))[0]

    assert [task["title"] for task in after["tasks"]] == ["Третья", "Первая", "Вторая"]


async def test_moving_into_a_terminal_column_marks_the_task_done(workspace) -> None:
    with _admin_client() as client:
        task_id = client.post(
            "/api/v1/workspace/tasks", json={"title": "Закрыть"}
        ).json()["id"]
        columns = await _columns(client)
        done = next(column for column in columns if column["code"] == "done")
        client.post(
            f"/api/v1/workspace/tasks/{task_id}/move",
            json={"status_id": done["id"], "position": 0},
        )
        after = await _columns(client)

    finished = next(column for column in after if column["code"] == "done")
    assert finished["tasks"][0]["is_done"] is True


async def test_filters_narrow_cards_but_keep_every_column(workspace) -> None:
    with _admin_client() as client:
        client.post("/api/v1/workspace/tasks", json={"title": "Баннеры для DE", "priority": "low"})
        client.post("/api/v1/workspace/tasks", json={"title": "Отчёт", "priority": "critical"})

        found = client.get("/api/v1/workspace/board?search=баннер").json()
        by_priority = client.get("/api/v1/workspace/board?priority=critical").json()

    assert found["total"] == 1
    assert found["columns"][0]["tasks"][0]["title"] == "Баннеры для DE"
    # Пустые колонки обязаны остаться: в них перетаскивают задачи.
    assert len(found["columns"]) == 5
    assert by_priority["total"] == 1
    assert by_priority["columns"][0]["tasks"][0]["title"] == "Отчёт"


async def test_sorting_by_due_date_puts_undated_tasks_last(workspace) -> None:
    soon = (date.today() + timedelta(days=1)).isoformat()
    later = (date.today() + timedelta(days=9)).isoformat()
    with _admin_client() as client:
        client.post("/api/v1/workspace/tasks", json={"title": "Без срока"})
        client.post("/api/v1/workspace/tasks", json={"title": "Поздняя", "due_date": later})
        client.post("/api/v1/workspace/tasks", json={"title": "Срочная", "due_date": soon})
        columns = client.get("/api/v1/workspace/board?sort=due_date").json()["columns"]

    assert [task["title"] for task in columns[0]["tasks"]] == [
        "Срочная", "Поздняя", "Без срока"
    ]


async def test_overdue_tasks_are_counted(workspace) -> None:
    past = (date.today() - timedelta(days=3)).isoformat()
    with _admin_client() as client:
        client.post("/api/v1/workspace/tasks", json={"title": "Горит", "due_date": past})
        board = client.get("/api/v1/workspace/board").json()
    assert board["overdue"] == 1


async def test_several_assignees_fit_on_one_task(workspace) -> None:
    async with SessionLocal() as db:
        role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        people = [
            User(
                workspace_id=workspace["workspace"],
                role_id=role.id,
                name=f"Исполнитель {index}",
                login=f"wsperson{index}",
                password_hash=hash_password("x" * 12),
            )
            for index in range(2)
        ]
        db.add_all(people)
        await db.commit()
        ids = [str(person.id) for person in people]

    try:
        with _admin_client() as client:
            created = client.post(
                "/api/v1/workspace/tasks",
                json={"title": "На двоих", "assignee_ids": ids},
            )
            assert created.status_code == 201
            assert sorted(created.json()["assignee_ids"]) == sorted(ids)

            # Замена списка не должна плодить дубли.
            updated = client.patch(
                f"/api/v1/workspace/tasks/{created.json()['id']}",
                json={"assignee_ids": [ids[0]]},
            )
            assert updated.json()["assignee_ids"] == [ids[0]]

        async with SessionLocal() as db:
            links = await db.scalar(select(func.count()).select_from(TaskAssignee))
            assert links == 1
    finally:
        async with SessionLocal() as db:
            for value in ids:
                await db.execute(delete(Session).where(Session.user_id == uuid.UUID(value)))
                await db.execute(delete(UserParent).where(UserParent.user_id == uuid.UUID(value)))
                await db.execute(delete(User).where(User.id == uuid.UUID(value)))
            await db.commit()


async def test_a_stranger_cannot_be_assigned(workspace) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Чужому", "assignee_ids": [str(uuid.uuid4())]},
        )
    assert response.status_code == 422
    assert "исполнител" in response.json()["error"]["message"].lower()


async def test_custom_fields_are_validated_by_type(workspace) -> None:
    with _admin_client() as client:
        number = client.post(
            "/api/v1/workspace/fields", json={"name": "Бюджет", "kind": "number"}
        ).json()
        choice = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Источник", "kind": "select", "options": ["FB", "Google"]},
        ).json()

        bad_number = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Плохое число", "custom_values": {number["id"]: "много"}},
        )
        bad_choice = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Плохой выбор", "custom_values": {choice["id"]: "TikTok"}},
        )
        good = client.post(
            "/api/v1/workspace/tasks",
            json={
                "title": "Нормальная",
                "custom_values": {number["id"]: "500", choice["id"]: "FB"},
            },
        )

    assert bad_number.status_code == 422
    assert bad_choice.status_code == 422
    assert good.status_code == 201
    assert good.json()["custom_values"][number["id"]] == 500.0
    assert good.json()["custom_values"][choice["id"]] == "FB"


async def test_a_select_field_needs_at_least_one_option(workspace) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/workspace/fields", json={"name": "Пустой список", "kind": "select"}
        )
    assert response.status_code == 422


async def test_a_required_field_blocks_saving_without_it(workspace) -> None:
    with _admin_client() as client:
        client.post(
            "/api/v1/workspace/fields",
            json={"name": "Ответственный отдел", "kind": "text", "is_required": True},
        )
        response = client.post("/api/v1/workspace/tasks", json={"title": "Без отдела"})
    assert response.status_code == 422
    assert "Ответственный отдел" in response.json()["error"]["message"]


async def test_deleting_a_field_clears_it_from_tasks(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields", json={"name": "Временное", "kind": "text"}
        ).json()
        task = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "С полем", "custom_values": {field["id"]: "значение"}},
        ).json()
        assert task["custom_values"][field["id"]] == "значение"

        client.delete(f"/api/v1/workspace/fields/{field['id']}")
        board = client.get("/api/v1/workspace/board").json()

    stored = board["columns"][0]["tasks"][0]
    assert stored["custom_values"] == {}


async def test_a_template_fills_only_its_own_fields(workspace) -> None:
    """Стандартные поля карточки шаблон не трогает — их вводят каждый раз."""
    with _admin_client() as client:
        kind = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Вид крео", "kind": "select", "options": ["Видео", "Статика"],
                  "show_always": False},
        ).json()
        template = client.post(
            "/api/v1/workspace/task-templates",
            json={
                "name": "Бриф на креатив",
                "field_ids": [kind["id"]],
                "custom_values": {kind["id"]: "Видео"},
            },
        ).json()

        from_template = client.post(
            "/api/v1/workspace/tasks",
            json={"template_id": template["id"], "title": "Крео для DE"},
        ).json()

    assert from_template["title"] == "Крео для DE"
    # Значение поля шаблона подставилось, а приоритет остался обычным.
    assert from_template["custom_values"][kind["id"]] == "Видео"
    assert from_template["priority"] == "medium"
    assert from_template["field_ids"] == [kind["id"]]


async def test_a_system_column_cannot_be_deleted(workspace) -> None:
    with _admin_client() as client:
        columns = await _columns(client)
        response = client.delete(f"/api/v1/workspace/statuses/{columns[0]['id']}")
    assert response.status_code == 422
    assert "Базовые" in response.json()["error"]["message"]


async def test_a_custom_column_takes_its_tasks_along_when_deleted(workspace) -> None:
    with _admin_client() as client:
        column = client.post(
            "/api/v1/workspace/statuses", json={"name": "Блокеры", "color": "#B91414"}
        ).json()
        task = client.post(
            "/api/v1/workspace/tasks", json={"title": "Заблокировано", "status_id": column["id"]}
        ).json()

        without_target = client.delete(f"/api/v1/workspace/statuses/{column['id']}")
        assert without_target.status_code == 422

        columns = await _columns(client)
        open_column = next(row for row in columns if row["code"] == "open")
        moved = client.delete(
            f"/api/v1/workspace/statuses/{column['id']}?move_to={open_column['id']}"
        )
        assert moved.status_code == 200

        after = await _columns(client)

    landing = next(row for row in after if row["code"] == "open")
    assert [row["id"] for row in landing["tasks"]] == [task["id"]]


async def test_a_buyer_can_use_the_board_but_not_reshape_it(workspace) -> None:
    async with SessionLocal() as db:
        role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        buyer = User(
            workspace_id=workspace["workspace"],
            role_id=role.id,
            name="Доска баера",
            login="wsboardbuyer",
            password_hash=hash_password("board-password"),
        )
        db.add(buyer)
        await db.commit()
        buyer_id = buyer.id

    try:
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/login",
                json={"login": "wsboardbuyer", "password": "board-password"},
            )
            assert login.status_code == 200
            assert client.get("/api/v1/workspace/board").status_code == 200
            assert client.post(
                "/api/v1/workspace/tasks", json={"title": "Моя задача"}
            ).status_code == 201
            # Колонки и поля — структура доски, её меняет только руководство.
            assert client.post(
                "/api/v1/workspace/statuses", json={"name": "Своя колонка"}
            ).status_code == 403
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Session).where(Session.user_id == buyer_id))
            await db.execute(delete(UserParent).where(UserParent.user_id == buyer_id))
            await db.execute(delete(User).where(User.id == buyer_id))
            await db.commit()


# --- база знаний (ТЗ 8.2) ----------------------------------------------------


def test_script_tags_never_survive_a_block() -> None:
    blocks = normalize_blocks(
        [
            {"type": "paragraph", "text": "<b>жирный</b><script>alert(1)</script>"},
            {"type": "paragraph", "text": '<a href="javascript:alert(1)">клик</a>'},
            {"type": "выдумка", "text": "мусор"},
        ]
    )
    assert len(blocks) == 2
    assert blocks[0]["text"] == "<b>жирный</b>&lt;script&gt;alert(1)&lt;/script&gt;"
    # Ссылка на javascript: не остаётся ссылкой — тег экранирован целиком.
    assert "javascript:" not in blocks[1]["text"] or "<a" not in blocks[1]["text"]


def test_an_https_link_stays_a_link() -> None:
    blocks = normalize_blocks(
        [{"type": "paragraph", "text": '<a href="https://example.com">док</a>'}]
    )
    assert blocks[0]["text"] == '<a href="https://example.com">док</a>'


def test_editor_div_wrappers_become_line_breaks() -> None:
    blocks = normalize_blocks(
        [
            {
                "type": "paragraph",
                "text": "Первая строка<div>Вторая строка</div><div>Третья строка</div>",
            },
            {
                "type": "heading_2",
                "text": "Заголовок&lt;div&gt;&lt;/div&gt;",
            },
        ]
    )
    assert blocks[0]["text"] == "Первая строка<br>Вторая строка<br>Третья строка"
    assert blocks[1]["text"] == "Заголовок"


def test_search_text_flattens_every_block_kind() -> None:
    blocks = normalize_blocks(
        [
            {"type": "heading_1", "text": "Регламент"},
            {"type": "checklist", "items": [{"text": "проверить пиксель", "checked": True}]},
            {"type": "table", "rows": [["GEO", "ставка"], ["DE", "12"]]},
            {"type": "code", "text": "print('hi')", "language": "python"},
        ]
    )
    text = blocks_to_text(blocks)
    assert "Регламент" in text
    assert "проверить пиксель" in text
    assert "ставка" in text
    assert "print('hi')" in text


def test_a_media_block_without_a_source_is_dropped() -> None:
    blocks = normalize_blocks(
        [
            {"type": "image", "caption": "без файла"},
            {"type": "image", "attachment_id": str(uuid.uuid4()), "caption": "с файлом"},
            {"type": "divider"},
        ]
    )
    assert [block["type"] for block in blocks] == ["image", "divider"]
    assert blocks[0]["caption"] == "с файлом"


async def test_a_section_tree_is_returned_with_articles(workspace) -> None:
    with _admin_client() as client:
        root = client.post(
            "/api/v1/knowledge/sections", json={"title": "Регламенты", "icon": "📘"}
        ).json()
        child = client.post(
            "/api/v1/knowledge/sections",
            json={"title": "Meta", "parent_id": root["id"]},
        ).json()
        client.post(
            "/api/v1/knowledge/articles",
            json={"title": "Как заливать", "section_id": child["id"], "status": "published"},
        )
        tree = client.get("/api/v1/knowledge/tree").json()

    assert len(tree["sections"]) == 1
    assert tree["sections"][0]["title"] == "Регламенты"
    assert tree["sections"][0]["children"][0]["articles"][0]["title"] == "Как заливать"


async def test_a_section_with_content_is_not_deleted_silently(workspace) -> None:
    with _admin_client() as client:
        section = client.post(
            "/api/v1/knowledge/sections", json={"title": "С контентом"}
        ).json()
        client.post(
            "/api/v1/knowledge/articles",
            json={"title": "Внутри", "section_id": section["id"]},
        )
        response = client.delete(f"/api/v1/knowledge/sections/{section['id']}")
    assert response.status_code == 422
    assert "статей" in response.json()["error"]["message"]


async def test_a_section_cannot_be_moved_into_its_own_branch(workspace) -> None:
    with _admin_client() as client:
        root = client.post("/api/v1/knowledge/sections", json={"title": "Корень"}).json()
        child = client.post(
            "/api/v1/knowledge/sections", json={"title": "Ветка", "parent_id": root["id"]}
        ).json()
        response = client.patch(
            f"/api/v1/knowledge/sections/{root['id']}", json={"parent_id": child["id"]}
        )
    assert response.status_code == 422


async def test_search_finds_articles_by_body_and_shows_why(workspace) -> None:
    with _admin_client() as client:
        section = client.post("/api/v1/knowledge/sections", json={"title": "База"}).json()
        client.post(
            "/api/v1/knowledge/articles",
            json={
                "title": "Инструкция по кабинетам",
                "section_id": section["id"],
                "status": "published",
                "blocks": [
                    {"type": "paragraph", "text": "Токен системного пользователя живёт долго"}
                ],
            },
        )
        found = client.get("/api/v1/knowledge/articles/search?q=системного").json()
        short = client.get("/api/v1/knowledge/articles/search?q=с").json()

    assert found["total"] == 1
    assert "системного" in found["items"][0]["excerpt"]
    # Однобуквенный запрос ничего не ищет: результат был бы бессмысленным.
    assert short["total"] == 0


async def test_publishing_stamps_the_date_and_archiving_hides_from_search(workspace) -> None:
    with _admin_client() as client:
        article = client.post(
            "/api/v1/knowledge/articles",
            json={"title": "Черновик регламента", "blocks": [
                {"type": "paragraph", "text": "уникальноеслово"}
            ]},
        ).json()
        assert article["published_at"] is None

        published = client.patch(
            f"/api/v1/knowledge/articles/{article['id']}", json={"status": "published"}
        ).json()
        assert published["published_at"] is not None
        assert client.get(
            "/api/v1/knowledge/articles/search?q=уникальноеслово"
        ).json()["total"] == 1

        client.patch(
            f"/api/v1/knowledge/articles/{article['id']}", json={"status": "archived"}
        )
        assert client.get(
            "/api/v1/knowledge/articles/search?q=уникальноеслово"
        ).json()["total"] == 0


async def test_a_closed_section_disappears_for_the_role(workspace) -> None:
    """Правило доступа на разделе прячет его от чужой роли — ТЗ 8.2."""
    with _admin_client() as client:
        section = client.post(
            "/api/v1/knowledge/sections", json={"title": "Только для лидов"}
        ).json()
        client.post(
            "/api/v1/knowledge/articles",
            json={"title": "Зарплаты", "section_id": section["id"], "status": "published"},
        )

    async with SessionLocal() as db:
        lead_role = await db.scalar(select(Role).where(Role.name == "Team Lead"))
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        buyer = User(
            workspace_id=workspace["workspace"],
            role_id=buyer_role.id,
            name="Любопытный баер",
            login="wskbbuyer",
            password_hash=hash_password("kb-password"),
        )
        db.add(buyer)
        await db.commit()
        buyer_id = buyer.id
        lead_role_id = lead_role.id

    try:
        with _admin_client() as client:
            client.request(
                "PUT",
                f"/api/v1/knowledge/sections/{section['id']}/access",
                json={
                    "rules": [
                        {
                            "role_id": str(lead_role_id),
                            "can_view": True,
                            "can_edit": True,
                        }
                    ]
                },
            )

        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            client.post(
                "/api/v1/auth/login",
                json={"login": "wskbbuyer", "password": "kb-password"},
            )
            tree = client.get("/api/v1/knowledge/tree").json()
            titles = [row["title"] for row in tree["sections"]]
            assert "Только для лидов" not in titles
            # И поиском закрытую статью тоже не достать.
            assert client.get("/api/v1/knowledge/articles/search?q=Зарплаты").json()["total"] == 0
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Session).where(Session.user_id == buyer_id))
            await db.execute(delete(UserParent).where(UserParent.user_id == buyer_id))
            await db.execute(delete(User).where(User.id == buyer_id))
            await db.commit()


async def test_access_rules_are_inherited_by_nested_sections(workspace) -> None:
    with _admin_client() as client:
        root = client.post("/api/v1/knowledge/sections", json={"title": "Закрытый"}).json()
        client.post(
            "/api/v1/knowledge/sections", json={"title": "Вложенный", "parent_id": root["id"]}
        )

    async with SessionLocal() as db:
        lead_role = await db.scalar(select(Role).where(Role.name == "Team Lead"))
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        buyer = User(
            workspace_id=workspace["workspace"],
            role_id=buyer_role.id,
            name="Вложенный баер",
            login="wsnestedbuyer",
            password_hash=hash_password("nested-password"),
        )
        db.add(buyer)
        await db.commit()
        buyer_id = buyer.id
        lead_role_id = lead_role.id

    try:
        with _admin_client() as client:
            client.request(
                "PUT",
                f"/api/v1/knowledge/sections/{root['id']}/access",
                json={"rules": [{"role_id": str(lead_role_id), "can_view": True}]},
            )

        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            client.post(
                "/api/v1/auth/login",
                json={"login": "wsnestedbuyer", "password": "nested-password"},
            )
            tree = client.get("/api/v1/knowledge/tree").json()
        assert tree["sections"] == []
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Session).where(Session.user_id == buyer_id))
            await db.execute(delete(UserParent).where(UserParent.user_id == buyer_id))
            await db.execute(delete(User).where(User.id == buyer_id))
            await db.commit()


async def test_a_draft_is_hidden_from_readers_but_visible_to_its_author(workspace) -> None:
    with _admin_client() as client:
        section = client.post("/api/v1/knowledge/sections", json={"title": "Общий"}).json()
        client.post(
            "/api/v1/knowledge/articles",
            json={"title": "Недописанное", "section_id": section["id"], "status": "draft"},
        )
        own = client.get("/api/v1/knowledge/tree").json()

    assert own["sections"][0]["articles"][0]["title"] == "Недописанное"

    async with SessionLocal() as db:
        buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        buyer = User(
            workspace_id=workspace["workspace"],
            role_id=buyer_role.id,
            name="Читатель",
            login="wsdraftreader",
            password_hash=hash_password("draft-password"),
        )
        db.add(buyer)
        await db.commit()
        buyer_id = buyer.id

    try:
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            client.post(
                "/api/v1/auth/login",
                json={"login": "wsdraftreader", "password": "draft-password"},
            )
            tree = client.get("/api/v1/knowledge/tree").json()
        assert tree["sections"][0]["articles"] == []
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Session).where(Session.user_id == buyer_id))
            await db.execute(delete(UserParent).where(UserParent.user_id == buyer_id))
            await db.execute(delete(User).where(User.id == buyer_id))
            await db.commit()


async def test_an_attachment_round_trips_through_the_api(workspace) -> None:
    with _admin_client() as client:
        uploaded = client.post(
            "/api/v1/knowledge/attachments",
            files={"file": ("schema.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        )
        assert uploaded.status_code == 201
        payload = uploaded.json()
        assert payload["kind"] == "image"

        article = client.post(
            "/api/v1/knowledge/articles",
            json={
                "title": "Со схемой",
                "blocks": [
                    {"type": "image", "attachment_id": payload["id"], "caption": "схема"}
                ],
            },
        ).json()
        assert article["blocks"][0]["attachment_id"] == payload["id"]

        fetched = client.get(f"/api/v1/knowledge/attachments/{payload['id']}")
        assert fetched.status_code == 200
        assert fetched.headers["x-content-type-options"] == "nosniff"
        assert fetched.content.startswith(b"\x89PNG")

    async with SessionLocal() as db:
        attachment = await db.scalar(
            select(KnowledgeAttachment).where(
                KnowledgeAttachment.id == uuid.UUID(payload["id"])
            )
        )
        # Вложение привязалось к статье — по этой связи проверяются права на файл.
        assert str(attachment.article_id) == article["id"]


async def test_an_executable_upload_is_refused(workspace) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/knowledge/attachments",
            files={"file": ("payload.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
        )
    assert response.status_code == 422


async def test_an_article_with_children_is_not_deleted(workspace) -> None:
    with _admin_client() as client:
        parent = client.post(
            "/api/v1/knowledge/articles", json={"title": "Родитель"}
        ).json()
        client.post(
            "/api/v1/knowledge/articles",
            json={"title": "Вложенная", "parent_id": parent["id"]},
        )
        response = client.delete(f"/api/v1/knowledge/articles/{parent['id']}")
    assert response.status_code == 422
    assert "вложенных" in response.json()["error"]["message"]


async def test_reading_an_article_reports_the_rights_of_the_reader(workspace) -> None:
    with _admin_client() as client:
        article = client.post(
            "/api/v1/knowledge/articles",
            json={"title": "Права", "blocks": [{"type": "quote", "text": "цитата"}]},
        ).json()
        full = client.get(f"/api/v1/knowledge/articles/{article['id']}").json()

    assert full["rights"]["can_edit"] is True
    assert full["blocks"][0]["type"] == "quote"
    assert full["status"] == ArticleStatus.draft.value
    assert full["children"] == []


# --- типы полей и шаблоны с набором полей (ТЗ 8.1) ---------------------------


async def test_a_template_decides_which_fields_the_card_shows(workspace) -> None:
    """Поле бриф-шаблона не должно висеть в каждой задаче доски."""
    with _admin_client() as client:
        source = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Исходник", "kind": "file", "show_always": False},
        ).json()
        brief = client.post(
            "/api/v1/workspace/task-templates",
            json={"name": "Бриф на креатив", "field_ids": [source["id"]]},
        ).json()

        with_template = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео для DE", "template_id": brief["id"]},
        ).json()
        without = client.post(
            "/api/v1/workspace/tasks", json={"title": "Обычная задача"}
        ).json()

    assert with_template["template_id"] == brief["id"]
    assert with_template["field_ids"] == [source["id"]]
    assert without["field_ids"] == []


async def test_only_one_template_stays_the_default(workspace) -> None:
    """Два шаблона «по умолчанию» — это новая задача, открывающаяся то так, то так."""
    with _admin_client() as client:
        first = client.post(
            "/api/v1/workspace/task-templates",
            json={"name": "Бриф на крео", "is_default": True},
        ).json()
        second = client.post(
            "/api/v1/workspace/task-templates",
            json={"name": "Бриф на лендинг", "is_default": True},
        ).json()
        assert first["is_default"] is True
        assert second["is_default"] is True

        rows = client.get("/api/v1/workspace/task-templates").json()["items"]
        defaults = [row["name"] for row in rows if row["is_default"]]
        assert defaults == ["Бриф на лендинг"]

        # Возврат отметки первому снимает её со второго.
        client.patch(
            f"/api/v1/workspace/task-templates/{first['id']}", json={"is_default": True}
        )
        rows = client.get("/api/v1/workspace/task-templates").json()["items"]
        assert [row["name"] for row in rows if row["is_default"]] == ["Бриф на крео"]


async def test_template_fields_come_first_in_the_order_of_the_template(workspace) -> None:
    with _admin_client() as client:
        geo = client.post(
            "/api/v1/workspace/fields",
            json={"name": "ГЕО", "kind": "labels", "options": ["DE", "IT"],
                  "show_always": False},
        ).json()
        spec = client.post(
            "/api/v1/workspace/fields",
            json={"name": "ТЗ", "kind": "textarea", "show_always": False},
        ).json()
        shared = client.post(
            "/api/v1/workspace/fields", json={"name": "Комментарий", "kind": "text"}
        ).json()
        template = client.post(
            "/api/v1/workspace/task-templates",
            json={"name": "Бриф", "field_ids": [spec["id"], geo["id"]]},
        ).json()
        task = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Бриф DE", "template_id": template["id"]},
        ).json()

    # Порядок шаблона сохраняется, общее поле доски идёт после него.
    assert task["field_ids"] == [spec["id"], geo["id"], shared["id"]]


async def test_a_filled_field_stays_in_the_card_after_it_leaves_the_template(
    workspace,
) -> None:
    with _admin_client() as client:
        note = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Референс", "kind": "url", "show_always": False},
        ).json()
        template = client.post(
            "/api/v1/workspace/task-templates",
            json={"name": "Бриф", "field_ids": [note["id"]]},
        ).json()
        client.post(
            "/api/v1/workspace/tasks",
            json={
                "title": "Крео",
                "template_id": template["id"],
                "custom_values": {note["id"]: "https://example.com/ref"},
            },
        )
        client.patch(
            f"/api/v1/workspace/task-templates/{template['id']}", json={"field_ids": []}
        )
        board = client.get("/api/v1/workspace/board").json()

    stored = [row for column in board["columns"] for row in column["tasks"]][0]
    assert stored["field_ids"] == [note["id"]]
    assert stored["custom_values"][note["id"]] == "https://example.com/ref"


async def test_a_required_field_of_another_template_does_not_block_a_task(
    workspace,
) -> None:
    with _admin_client() as client:
        strict = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Номер договора", "kind": "text", "is_required": True,
                  "show_always": False},
        ).json()
        client.post(
            "/api/v1/workspace/task-templates",
            json={"name": "Договор", "field_ids": [strict["id"]]},
        )
        free = client.post("/api/v1/workspace/tasks", json={"title": "Без договора"})
    assert free.status_code == 201


async def test_money_is_stored_with_two_decimals_as_text(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Бюджет", "kind": "money", "config": {"currency": "EUR"}},
        ).json()
        task = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Закуп", "custom_values": {field["id"]: "1234,5"}},
        ).json()

    assert field["config"] == {"currency": "EUR"}
    # Именно строка: double не представляет 0.1, а это деньги.
    assert task["custom_values"][field["id"]] == "1234.50"


async def test_an_unsupported_currency_is_refused(workspace) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Бюджет", "kind": "money", "config": {"currency": "XXX"}},
        )
    assert response.status_code == 422


async def test_labels_take_several_values_and_refuse_unknown_ones(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields",
            json={"name": "ГЕО", "kind": "labels", "options": ["DE", "IT", "ES"]},
        ).json()
        good = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео", "custom_values": {field["id"]: ["DE", "ES", "DE"]}},
        )
        bad = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео", "custom_values": {field["id"]: ["DE", "PL"]}},
        )

    assert good.status_code == 201
    # Повтор схлопывается: две одинаковые метки в карточке неразличимы.
    assert good.json()["custom_values"][field["id"]] == ["DE", "ES"]
    assert bad.status_code == 422


async def test_duplicate_options_collapse_when_the_field_is_created(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Формат", "kind": "select",
                  "options": ["Видео", " Видео ", "", "Статика"]},
        ).json()
    assert field["options"] == ["Видео", "Статика"]


async def test_a_url_field_refuses_a_javascript_link(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields", json={"name": "Референс", "kind": "url"}
        ).json()
        response = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео", "custom_values": {field["id"]: "javascript:alert(1)"}},
        )
    assert response.status_code == 422


async def test_a_file_field_binds_the_upload_to_the_task(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields", json={"name": "Исходник", "kind": "file"}
        ).json()
        uploaded = client.post(
            "/api/v1/workspace/attachments",
            files={"file": ("source.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        )
        assert uploaded.status_code == 201
        payload = uploaded.json()

        task = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео", "custom_values": {field["id"]: [payload["id"]]}},
        ).json()
        assert task["custom_values"][field["id"]] == [payload["id"]]

        fetched = client.get(f"/api/v1/workspace/attachments/{payload['id']}")
        assert fetched.status_code == 200
        assert fetched.headers["x-content-type-options"] == "nosniff"

        board = client.get("/api/v1/workspace/board").json()
        assert board["attachments"][payload["id"]]["file_name"] == "source.png"

    async with SessionLocal() as db:
        attachment = await db.get(KnowledgeAttachment, uuid.UUID(payload["id"]))
        # Привязка к задаче — по ней файл переживает уборку брошенных загрузок.
        assert str(attachment.task_id) == task["id"]


async def test_a_file_removed_from_the_card_is_deleted(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields", json={"name": "Исходник", "kind": "file"}
        ).json()
        payload = client.post(
            "/api/v1/workspace/attachments",
            files={"file": ("source.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        ).json()
        task = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео", "custom_values": {field["id"]: [payload["id"]]}},
        ).json()
        client.patch(f"/api/v1/workspace/tasks/{task['id']}", json={"custom_values": {}})

    async with SessionLocal() as db:
        assert await db.get(KnowledgeAttachment, uuid.UUID(payload["id"])) is None


async def test_a_file_of_another_task_cannot_be_reused(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields", json={"name": "Исходник", "kind": "file"}
        ).json()
        payload = client.post(
            "/api/v1/workspace/attachments",
            files={"file": ("source.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        ).json()
        client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Первая", "custom_values": {field["id"]: [payload["id"]]}},
        )
        response = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Вторая", "custom_values": {field["id"]: [payload["id"]]}},
        )
    assert response.status_code == 422
    assert "другой задаче" in response.json()["error"]["message"]


async def test_an_unknown_file_id_is_refused(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields", json={"name": "Исходник", "kind": "file"}
        ).json()
        response = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео", "custom_values": {field["id"]: [str(uuid.uuid4())]}},
        )
    assert response.status_code == 422


async def test_a_template_cannot_reference_an_unknown_field(workspace) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/workspace/task-templates",
            json={"name": "Кривой", "field_ids": [str(uuid.uuid4())]},
        )
    assert response.status_code == 422


async def test_deleting_a_field_removes_it_from_templates(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Временное", "kind": "text", "show_always": False},
        ).json()
        template = client.post(
            "/api/v1/workspace/task-templates",
            json={"name": "Бриф", "field_ids": [field["id"]]},
        ).json()
        client.delete(f"/api/v1/workspace/fields/{field['id']}")
        templates = client.get("/api/v1/workspace/task-templates").json()["items"]

    assert [row["id"] for row in templates] == [template["id"]]
    assert templates[0]["field_ids"] == []


async def test_deleting_a_file_field_removes_its_uploads(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields", json={"name": "Исходник", "kind": "file"}
        ).json()
        payload = client.post(
            "/api/v1/workspace/attachments",
            files={"file": ("source.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        ).json()
        client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео", "custom_values": {field["id"]: [payload["id"]]}},
        )
        client.delete(f"/api/v1/workspace/fields/{field['id']}")

    async with SessionLocal() as db:
        # Иначе файл навсегда занимал бы квоту, не показываясь нигде.
        assert await db.get(KnowledgeAttachment, uuid.UUID(payload["id"])) is None


async def test_a_template_may_leave_a_required_field_empty(workspace) -> None:
    with _admin_client() as client:
        client.post(
            "/api/v1/workspace/fields",
            json={"name": "Номер договора", "kind": "text", "is_required": True},
        )
        response = client.post(
            "/api/v1/workspace/task-templates", json={"name": "Пустой шаблон"}
        )
    assert response.status_code == 201


async def test_deleting_a_task_removes_its_files(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields", json={"name": "Исходник", "kind": "file"}
        ).json()
        payload = client.post(
            "/api/v1/workspace/attachments",
            files={"file": ("source.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        ).json()
        task = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео", "custom_values": {field["id"]: [payload["id"]]}},
        ).json()
        client.delete(f"/api/v1/workspace/tasks/{task['id']}")

    async with SessionLocal() as db:
        assert await db.get(KnowledgeAttachment, uuid.UUID(payload["id"])) is None


# --- именной доступ к разделу (ТЗ 8.2) ---------------------------------------


async def _buyer(workspace, login: str, password: str) -> uuid.UUID:
    """Завести обычного пользователя-баера в том же воркспейсе."""
    async with SessionLocal() as db:
        role = await db.scalar(select(Role).where(Role.name == "Buyer"))
        user = User(
            workspace_id=workspace["workspace"],
            role_id=role.id,
            name=login,
            login=login,
            password_hash=hash_password(password),
        )
        db.add(user)
        await db.commit()
        return user.id


async def _drop_user(user_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(Session).where(Session.user_id == user_id))
        await db.execute(delete(UserParent).where(UserParent.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def _client_for(login: str, password: str):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.__enter__()
    client.post("/api/v1/auth/login", json={"login": login, "password": password})
    return client


async def test_a_named_rule_opens_the_section_only_for_that_person(workspace) -> None:
    """Правило на человека закрывает раздел для всех остальных в его роли."""
    chosen = await _buyer(workspace, "wschosen", "chosen-password")
    other = await _buyer(workspace, "wsother", "other-password")
    try:
        with _admin_client() as client:
            section = client.post(
                "/api/v1/knowledge/sections", json={"title": "Только для Ирины"}
            ).json()
            response = client.put(
                f"/api/v1/knowledge/sections/{section['id']}/access",
                json={"rules": [{"user_id": str(chosen), "can_view": True}]},
            )
            assert response.status_code == 200

        client = _client_for("wschosen", "chosen-password")
        try:
            mine = client.get("/api/v1/knowledge/tree").json()
        finally:
            client.__exit__(None, None, None)

        client = _client_for("wsother", "other-password")
        try:
            theirs = client.get("/api/v1/knowledge/tree").json()
        finally:
            client.__exit__(None, None, None)
    finally:
        await _drop_user(chosen)
        await _drop_user(other)

    assert [row["title"] for row in mine["sections"]] == ["Только для Ирины"]
    # Роль у обоих одна — раздел скрыт именно по имени.
    assert theirs["sections"] == []


async def test_a_named_rule_beats_the_rule_of_the_same_role(workspace) -> None:
    chosen = await _buyer(workspace, "wsnamedwins", "named-password")
    try:
        async with SessionLocal() as db:
            buyer_role = await db.scalar(select(Role).where(Role.name == "Buyer"))
            role_id = buyer_role.id

        with _admin_client() as client:
            section = client.post(
                "/api/v1/knowledge/sections", json={"title": "Закрытый для баеров"}
            ).json()
            client.put(
                f"/api/v1/knowledge/sections/{section['id']}/access",
                json={
                    "rules": [
                        {"role_id": str(role_id), "can_view": False},
                        {"user_id": str(chosen), "can_view": True, "can_edit": True},
                    ]
                },
            )

        client = _client_for("wsnamedwins", "named-password")
        try:
            tree = client.get("/api/v1/knowledge/tree").json()
        finally:
            client.__exit__(None, None, None)
    finally:
        await _drop_user(chosen)

    assert [row["title"] for row in tree["sections"]] == ["Закрытый для баеров"]
    assert tree["sections"][0]["rights"]["can_edit"] is True


async def test_a_named_rule_is_inherited_by_nested_sections(workspace) -> None:
    chosen = await _buyer(workspace, "wsnamedchild", "child-password")
    try:
        with _admin_client() as client:
            parent = client.post(
                "/api/v1/knowledge/sections", json={"title": "Мой раздел"}
            ).json()
            client.post(
                "/api/v1/knowledge/sections",
                json={"title": "Подраздел", "parent_id": parent["id"]},
            )
            client.put(
                f"/api/v1/knowledge/sections/{parent['id']}/access",
                json={"rules": [{"user_id": str(chosen), "can_view": True}]},
            )

        client = _client_for("wsnamedchild", "child-password")
        try:
            tree = client.get("/api/v1/knowledge/tree").json()
        finally:
            client.__exit__(None, None, None)
    finally:
        await _drop_user(chosen)

    assert [row["title"] for row in tree["sections"]] == ["Мой раздел"]
    assert [row["title"] for row in tree["sections"][0]["children"]] == ["Подраздел"]


async def test_a_rule_for_both_a_role_and_a_person_is_refused(workspace) -> None:
    with _admin_client() as client:
        section = client.post("/api/v1/knowledge/sections", json={"title": "Раздел"}).json()
        role_id = client.get("/api/v1/knowledge/sections/" + section["id"] + "/access") \
            .json()["roles"][0]["role_id"]
        both = client.put(
            f"/api/v1/knowledge/sections/{section['id']}/access",
            json={"rules": [{"role_id": role_id, "user_id": str(workspace["admin"])}]},
        )
        neither = client.put(
            f"/api/v1/knowledge/sections/{section['id']}/access",
            json={"rules": [{"can_view": True}]},
        )
    assert both.status_code == 422
    assert neither.status_code == 422


async def test_two_rules_for_the_same_person_are_refused(workspace) -> None:
    with _admin_client() as client:
        section = client.post("/api/v1/knowledge/sections", json={"title": "Раздел"}).json()
        response = client.put(
            f"/api/v1/knowledge/sections/{section['id']}/access",
            json={
                "rules": [
                    {"user_id": str(workspace["admin"]), "can_view": True},
                    {"user_id": str(workspace["admin"]), "can_view": False},
                ]
            },
        )
    assert response.status_code == 422
    assert "два правила" in response.json()["error"]["message"]


async def test_the_access_form_lists_both_roles_and_people(workspace) -> None:
    with _admin_client() as client:
        section = client.post("/api/v1/knowledge/sections", json={"title": "Раздел"}).json()
        access = client.get(f"/api/v1/knowledge/sections/{section['id']}/access").json()

    assert access["inherited"] is True
    assert access["roles"] and all("role_name" in row for row in access["roles"])
    assert [row["user_name"] for row in access["users"]]
    assert all(row["configured"] is False for row in access["users"])


async def test_an_unknown_person_cannot_be_granted_access(workspace) -> None:
    with _admin_client() as client:
        section = client.post("/api/v1/knowledge/sections", json={"title": "Раздел"}).json()
        response = client.put(
            f"/api/v1/knowledge/sections/{section['id']}/access",
            json={"rules": [{"user_id": str(uuid.uuid4()), "can_view": True}]},
        )
    assert response.status_code == 422
    assert "пользователя" in response.json()["error"]["message"]


async def test_the_board_can_be_sorted_by_creation_and_update_time(workspace) -> None:
    """Сортировка применяется ко всем колонкам разом, а не к одной."""
    with _admin_client() as client:
        first = client.post("/api/v1/workspace/tasks", json={"title": "Первая"}).json()
        second = client.post("/api/v1/workspace/tasks", json={"title": "Вторая"}).json()
        # Правим первую — она становится самой свежей по дате изменения.
        client.patch(f"/api/v1/workspace/tasks/{first['id']}", json={"title": "Первая*"})

        newest = client.get("/api/v1/workspace/board?sort=created").json()
        oldest = client.get("/api/v1/workspace/board?sort=created_asc").json()

    # SQLite хранит время с точностью до секунды, и обе задачи заведены в одну.
    # Проставляем время правки явно — проверяем сортировку, а не разрешение часов.
    async with SessionLocal() as db:
        await db.execute(
            update(Task)
            .where(Task.id == uuid.UUID(first["id"]))
            .values(updated_at=datetime(2026, 8, 7, 12, 0, tzinfo=UTC))
        )
        await db.execute(
            update(Task)
            .where(Task.id == uuid.UUID(second["id"]))
            .values(updated_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC))
        )
        await db.commit()

    with _admin_client() as client:
        updated = client.get("/api/v1/workspace/board?sort=updated").json()
        stale = client.get("/api/v1/workspace/board?sort=updated_asc").json()

    def titles(board: dict) -> list[str]:
        return [row["title"] for column in board["columns"] for row in column["tasks"]]

    assert titles(newest) == ["Вторая", "Первая*"]
    assert titles(oldest) == ["Первая*", "Вторая"]
    assert titles(updated) == ["Первая*", "Вторая"]
    assert titles(stale) == ["Вторая", "Первая*"]


async def test_a_field_created_inside_a_template_stays_inside_it(workspace) -> None:
    with _admin_client() as client:
        field = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Дедлайн монтажа", "kind": "date", "show_always": False},
        ).json()
        template = client.post(
            "/api/v1/workspace/task-templates",
            json={"name": "Бриф", "field_ids": [field["id"]]},
        ).json()
        with_template = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Крео", "template_id": template["id"]},
        ).json()
        without = client.post("/api/v1/workspace/tasks", json={"title": "Обычная"}).json()

    assert with_template["field_ids"] == [field["id"]]
    assert without["field_ids"] == []


async def test_a_field_created_from_a_card_shows_in_every_task(workspace) -> None:
    """Поле, заведённое прямо в карточке, — общее для доски."""
    with _admin_client() as client:
        client.post("/api/v1/workspace/tasks", json={"title": "Была раньше"})
        field = client.post(
            "/api/v1/workspace/fields",
            json={"name": "Комментарий", "kind": "textarea", "show_always": True},
        ).json()
        board = client.get("/api/v1/workspace/board").json()

    stored = [row for column in board["columns"] for row in column["tasks"]][0]
    assert stored["field_ids"] == [field["id"]]


async def test_children_are_assigned_from_the_parent_card(database) -> None:
    """Иерархию задают с обеих сторон: «мои начальники» и «мои подчинённые».

    В базе связь одна, поэтому подчинённые пишутся как родитель у каждого из
    них — и снятие подчинённого не должно трогать остальных его начальников.
    """
    suffix = uuid.uuid4().hex[:8]
    created: list[str] = []

    def _all_users(client):
        payload = client.get("/api/v1/users").json()
        return payload["items"] if isinstance(payload, dict) else payload

    with _admin_client() as client:
        role_id = client.get("/api/v1/roles").json()[0]["id"]

        def make(login: str, **extra) -> str:
            response = client.post(
                "/api/v1/users",
                json={"name": f"Child test {login}", "login": f"{login}-{suffix}",
                      "password": "test-password", "role_id": role_id, **extra},
            )
            assert response.status_code == 201, response.text
            created.append(response.json()["id"])
            return response.json()["id"]

        chief = make("chief")
        first = make("first")
        second = make("second")

        assigned = client.patch(
            f"/api/v1/users/{chief}", json={"child_ids": [first, second]}
        )
        assert assigned.status_code == 200

        people = {row["id"]: row for row in _all_users(client)}
        assert [p["id"] for p in people[first]["parents"]] == [chief]
        assert [p["id"] for p in people[second]["parents"]] == [chief]

        # Снимаем одного — второй остаётся на месте.
        client.patch(f"/api/v1/users/{chief}", json={"child_ids": [second]})
        people = {row["id"]: row for row in _all_users(client)}
        assert people[first]["parents"] == []
        assert [p["id"] for p in people[second]["parents"]] == [chief]

        # Кольцо не пропускаем: начальник не может стать подчинённым своего же
        # подчинённого.
        cycle = client.patch(f"/api/v1/users/{second}", json={"child_ids": [chief]})
        assert cycle.status_code == 422

        for user_id in reversed(created):
            client.delete(f"/api/v1/users/{user_id}")


async def test_a_task_keeps_its_start_date(workspace) -> None:
    """Дата начала — отдельная от срока: по ней видно, когда задачу берут.

    И начать позже срока нельзя: это не задача, а опечатка.
    """
    with _admin_client() as client:
        created = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Спланировать залив", "start_date": "2026-09-10",
                  "due_date": "2026-09-20"},
        )
        assert created.status_code == 201
        task = created.json()
        assert task["start_date"] == "2026-09-10"

        columns = await _columns(client)
        card = columns[0]["tasks"][0]
        assert card["start_date"] == "2026-09-10"

        moved = client.patch(
            f"/api/v1/workspace/tasks/{task['id']}", json={"start_date": "2026-09-12"}
        )
        assert moved.status_code == 200

        wrong = client.patch(
            f"/api/v1/workspace/tasks/{task['id']}", json={"start_date": "2026-09-25"}
        )
        assert wrong.status_code == 422

        backwards = client.post(
            "/api/v1/workspace/tasks",
            json={"title": "Задом наперёд", "start_date": "2026-09-20",
                  "due_date": "2026-09-10"},
        )
        assert backwards.status_code == 422
