import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret, hash_password
from app.main import app
from app.models import IntegrationConnection, Role, Status, SyncRun, SyncStatus, User

REFRESH = "/api/v1/integrations/keitaro/refresh"


async def _connection(name: str, last_sync_at: datetime | None, status: Status) -> str:
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name=name,
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("test-key"),
            status=status,
            last_sync_at=last_sync_at,
        )
        db.add(connection)
        await db.commit()
        return str(connection.id)


async def _runs(connection_id: str) -> list[SyncRun]:
    async with SessionLocal() as db:
        return list(
            (
                await db.execute(
                    select(SyncRun).where(SyncRun.connection_id == uuid.UUID(connection_id))
                )
            ).scalars()
        )


async def _buyer(suffix: str) -> tuple[str, str]:
    """Баер из стандартной роли: права Медиаборда есть, настроек — нет."""
    login, password = f"refreshbuyer{suffix}", "refresh-password"
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        # Роль ищем в своём воркспейсе: соседние тесты заводят «Buyer» с другим
        # набором прав в отдельных воркспейсах.
        role = await db.scalar(
            select(Role).where(Role.workspace_id == admin.workspace_id, Role.name == "Buyer")
        )
        db.add(
            User(
                workspace_id=admin.workspace_id,
                role_id=role.id,
                name="Refresh buyer",
                login=login,
                password_hash=hash_password(password),
            )
        )
        await db.commit()
    return login, password


async def _drop(connection_ids: list[str], run_ids: list[str]) -> None:
    """Убрать свои подключения и все запуски, которые поставило обновление.

    База у тестов общая: соседние берут первое попавшееся подключение и
    смотрят, не идёт ли синхронизация. Обновление ставит запуски и чужим
    устаревшим подключениям воркспейса — их тоже убираем.
    """
    ids = [uuid.UUID(value) for value in connection_ids]
    runs = [uuid.UUID(value) for value in run_ids]
    async with SessionLocal() as db:
        await db.execute(delete(SyncRun).where(SyncRun.id.in_(runs)))
        await db.execute(delete(SyncRun).where(SyncRun.connection_id.in_(ids)))
        await db.execute(delete(IntegrationConnection).where(IntegrationConnection.id.in_(ids)))
        await db.commit()


async def test_buyer_refreshes_keitaro_from_the_mediaboard(database, monkeypatch) -> None:
    """Баер обновляет данные Keitaro с Медиаборда, хотя запуск в настройках ему закрыт.

    Устаревшее подключение уходит в очередь, свежее и выключенное — нет, а
    повторное нажатие не плодит вторую синхронизацию.
    """
    from app.api.routers import integrations

    queued: list[tuple] = []
    monkeypatch.setattr(
        integrations.sync_keitaro_connection, "delay", lambda *args: queued.append(args)
    )
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    stale = await _connection(
        f"Refresh stale {suffix}", now - timedelta(minutes=10), Status.active
    )
    fresh = await _connection(
        f"Refresh fresh {suffix}", now - timedelta(seconds=10), Status.active
    )
    off = await _connection(f"Refresh off {suffix}", None, Status.inactive)
    login, password = await _buyer(suffix)

    try:
        with TestClient(app) as client:
            assert client.post(REFRESH).status_code == 401
            signed_in = client.post(
                "/api/v1/auth/login", json={"login": login, "password": password}
            )
            assert signed_in.status_code == 200
            # Ручной запуск из настроек баеру по-прежнему недоступен.
            assert client.post(f"/api/v1/integrations/keitaro/{stale}/sync").status_code == 403

            first = client.post(REFRESH)
            assert first.status_code == 202, first.text
            assert first.json()["state"] == "syncing"
            started = [args[0] for args in queued]
            assert stale in started
            assert fresh not in started
            assert off not in started
            assert all(args[2] == "incremental" for args in queued)

            count = len(queued)
            again = client.post(REFRESH)
            assert again.status_code == 202
            assert again.json()["state"] == "syncing"
            assert len(queued) == count

        runs = await _runs(stale)
        assert len(runs) == 1
        assert runs[0].details["source"] == "mediaboard"
        assert await _runs(fresh) == []
        assert await _runs(off) == []
    finally:
        await _drop([stale, fresh, off], [args[1] for args in queued])


async def test_old_never_started_failure_does_not_hide_latest_success(database) -> None:
    """Потерянное задание без started_at не остаётся вечной ошибкой в меню."""
    suffix = uuid.uuid4().hex[:8]
    connection_id = await _connection(
        f"Latest successful {suffix}", datetime.now(UTC), Status.active
    )
    now = datetime.now(UTC)
    async with SessionLocal() as db:
        old_failure = SyncRun(
            connection_id=uuid.UUID(connection_id),
            status=SyncStatus.failed,
            mode="incremental",
            created_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=1),
            error="TimeoutError: old queued run",
        )
        latest_success = SyncRun(
            connection_id=uuid.UUID(connection_id),
            status=SyncStatus.success,
            mode="incremental",
            created_at=now,
            started_at=now,
            finished_at=now,
            progress_pct=100,
        )
        db.add_all([old_failure, latest_success])
        await db.commit()
        run_ids = [str(old_failure.id), str(latest_success.id)]

    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/login",
                json={"login": "admin", "password": "test-password"},
            )
            assert login.status_code == 200
            status = client.get("/api/v1/integrations/keitaro/sidebar-status")
            assert status.status_code == 200
            # В общем тестовом воркспейсе другой сценарий может держать свежую
            # задачу в очереди. Важно, что старая ошибка без started_at больше
            # не объявляется последним состоянием.
            assert status.json()["state"] != "error"

            overview = client.get("/api/v1/integrations/keitaro/overview")
            row = next(
                item
                for item in overview.json()["connections"]
                if item["id"] == connection_id
            )
            assert row["last_run"]["id"] == run_ids[1]
            assert row["last_run"]["status"] == "success"
    finally:
        await _drop([connection_id], run_ids)
