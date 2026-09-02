"""Ручная чистка комментариев под рекламой.

Проверяется не «ручка отвечает 200», а то, ради чего модуль устроен именно так:
удалённый комментарий остаётся в CRM вместе с текстом, чужой id через ручку не
проходит, второе задание по кабинету не стартует, отмена срабатывает на
следующем комментарии, а нехватка прав на страницу объясняется словами, а не
кодом Meta.
"""

import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models import (
    IntegrationConnection,
    MetaAdAccount,
    MetaComment,
    MetaCommentJob,
    MetaEntity,
    MetaFanPage,
    MetaOperation,
    Permission,
    Role,
    User,
)
from app.services.meta import MetaClient, MetaError
from app.services.meta_comments import (
    CommentJobEngine,
    describe_access_error,
    has_link,
    has_phone,
    parse_meta_time,
)
from tests.test_media_finance import _admin_client

PAGE_ID = "990001"
POST_ID = f"{PAGE_ID}_500001"
OTHER_POST_ID = f"{PAGE_ID}_500002"
AD_ID = "23848100001"

# Что «отдаёт Meta» по умолчанию: спам со ссылкой, спам с телефоном, живой
# вопрос клиента и ответ в ветке.
COMMENTS = [
    {
        "id": f"{POST_ID}_1",
        "message": "Заходи на мойсайт.ru там дешевле",
        "created_time": "2026-08-27T09:12:00+0000",
        "from": {"id": "u1", "name": "Спамер Первый"},
        "is_hidden": False,
        "like_count": 0,
        "comment_count": 0,
    },
    {
        "id": f"{POST_ID}_2",
        "message": "пишите +7 902 111-22-33 сделаю дешевле",
        "created_time": "2026-08-27T09:10:00+0000",
        "from": {"id": "u2", "name": "Спамер Второй"},
        "is_hidden": False,
        "like_count": 1,
        "comment_count": 0,
    },
    {
        "id": f"{POST_ID}_3",
        "message": "А доставка в Астану есть?",
        "created_time": "2026-08-27T09:00:00+0000",
        "from": {"id": "u3", "name": "Живой Клиент"},
        "is_hidden": False,
        "like_count": 0,
        "comment_count": 1,
    },
    {
        "id": f"{POST_ID}_4",
        "message": "и мне интересно",
        "created_time": "2026-08-27T09:05:00+0000",
        "from": {"id": "u4", "name": "Второй Клиент"},
        "is_hidden": True,
        "like_count": 0,
        "comment_count": 0,
        "parent": {"id": f"{POST_ID}_3"},
    },
]


class FakeGraph:
    """Graph API ровно в той части, которой касается чистка."""

    def __init__(
        self,
        comments=None,
        *,
        fail_read: MetaError | None = None,
        accounts: list[dict] | None = None,
        ads_posts: list[dict] | None = None,
    ) -> None:
        self.comments = COMMENTS if comments is None else comments
        self.fail_read = fail_read
        # Страницы с токенами — ответ /me/accounts; пусто = страниц не видно.
        self.accounts = accounts or []
        # Ответ /{page}/ads_posts — рекламные (тёмные) посты страницы.
        self.ads_posts = ads_posts or []
        self.calls: list[tuple[str, str]] = []
        self.auth: list[tuple[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            self.calls.append((request.method, path))
            self.auth.append((path, request.headers.get("Authorization", "")))
            if request.method == "GET" and path.endswith("/me/accounts"):
                return httpx.Response(200, json={"data": self.accounts, "paging": {}})
            if request.method == "GET" and path.endswith("/ads_posts"):
                return httpx.Response(200, json={"data": self.ads_posts, "paging": {}})
            if request.method == "GET" and path.endswith("/comments"):
                if self.fail_read:
                    return httpx.Response(
                        400,
                        json={
                            "error": {
                                "message": self.fail_read.args[0],
                                "code": self.fail_read.error_code,
                            }
                        },
                    )
                return httpx.Response(200, json={"data": self.comments, "paging": {}})
            if request.method in {"POST", "DELETE"}:
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json={"data": []})

        return httpx.MockTransport(handle)

    def client_factory(self):
        transport = self.transport()

        def factory(token, **kwargs):
            kwargs.pop("transport", None)
            kwargs.pop("proxy", None)
            kwargs.pop("user_agent", None)
            return MetaClient(token, transport=transport, **kwargs)

        return factory


@pytest.fixture
async def comment_setup(database):
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            owner_id=admin.id,
            name="Comments connection",
            kind="meta",
            base_url="https://graph.facebook.com/v23.0",
            api_key_encrypted=encrypt_secret("comments-token-long-enough-value"),
            auth_method="system_user",
        )
        db.add(connection)
        await db.flush()
        account = MetaAdAccount(
            workspace_id=admin.workspace_id,
            connection_id=connection.id,
            external_id="act_comments_1",
            name="Comments cabinet",
            owner_id=admin.id,
            currency="USD",
        )
        db.add(account)
        await db.flush()
        db.add_all(
            [
                MetaEntity(
                    workspace_id=admin.workspace_id,
                    connection_id=connection.id,
                    account_id=account.id,
                    level="ad",
                    external_id=AD_ID,
                    name="BR | creo 07",
                    page_external_id=PAGE_ID,
                    post_external_id=POST_ID,
                    effective_status="ACTIVE",
                ),
                MetaEntity(
                    workspace_id=admin.workspace_id,
                    connection_id=connection.id,
                    account_id=account.id,
                    level="ad",
                    external_id=f"{AD_ID}2",
                    name="BR | creo 08 (остановлено)",
                    page_external_id=PAGE_ID,
                    post_external_id=OTHER_POST_ID,
                    effective_status="PAUSED",
                ),
            ]
        )
        await db.commit()
        ids = {
            "workspace": admin.workspace_id,
            "admin": admin.id,
            "connection": connection.id,
            "account": account.id,
        }

    yield ids

    async with SessionLocal() as db:
        await db.execute(delete(MetaOperation).where(MetaOperation.workspace_id == ids["workspace"]))
        await db.execute(delete(MetaComment).where(MetaComment.account_id == ids["account"]))
        await db.execute(delete(MetaCommentJob).where(MetaCommentJob.account_id == ids["account"]))
        await db.execute(delete(MetaEntity).where(MetaEntity.account_id == ids["account"]))
        await db.execute(delete(MetaAdAccount).where(MetaAdAccount.id == ids["account"]))
        await db.execute(
            delete(IntegrationConnection).where(IntegrationConnection.id == ids["connection"])
        )
        await db.commit()


def _message(response) -> str:
    """Текст ошибки: приложение отвечает конвертом {"error": {"message": ...}}."""
    return str((response.json().get("error") or {}).get("message") or "")


async def _run_job(job_id, graph: FakeGraph) -> dict:
    engine = CommentJobEngine(SessionLocal, graph.client_factory())
    engine.delay = 0
    return await engine.run(str(job_id))


def test_text_flags_catch_the_usual_spam() -> None:
    assert has_link("Заходи на мойсайт.ru там дешевле")
    assert has_link("https://t.me/spam")
    assert not has_link("А доставка в Астану есть?")
    assert has_phone("пишите +7 902 111-22-33")
    assert not has_phone("цена 500 рублей")


def test_meta_time_survives_offset_without_colon() -> None:
    parsed = parse_meta_time("2026-08-27T09:12:00+0000")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parse_meta_time("") is None
    assert parse_meta_time("не дата") is None


def test_missing_page_scope_is_explained_in_words() -> None:
    """Самая частая причина пустого списка — рекламный токен без прав страницы."""
    message = describe_access_error(
        MetaError("(#200) Requires pages_read_engagement", error_code=200)
    )
    assert "pages_read_engagement" in message
    assert "токен" in message.lower()
    assert "190" not in describe_access_error(MetaError("boom", error_code=190))


async def test_fetch_stores_comments_with_flags(comment_setup) -> None:
    graph = FakeGraph()
    with _admin_client() as client:
        started = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"]), "active_only": True},
        )
    assert started.status_code == 202
    job = started.json()
    # Активное объявление одно — второй пост в задание не попал.
    assert job["total"] == 1

    result = await _run_job(job["id"], graph)
    assert result["status"] == "done"

    async with SessionLocal() as db:
        rows = {
            row.external_id: row
            for row in (
                await db.execute(
                    select(MetaComment).where(MetaComment.account_id == comment_setup["account"])
                )
            ).scalars()
        }
    assert len(rows) == 4
    assert rows[f"{POST_ID}_1"].has_link is True
    assert rows[f"{POST_ID}_2"].has_phone is True
    assert rows[f"{POST_ID}_3"].has_link is False
    # Скрытый в самом Facebook приезжает скрытым, а не «видимым».
    assert rows[f"{POST_ID}_4"].status == "hidden"
    assert rows[f"{POST_ID}_4"].parent_external_id == f"{POST_ID}_3"
    assert rows[f"{POST_ID}_1"].author_name == "Спамер Первый"

    with _admin_client() as client:
        listed = client.get(
            "/api/v1/meta/comments",
            params={"account_id": str(comment_setup["account"]), "only_links": True},
        )
    assert listed.status_code == 200
    assert [item["external_id"] for item in listed.json()["items"]] == [f"{POST_ID}_1"]


async def test_delete_keeps_the_text_in_crm(comment_setup) -> None:
    """Удалённый комментарий Meta не отдаёт никогда — след обязан остаться у нас."""
    graph = FakeGraph()
    with _admin_client() as client:
        fetch = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        ).json()
    await _run_job(fetch["id"], graph)

    with _admin_client() as client:
        action = client.post(
            "/api/v1/meta/comments/action",
            json={
                "account_id": str(comment_setup["account"]),
                "action": "delete",
                "comments": [f"{POST_ID}_1"],
            },
        )
    assert action.status_code == 202
    await _run_job(action.json()["id"], graph)

    async with SessionLocal() as db:
        comment = await db.scalar(
            select(MetaComment).where(MetaComment.external_id == f"{POST_ID}_1")
        )
        operation = await db.scalar(
            select(MetaOperation).where(MetaOperation.kind == "comment_delete")
        )
        job = await db.scalar(
            select(MetaCommentJob).where(MetaCommentJob.id == uuid.UUID(action.json()["id"]))
        )
    assert comment.status == "deleted"
    assert comment.message == "Заходи на мойсайт.ru там дешевле"
    assert comment.acted_at is not None
    assert operation.status == "success"
    assert job.succeeded == 1 and job.failed == 0 and job.status == "done"
    assert ("DELETE", f"/v23.0/{POST_ID}_1") in graph.calls


async def test_hide_and_unhide_move_the_status_both_ways(comment_setup) -> None:
    graph = FakeGraph()
    with _admin_client() as client:
        fetch = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        ).json()
    await _run_job(fetch["id"], graph)

    with _admin_client() as client:
        hidden = client.post(
            "/api/v1/meta/comments/action",
            json={
                "account_id": str(comment_setup["account"]),
                "action": "hide",
                "comments": [f"{POST_ID}_2"],
            },
        ).json()
    await _run_job(hidden["id"], graph)
    async with SessionLocal() as db:
        row = await db.scalar(
            select(MetaComment).where(MetaComment.external_id == f"{POST_ID}_2")
        )
    assert row.status == "hidden"

    with _admin_client() as client:
        back = client.post(
            "/api/v1/meta/comments/action",
            json={
                "account_id": str(comment_setup["account"]),
                "action": "unhide",
                "comments": [f"{POST_ID}_2"],
            },
        ).json()
    await _run_job(back["id"], graph)
    async with SessionLocal() as db:
        row = await db.scalar(
            select(MetaComment).where(MetaComment.external_id == f"{POST_ID}_2")
        )
    assert row.status == "visible"


async def test_comment_that_vanished_from_meta_is_marked_deleted(comment_setup) -> None:
    """Комментарий убрал автор или сама Meta — список обязан это показать."""
    with _admin_client() as client:
        first = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        ).json()
    await _run_job(first["id"], FakeGraph())

    with _admin_client() as client:
        second = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        ).json()
    await _run_job(second["id"], FakeGraph(comments=COMMENTS[1:]))

    async with SessionLocal() as db:
        gone = await db.scalar(
            select(MetaComment).where(MetaComment.external_id == f"{POST_ID}_1")
        )
    assert gone.status == "deleted"
    assert gone.message


async def test_foreign_comment_id_does_not_pass(comment_setup) -> None:
    """Через ручку действия нельзя достать комментарий чужого кабинета."""
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/comments/action",
            json={
                "account_id": str(comment_setup["account"]),
                "action": "delete",
                "comments": ["777_чужой"],
            },
        )
    assert response.status_code == 422
    assert "обновите список" in _message(response).lower()


async def test_second_job_on_the_same_cabinet_is_refused(comment_setup) -> None:
    """Два задания разом — это удвоенный темп запросов там, где темп и есть риск."""
    with _admin_client() as client:
        first = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        )
        second = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        )
    assert first.status_code == 202
    assert second.status_code == 409

    with _admin_client() as client:
        cancelled = client.post(f"/api/v1/meta/comments/jobs/{first.json()['id']}/cancel")
        again = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        )
    assert cancelled.json()["status"] == "cancelled"
    assert again.status_code == 202


async def test_cancel_stops_the_run_between_comments(comment_setup) -> None:
    graph = FakeGraph()
    with _admin_client() as client:
        fetch = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        ).json()
    await _run_job(fetch["id"], graph)

    with _admin_client() as client:
        action = client.post(
            "/api/v1/meta/comments/action",
            json={
                "account_id": str(comment_setup["account"]),
                "action": "hide",
                "comments": [f"{POST_ID}_1", f"{POST_ID}_2", f"{POST_ID}_3"],
            },
        ).json()

    async with SessionLocal() as db:
        job = await db.get(MetaCommentJob, uuid.UUID(action["id"]))
        job.cancel_requested = True
        await db.commit()

    result = await _run_job(action["id"], graph)
    assert result["status"] == "cancelled"
    async with SessionLocal() as db:
        stored = await db.get(MetaCommentJob, uuid.UUID(action["id"]))
        untouched = await db.scalar(
            select(MetaComment).where(MetaComment.external_id == f"{POST_ID}_1")
        )
    assert stored.status == "cancelled"
    assert stored.processed == 0
    assert untouched.status == "visible"


async def test_cap_protects_from_select_all(comment_setup) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/comments/action",
            json={
                "account_id": str(comment_setup["account"]),
                "action": "delete",
                "comments": [f"{POST_ID}_{index}" for index in range(400)],
            },
        )
    assert response.status_code == 422
    assert "не больше" in _message(response)


async def test_failed_call_does_not_stop_the_rest(comment_setup) -> None:
    """Один комментарий отбился — остальные обязаны быть обработаны."""
    graph = FakeGraph()
    with _admin_client() as client:
        fetch = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        ).json()
    await _run_job(fetch["id"], graph)

    with _admin_client() as client:
        action = client.post(
            "/api/v1/meta/comments/action",
            json={
                "account_id": str(comment_setup["account"]),
                "action": "delete",
                "comments": [f"{POST_ID}_1", f"{POST_ID}_2"],
            },
        ).json()

    failing = FakeGraph()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"{POST_ID}_1"):
            return httpx.Response(
                400, json={"error": {"message": "Object does not exist", "code": 803}}
            )
        return httpx.Response(200, json={"success": True})

    def factory(token, **kwargs):
        kwargs.pop("transport", None)
        kwargs.pop("proxy", None)
        kwargs.pop("user_agent", None)
        return MetaClient(token, transport=httpx.MockTransport(handle), **kwargs)

    engine = CommentJobEngine(SessionLocal, factory)
    engine.delay = 0
    result = await engine.run(action["id"])

    assert result["status"] == "done"
    async with SessionLocal() as db:
        job = await db.get(MetaCommentJob, uuid.UUID(action["id"]))
        first = await db.scalar(
            select(MetaComment).where(MetaComment.external_id == f"{POST_ID}_1")
        )
        second = await db.scalar(
            select(MetaComment).where(MetaComment.external_id == f"{POST_ID}_2")
        )
    assert job.succeeded == 1 and job.failed == 1
    assert first.status == "visible"
    assert second.status == "deleted"
    assert failing.calls == []


async def test_moderation_needs_its_own_permission(comment_setup) -> None:
    """`meta.view` показывает комментарии, но удалять ими нельзя."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        viewer_role = Role(
            workspace_id=admin.workspace_id,
            name="Comments viewer",
            permissions=list(
                (
                    await db.execute(
                        select(Permission).where(Permission.code.in_(["meta.view"]))
                    )
                ).scalars()
            ),
        )
        db.add(viewer_role)
        await db.flush()
        from app.core.security import hash_password

        viewer = User(
            workspace_id=admin.workspace_id,
            role_id=viewer_role.id,
            name="Comments viewer",
            login="comments-viewer",
            password_hash=hash_password("viewer-password"),
        )
        db.add(viewer)
        await db.flush()
        account = await db.get(MetaAdAccount, comment_setup["account"])
        owner_before = account.owner_id
        account.owner_id = viewer.id
        await db.commit()
        viewer_id, role_id = viewer.id, viewer_role.id

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login": "comments-viewer", "password": "viewer-password"},
        )
        assert login.status_code == 200
        listed = client.get(
            "/api/v1/meta/comments", params={"account_id": str(comment_setup["account"])}
        )
        blocked = client.post(
            "/api/v1/meta/comments/action",
            json={
                "account_id": str(comment_setup["account"]),
                "action": "delete",
                "comments": [f"{POST_ID}_1"],
            },
        )
    assert listed.status_code == 200
    assert blocked.status_code == 403

    async with SessionLocal() as db:
        account = await db.get(MetaAdAccount, comment_setup["account"])
        account.owner_id = owner_before
        await db.execute(delete(User).where(User.id == viewer_id))
        await db.execute(delete(Role).where(Role.id == role_id))
        await db.commit()


async def test_posts_list_shows_active_first(comment_setup) -> None:
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/posts",
            params={"account_id": str(comment_setup["account"])},
        )
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["post_external_id"] for item in items] == [POST_ID, OTHER_POST_ID]
    assert items[0]["active_ads"] == 1
    assert items[0]["title"] == "BR | creo 07"
    assert items[0]["permalink"].endswith(POST_ID)


async def test_session_connection_goes_through_the_browser_context(
    comment_setup, monkeypatch
) -> None:
    """У session-подключения запись обязана идти через живой контекст сессии."""
    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "session"
        connection.proxy_url = "http://user:pass@127.0.0.1:9999"
        await db.commit()

    opened: dict = {}
    closed: list[str] = []

    async def fake_open(session_factory, connection_id, **kwargs):
        opened["connection_id"] = connection_id
        opened["proxy_url"] = kwargs.get("proxy_url")
        return {"transport": object(), "owned": True, "token": "EAAB-fresh"}

    class FakeManager:
        async def close(self, connection_id):
            closed.append(connection_id)

    monkeypatch.setattr("app.services.meta_comments.open_session_access", fake_open)
    monkeypatch.setattr(
        "app.services.meta_comments.get_session_manager", lambda: FakeManager()
    )

    graph = FakeGraph()
    tokens: list[str] = []

    def factory(token, **kwargs):
        tokens.append(token)
        kwargs.pop("transport", None)
        kwargs.pop("proxy", None)
        kwargs.pop("user_agent", None)
        return MetaClient(token, transport=graph.transport(), **kwargs)

    with _admin_client() as client:
        fetch = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        ).json()

    engine = CommentJobEngine(SessionLocal, factory)
    engine.delay = 0
    result = await engine.run(fetch["id"])

    assert result["status"] == "done"
    assert opened["connection_id"] == str(comment_setup["connection"])
    assert opened["proxy_url"] == "http://user:pass@127.0.0.1:9999"
    # Свежий токен сессии важнее сохранённого в подключении.
    assert tokens == ["EAAB-fresh"]
    # Браузер, поднятый ради задания, закрыт.
    assert closed == [str(comment_setup["connection"])]

    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "system_user"
        connection.proxy_url = None
        await db.commit()


async def test_job_failure_is_recorded_with_a_readable_reason(comment_setup) -> None:
    graph = FakeGraph(fail_read=MetaError("(#200) Requires pages_read_engagement", error_code=200))
    with _admin_client() as client:
        fetch = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        ).json()
    result = await _run_job(fetch["id"], graph)

    # Отказ одного поста не роняет задание: оно доходит до конца и считает промах.
    assert result["status"] == "done"
    async with SessionLocal() as db:
        job = await db.get(MetaCommentJob, uuid.UUID(fetch["id"]))
        operation = await db.scalar(
            select(MetaOperation).where(MetaOperation.kind == "comments_fetch")
        )
    assert job.failed == 1
    assert "pages_read_engagement" in operation.error


def _tokens_used(graph: FakeGraph, suffix: str) -> list[str]:
    """Какими токенами ходили запросы на путь с таким окончанием."""
    return [token for path, token in graph.auth if path.endswith(suffix)]


async def test_fetch_reads_dark_posts_with_the_page_token(comment_setup) -> None:
    """Тёмные посты читаем токеном страницы: юзер-токен получает пустоту."""
    graph = FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page-token"}])
    with _admin_client() as client:
        fetch = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"])},
        ).json()
    result = await _run_job(fetch["id"], graph)

    assert result["status"] == "done"
    assert result["received"] == len(COMMENTS)
    used = _tokens_used(graph, "/comments")
    assert used and all(token == "Bearer EAAE-page-token" for token in used)
    listing = _tokens_used(graph, "/me/accounts")
    assert listing and all(
        token == "Bearer comments-token-long-enough-value" for token in listing
    )


async def test_access_explains_the_unmanaged_page(
    comment_setup, monkeypatch
) -> None:
    """Чужая страница — не «всё хорошо», а внятное объяснение.

    Meta отдаёт пустоту вместо ошибок на запрос комментариев тёмного поста
    чужой страницы, поэтому «пустой успешный ответ» здесь врёт.
    """
    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "session"
        await db.commit()

    graph = FakeGraph(accounts=[])  # страниц у пользователя сессии не видно

    async def fake_client_for(connection, db):
        return MetaClient("EAAB-session", transport=graph.transport())

    monkeypatch.setattr("app.api.routers.meta.client_for", fake_client_for)
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/access",
            params={"account_id": str(comment_setup["account"])},
        )
    body = response.json()
    assert body["ok"] is False
    assert PAGE_ID in body["reason"]
    assert "тёмных" in body["reason"] or "роли" in body["reason"]

    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "system_user"
        await db.commit()


async def test_access_passes_when_the_page_is_managed(
    comment_setup, monkeypatch
) -> None:
    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "session"
        await db.commit()

    graph = FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page"}])

    async def fake_client_for(connection, db):
        return MetaClient("EAAB-session", transport=graph.transport())

    monkeypatch.setattr("app.api.routers.meta.client_for", fake_client_for)
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/access",
            params={"account_id": str(comment_setup["account"])},
        )
    assert response.json()["ok"] is True

    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "system_user"
        await db.commit()


async def test_posts_fallback_pulls_page_ads_for_dynamic_ads(
    comment_setup, monkeypatch
) -> None:
    """У динамического объявления поста нет — берём рекламные посты страницы."""
    async with SessionLocal() as db:
        account = await db.get(MetaAdAccount, comment_setup["account"])
        db.add(
            MetaEntity(
                workspace_id=account.workspace_id,
                connection_id=account.connection_id,
                account_id=account.id,
                level="ad",
                external_id="23848100099",
                name="DYN | catalog",
                page_external_id=PAGE_ID,
                post_external_id=None,
                effective_status="ACTIVE",
            )
        )
        await db.commit()

    dark_post = {
        "id": "990001_777777",
        "created_time": "2026-08-28T10:00:00+0000",
        "message": "тёмный пост",
        "permalink_url": "https://facebook.com/990001_777777",
        "is_published": False,
    }
    graph = FakeGraph(
        accounts=[{"id": PAGE_ID, "access_token": "EAAE-page"}],
        ads_posts=[dark_post],
    )

    async def fake_client_for(connection, db):
        return MetaClient("EAAB-session", transport=graph.transport())

    monkeypatch.setattr("app.api.routers.meta.client_for", fake_client_for)
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/posts",
            params={"account_id": str(comment_setup["account"])},
        )
    items = response.json()["items"]
    posts = [item["post_external_id"] for item in items]
    assert "990001_777777" in posts
    page_item = next(item for item in items if item["post_external_id"] == "990001_777777")
    assert page_item["source"] == "page_ad"
    assert page_item["title"] == "Рекламный пост страницы"
    assert page_item["page_external_id"] == PAGE_ID



# --- несколько страниц и несколько подключений на кабинете --------------------


async def test_one_foreign_page_does_not_block_the_whole_account(
    comment_setup, monkeypatch
) -> None:
    """Кабинет ведёт две страницы: своя читается, чужая — нет.

    Раньше проверка брала один случайный пост и по нему объявляла комментарии
    недоступными для всего кабинета: достаточно было одной чужой страницы,
    чтобы экран закрылся, хотя та, с которой реально льют, работает.
    """
    foreign_page = "104805155705840"
    async with SessionLocal() as db:
        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "session"
        db.add(
            MetaEntity(
                workspace_id=comment_setup["workspace"],
                connection_id=comment_setup["connection"],
                account_id=comment_setup["account"],
                level="ad",
                external_id="ad-foreign",
                name="Чужая страница | creo",
                page_external_id=foreign_page,
                post_external_id=f"{foreign_page}_700001",
                effective_status="ACTIVE",
            )
        )
        await db.commit()

    # Пользователь подключения ведёт только свою страницу.
    graph = FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page"}])

    async def fake_client_for(connection, db):
        return MetaClient("EAAB-session", transport=graph.transport())

    monkeypatch.setattr("app.api.routers.meta.client_for", fake_client_for)
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/access",
            params={"account_id": str(comment_setup["account"])},
        )
    body = response.json()
    # Экран работает: одна страница читается.
    assert body["ok"] is True
    by_page = {row["page_external_id"]: row for row in body["pages"]}
    assert by_page[PAGE_ID]["ok"] is True
    assert by_page[foreign_page]["ok"] is False
    # И в тексте названа именно недоступная страница, а не случайная.
    assert foreign_page in body["reason"]

    async with SessionLocal() as db:
        from sqlalchemy import delete

        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "system_user"
        await db.execute(delete(MetaEntity).where(MetaEntity.external_id == "ad-foreign"))
        await db.commit()


async def test_page_of_another_connection_is_found(comment_setup, monkeypatch) -> None:
    """Страницу ведёт другое подключение воркспейса — доступ есть через него.

    На кабинете держат несколько подключений, и токен страницы бывает только у
    одного из них. Раньше спрашивали исключительно подключение кабинета.
    """
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        other = IntegrationConnection(
            workspace_id=comment_setup["workspace"],
            owner_id=admin.id,
            name="Второе подключение",
            kind="meta",
            base_url="https://graph.facebook.com/v23.0",
            api_key_encrypted=encrypt_secret("second-token-long-enough-value"),
            auth_method="session",
        )
        db.add(other)
        await db.flush()
        db.add(
            MetaFanPage(
                workspace_id=comment_setup["workspace"],
                connection_id=other.id,
                external_id=PAGE_ID,
                name="Наша страница",
            )
        )
        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "session"
        await db.commit()
        other_id = other.id

    def transport_for(connection):
        # Токен страницы есть только у второго подключения.
        if connection.id == other_id:
            return FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page"}])
        return FakeGraph(accounts=[])

    async def fake_client_for(connection, db):
        return MetaClient("EAAB", transport=transport_for(connection).transport())

    monkeypatch.setattr("app.api.routers.meta.client_for", fake_client_for)
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/access",
            params={"account_id": str(comment_setup["account"])},
        )
    body = response.json()
    assert body["ok"] is True
    row = [item for item in body["pages"] if item["page_external_id"] == PAGE_ID][0]
    assert row["ok"] is True
    assert row["connection_name"] == "Второе подключение"

    async with SessionLocal() as db:
        from sqlalchemy import delete

        connection = await db.get(IntegrationConnection, comment_setup["connection"])
        connection.auth_method = "system_user"
        await db.execute(delete(MetaFanPage).where(MetaFanPage.connection_id == other_id))
        await db.execute(delete(IntegrationConnection).where(IntegrationConnection.id == other_id))
        await db.commit()


async def test_foreign_page_post_is_counted_as_a_miss(comment_setup) -> None:
    """«Успешно, 0 комментариев» по чужой странице — это не успех.

    Graph отдаёт по чужой странице пустой список вместо отказа, и задание
    рапортовало об успехе, оставляя человека гадать, почему пусто.
    """
    foreign_page = "104805155705840"
    async with SessionLocal() as db:
        db.add(
            MetaEntity(
                workspace_id=comment_setup["workspace"],
                connection_id=comment_setup["connection"],
                account_id=comment_setup["account"],
                level="ad",
                external_id="ad-foreign-2",
                name="Чужая страница | creo",
                page_external_id=foreign_page,
                post_external_id=f"{foreign_page}_700002",
                effective_status="ACTIVE",
            )
        )
        await db.commit()

    graph = FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page"}])
    with _admin_client() as client:
        job = client.post(
            "/api/v1/meta/comments/fetch",
            json={"account_id": str(comment_setup["account"]),
                  "posts": [f"{foreign_page}_700002"]},
        ).json()
    result = await _run_job(job["id"], graph)

    assert result["status"] == "done"
    async with SessionLocal() as db:
        stored = await db.get(MetaCommentJob, uuid.UUID(job["id"]))
        operation = await db.scalar(
            select(MetaOperation).where(MetaOperation.kind == "comments_fetch")
        )
        from sqlalchemy import delete

        await db.execute(delete(MetaEntity).where(MetaEntity.external_id == "ad-foreign-2"))
        await db.commit()
    assert stored.failed == 1 and stored.succeeded == 0
    assert "не в ролях" in (operation.error or "")


# --- поиск поста по ссылке ---------------------------------------------------


def test_share_links_are_recognised_for_browser_resolution() -> None:
    """`facebook.com/share/p/...` не содержит ни страницы, ни поста.

    Раскрыть её может только браузер: Graph по ней ничего не отдаёт напрямую.
    """
    from app.services.meta_comments import parse_post_reference

    assert parse_post_reference(
        "https://www.facebook.com/share/p/1dJZXsR776/"
    ) == {"kind": "share"}
    assert parse_post_reference(
        "https://www.facebook.com/167817886407197_122246890700097056"
    ) == {"kind": "post", "post_id": "167817886407197_122246890700097056"}
    assert parse_post_reference("167817886407197_122246890700097056")["kind"] == "post"
    assert parse_post_reference("120212345678901234")["kind"] == "number"
    assert parse_post_reference("")["kind"] == "empty"
    assert parse_post_reference("просто текст")["kind"] == "unknown"


def test_share_post_id_uses_page_and_target_from_facebook_controls() -> None:
    from app.services.meta_comments import share_post_id

    assert share_post_id([
        "https://www.facebook.com/permalink.php?story_fbid=pfbidXXX&id=61552911694942",
        "https://www.facebook.com/ad_center/create/boostpost/"
        "?ad_account_id=1534552924599144&page_id=167817886407197"
        "&target_id=122246891066097056",
    ]) == "167817886407197_122246891066097056"


async def test_resolve_explains_share_needs_a_session(comment_setup) -> None:
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/resolve",
            params={
                "account_id": str(comment_setup["account"]),
                "ref": "https://www.facebook.com/share/p/1dJZXsR776/",
            },
        )
    body = response.json()
    assert body["ok"] is False
    assert "сесси" in body["reason"]


async def test_resolve_share_through_browser_session(comment_setup, monkeypatch) -> None:
    graph = FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page"}])

    async with SessionLocal() as db:
        connection = await db.get(
            IntegrationConnection, comment_setup["connection"]
        )
        connection.auth_method = "session"
        await db.commit()

    async def fake_client_for(connection, db):
        return MetaClient("EAAB", transport=graph.transport())

    async def fake_resolve_share(connection_id, ref):
        assert connection_id == str(comment_setup["connection"])
        assert ref == "https://www.facebook.com/share/p/1dJZXsR776/"
        return {"ok": True, "post_id": POST_ID, "via": "browser session"}

    monkeypatch.setattr("app.api.routers.meta.client_for", fake_client_for)
    monkeypatch.setattr(
        "app.services.meta_comments.resolve_share_post", fake_resolve_share
    )
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/resolve",
            params={
                "account_id": str(comment_setup["account"]),
                "ref": "https://www.facebook.com/share/p/1dJZXsR776/",
            },
        )
    body = response.json()
    assert body["ok"] is True
    assert body["post_external_id"] == POST_ID
    assert body["comments"] == len(COMMENTS)
    assert body["via"] == "browser session"


async def test_resolve_finds_a_post_by_link(comment_setup, monkeypatch) -> None:
    """Пост из ссылки читается, даже если синхронизация о нём ещё не знает."""
    graph = FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page"}])

    async def fake_client_for(connection, db):
        return MetaClient("EAAB", transport=graph.transport())

    monkeypatch.setattr("app.api.routers.meta.client_for", fake_client_for)
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/resolve",
            params={
                "account_id": str(comment_setup["account"]),
                "ref": f"https://www.facebook.com/{POST_ID}",
            },
        )
    body = response.json()
    assert body["ok"] is True
    assert body["post_external_id"] == POST_ID
    assert body["page_external_id"] == PAGE_ID
    assert body["comments"] == len(COMMENTS)
    # Этот пост синхронизация знает.
    assert body["known"] is True


async def test_resolve_works_for_a_post_outside_the_sync(
    comment_setup, monkeypatch
) -> None:
    """Объявление создали только что — синхронизация о нём не знает.

    Именно этот случай ловит поиск по ссылке: список постов ещё пуст, а
    комментарии под постом уже есть.
    """
    fresh_post = f"{PAGE_ID}_999999"
    graph = FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page"}])

    async def fake_client_for(connection, db):
        return MetaClient("EAAB", transport=graph.transport())

    monkeypatch.setattr("app.api.routers.meta.client_for", fake_client_for)
    with _admin_client() as client:
        response = client.get(
            "/api/v1/meta/comments/resolve",
            params={
                "account_id": str(comment_setup["account"]),
                "ref": fresh_post,
            },
        )
    body = response.json()
    assert body["ok"] is True
    assert body["post_external_id"] == fresh_post
    # Синхронизация о нём не знает — и это прямо сказано, а не скрыто.
    assert body["known"] is False


async def test_fetch_accepts_a_post_the_sync_never_saw(comment_setup) -> None:
    """Загрузка по ссылке не требует, чтобы пост был в синхронизации."""
    fresh_post = f"{PAGE_ID}_888888"
    graph = FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page"}])
    with _admin_client() as client:
        job = client.post(
            "/api/v1/meta/comments/fetch",
            json={
                "account_id": str(comment_setup["account"]),
                "posts": [fresh_post],
                "active_only": False,
            },
        )
    assert job.status_code == 202
    result = await _run_job(job.json()["id"], graph)
    assert result["status"] == "done"
    async with SessionLocal() as db:
        stored = list(
            (
                await db.execute(
                    select(MetaComment).where(
                        MetaComment.post_external_id == fresh_post
                    )
                )
            ).scalars()
        )
    assert len(stored) == len(COMMENTS)


async def test_delete_uses_page_token_even_when_comment_id_has_no_page_prefix(
    comment_setup,
) -> None:
    """Префикс ID комментария — это пост, не страница: «500001_965938…».

    Раньше страницу искали по этому префиксу, не находили и резали чужой
    коммент юзер-токеном — Meta отвечала «можно удалять только свои».
    Страницу берём из сохранённого поста комментария.
    """
    from app.models import MetaComment as MetaCommentRow

    tricky_id = "500001_965938"
    async with SessionLocal() as db:
        account = await db.get(MetaAdAccount, comment_setup["account"])
        db.add(
            MetaCommentRow(
                workspace_id=comment_setup["workspace"],
                connection_id=comment_setup["connection"],
                account_id=comment_setup["account"],
                external_id=tricky_id,
                post_external_id=POST_ID,
            )
        )
        await db.commit()

    graph = FakeGraph(accounts=[{"id": PAGE_ID, "access_token": "EAAE-page-token"}])
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/comments/action",
            json={
                "account_id": str(comment_setup["account"]),
                "action": "delete",
                "comments": [tricky_id],
            },
        )
    assert response.status_code == 202, response.text
    result = await _run_job(response.json()["id"], graph)
    assert result["status"] == "done"
    assert result["succeeded"] == 1
    used = _tokens_used(graph, f"/{tricky_id}")
    assert used and all(token == "Bearer EAAE-page-token" for token in used)
