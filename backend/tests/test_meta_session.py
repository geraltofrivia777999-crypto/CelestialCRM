"""Браузерные сессии Meta (токен EAAB): парсинг cookies, прокси-защита, API.

Токен сессии (EAAB) получается только из живого браузера с cookies и прокси
аккаунта. Тесты проверяют главное правило: без работающего прокси браузер не
запускается, а подключение с auth_method="session" без прокси не создаётся.
Playwright в тестах не запускается — всё внешнее мокается.
"""

import uuid

import httpx
import pytest
from sqlalchemy import delete, select

import app.api.routers.meta as meta_router
import app.services.meta_session as meta_session
from app.core.security import encrypt_secret
from app.models import IntegrationConnection, MetaAdAccount, SyncRun, SyncStatus, User
from app.services.meta import MetaClient
from app.services.meta_session import (
    MetaSessionError,
    MetaSessionManager,
    parse_cookies,
    parse_proxy_url,
)
from app.services.meta_sync import MetaSyncEngine
from tests.test_media_finance import _admin_client
from tests.test_meta import SessionLocal

# --- parse_cookies ------------------------------------------------------------


def test_parse_cookies_header_style() -> None:
    cookies = parse_cookies("c_user=1000001; xs=abc123; datr=xyz")
    assert len(cookies) == 3
    assert cookies[0]["name"] == "c_user"
    assert cookies[0]["value"] == "1000001"
    # Известные httpOnly/secure cookies получают правильные флаги
    assert cookies[1]["httpOnly"] is True
    assert cookies[1]["secure"] is True
    assert all(c["domain"] == ".facebook.com" for c in cookies)


def test_parse_cookies_json_array_with_normalization() -> None:
    payload = (
        '[{"name":"a","value":"1","sameSite":"no_restriction","secure":"false",'
        '"httpOnly":"false","expires":"1777777777"},'
        '{"name":"b","value":"2","sameSite":"unspecified","expires":null}]'
    )
    by_name = {c["name"]: c for c in parse_cookies(payload)}
    assert by_name["a"]["sameSite"] == "None"
    assert by_name["a"]["secure"] is True  # SameSite=None требует Secure
    assert by_name["a"]["httpOnly"] is False
    assert by_name["a"]["expires"] == 1777777777.0
    assert by_name["b"]["sameSite"] == "Lax"
    assert by_name["b"]["expires"] == -1.0


def test_parse_cookies_empty_and_garbage() -> None:
    assert parse_cookies("") == []
    assert parse_cookies(None) == []
    assert parse_cookies("garbage-without-equals") == []


# --- parse_proxy_url ----------------------------------------------------------


def test_parse_proxy_url_http_with_auth() -> None:
    assert parse_proxy_url("http://u:p@1.2.3.4:8080") == {
        "server": "http://1.2.3.4:8080",
        "username": "u",
        "password": "p",
    }


def test_parse_proxy_url_socks5h_maps_to_socks5() -> None:
    cfg = parse_proxy_url("socks5h://u:p@host:1080")
    assert cfg["server"] == "socks5://host:1080"


def test_parse_proxy_url_rejects_socks4() -> None:
    with pytest.raises(MetaSessionError):
        parse_proxy_url("socks4://host:1080")


def test_parse_proxy_url_requires_scheme() -> None:
    with pytest.raises(MetaSessionError):
        parse_proxy_url("host:8080")


# --- MetaSessionManager: защита от бана ---------------------------------------


async def test_start_without_proxy_is_blocked_before_playwright(tmp_path, monkeypatch) -> None:
    """Без прокси браузер не запускается вообще — cookies не показываются чужому IP."""
    manager = MetaSessionManager()
    manager._session_dir = tmp_path

    with pytest.raises(MetaSessionError, match="требует прокси"):
        await manager.start(cookies="c_user=1", proxy_url="   ")


async def test_start_with_dead_proxy_is_blocked(tmp_path, monkeypatch) -> None:
    """Нерабочий прокси — та же защита: до Playwright дело не доходит."""
    manager = MetaSessionManager()
    manager._session_dir = tmp_path

    async def fake_check(proxy_url):
        return {"ok": False, "error": "Connection refused"}

    monkeypatch.setattr(meta_session, "check_proxy_url", fake_check)

    with pytest.raises(MetaSessionError, match="Прокси не работает"):
        await manager.start(cookies="c_user=1", proxy_url="http://u:p@1.2.3.4:8080")


# --- API: подключение session без прокси не создаётся -------------------------


async def test_create_session_connection_without_proxy_rejected(database) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/connections",
            json={
                "name": "Сессия без прокси",
                "access_token": "a-token-long-enough-for-schema",
                "auth_method": "session",
            },
        )
    assert response.status_code == 422
    assert "прокси" in response.json()["error"]["message"].lower()


async def test_create_session_connection_with_proxy_saved(database, monkeypatch) -> None:
    def factory(access_token: str, **kwargs) -> MetaClient:
        def handler(request):
            if request.url.path.endswith("/me"):
                return httpx.Response(200, json={"id": "1", "name": "SU"})
            return httpx.Response(
                200,
                json={"data": [{"id": "act_1", "name": "Кабинет", "account_status": 1}]},
            )

        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(meta_router, "MetaClient", factory)

    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/connections",
            json={
                "name": "Сессия с прокси",
                "access_token": "a-token-long-enough-for-schema",
                "auth_method": "session",
                "proxy_url": "http://u:p@1.2.3.4:8080",
            },
        )
    assert response.status_code == 201
    connection_id = uuid.UUID(response.json()["id"])
    assert response.json()["proxy_url"] == "http://u:p@1.2.3.4:8080"
    try:
        async with SessionLocal() as db:
            stored = await db.get(IntegrationConnection, connection_id)
            assert stored.auth_method == "session"
            assert stored.proxy_url == "http://u:p@1.2.3.4:8080"
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(MetaAdAccount).where(MetaAdAccount.connection_id == connection_id))
            await db.execute(delete(IntegrationConnection).where(IntegrationConnection.id == connection_id))
            await db.commit()


async def test_switching_connection_to_session_requires_proxy(database, monkeypatch) -> None:
    """Правка существующего подключения на «Токен сессии» без прокси отклоняется."""
    def factory(access_token: str, **kwargs) -> MetaClient:
        def handler(request):
            if request.url.path.endswith("/me"):
                return httpx.Response(200, json={"id": "1", "name": "SU"})
            return httpx.Response(
                200,
                json={"data": [{"id": "act_1", "name": "Кабинет", "account_status": 1}]},
            )

        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(meta_router, "MetaClient", factory)

    with _admin_client() as client:
        created = client.post(
            "/api/v1/meta/connections",
            json={
                "name": "Переключение способа",
                "access_token": "a-token-long-enough-for-schema",
            },
        )
        assert created.status_code == 201
        connection_id = created.json()["id"]
        try:
            patched = client.patch(
                f"/api/v1/meta/connections/{connection_id}",
                json={"auth_method": "session"},
            )
            assert patched.status_code == 422
            assert "прокси" in patched.json()["error"]["message"].lower()
        finally:
            client.delete(f"/api/v1/meta/connections/{connection_id}")


# --- API: браузерная сессия ----------------------------------------------------


async def test_session_start_without_proxy_is_422(database) -> None:
    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/session/start",
            json={"cookies": "c_user=1"},
        )
    assert response.status_code == 422


async def test_session_status_unknown_is_404(database) -> None:
    with _admin_client() as client:
        response = client.get("/api/v1/meta/session/nope/status")
    assert response.status_code == 404


async def test_proxy_check_endpoint(database, monkeypatch) -> None:
    async def fake_check(proxy_url):
        assert proxy_url == "http://u:p@1.2.3.4:8080"
        return {"ok": True, "ip": "1.2.3.4", "country": None, "latency_ms": 120}

    monkeypatch.setattr(meta_router, "check_proxy_url", fake_check)

    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/proxy/check", json={"proxy_url": "http://u:p@1.2.3.4:8080"}
        )
    assert response.status_code == 200
    assert response.json()["ip"] == "1.2.3.4"


async def test_proxy_check_failure_is_400(database, monkeypatch) -> None:
    async def fake_check(proxy_url):
        return {"ok": False, "error": "Connection refused"}

    monkeypatch.setattr(meta_router, "check_proxy_url", fake_check)

    with _admin_client() as client:
        response = client.post(
            "/api/v1/meta/proxy/check", json={"proxy_url": "http://u:p@1.2.3.4:8080"}
        )
    assert response.status_code == 400


# --- Синхронизация: автообновление умершего токена сессии ----------------------


async def test_sync_renews_session_token_on_190(database, monkeypatch) -> None:
    """Meta отбила токен (190) → восстанавливаем сессию, берём новый EAAB, повторяем."""
    import httpx

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Сессия для обновления",
            kind="meta",
            base_url="https://graph.facebook.com/v23.0",
            api_key_encrypted=encrypt_secret("old-session-token-long-enough"),
            auth_method="session",
            proxy_url="http://u:p@1.2.3.4:8080",
            lookback_days=3,
        )
        db.add(connection)
        await db.commit()
        run = SyncRun(connection_id=connection.id, mode="backfill", status=SyncStatus.queued)
        db.add(run)
        await db.commit()
        connection_id = connection.id
        run_id = run.id

    calls = {"count": 0, "tokens": []}

    def factory(access_token: str, **kwargs) -> MetaClient:
        calls["count"] += 1
        calls["tokens"].append(access_token)
        dead = calls["count"] == 1  # первый клиент умирает на adaccounts

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/me"):
                return httpx.Response(200, json={"id": "1", "name": "SU"})
            if request.url.path.endswith("/me/adaccounts"):
                if dead:
                    return httpx.Response(
                        400,
                        json={"error": {"code": 190, "message": "Token expired"}},
                    )
                return httpx.Response(200, json={"data": []})
            if request.url.path.endswith("/me/accounts"):
                return httpx.Response(200, json={"data": []})
            if request.url.path.endswith("/me/businesses"):
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"data": []})

        return MetaClient(access_token, transport=httpx.MockTransport(handler), **kwargs)

    async def fake_renew(self, config):
        assert config["auth_method"] == "session"
        assert config["proxy_url"] == "http://u:p@1.2.3.4:8080"
        return "fresh-eaab-token-long-enough"

    monkeypatch.setattr(MetaSyncEngine, "_renew_session_token", fake_renew)

    try:
        engine = MetaSyncEngine(SessionLocal, client_factory=factory)
        result = await engine.run(str(connection_id), str(run_id), "backfill")
        assert result["status"] == "success"
        assert calls["count"] == 2
        assert calls["tokens"] == [
            "old-session-token-long-enough",
            "fresh-eaab-token-long-enough",
        ]
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(SyncRun).where(SyncRun.connection_id == connection_id))
            await db.execute(
                delete(IntegrationConnection).where(IntegrationConnection.id == connection_id)
            )
            await db.commit()
