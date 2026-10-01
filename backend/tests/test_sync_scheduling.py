"""Планировщик синхронизаций: что видит воркер, когда получает задание."""

import sqlite3
import uuid

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models import IntegrationConnection, SyncRun, SyncStatus, User
from app.workers.tasks import _schedule_connections
from tests.conftest import TEST_DB


async def test_the_worker_finds_the_run_row_the_scheduler_queued(database) -> None:
    """Задание уходит после коммита — иначе воркер не находит строку прогона.

    Так и было: `delay` звали до фиксации транзакции, воркер соседнего процесса
    успевал прочитать базу раньше, отвечал «missing», и строка оставалась
    «queued» навсегда — подключение с незавершённым прогоном планировщик больше
    не трогал, и синхронизация вставала совсем.
    """
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name="Scheduler check",
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("test-key"),
        )
        db.add(connection)
        await db.commit()
        connection_id = connection.id

    seen = []

    class FakeTask:
        """Воркер в момент получения задания: своё соединение, чужая транзакция.

        Читаем базу отдельным соединением — незафиксированную строку оно не
        увидит, ровно как процесс воркера.
        """

        def delay(self, connection_id: str, run_id: str, mode: str) -> None:
            with sqlite3.connect(TEST_DB) as outside:
                # SQLite держит идентификаторы без дефисов — сверяем в обоих видах.
                found = outside.execute(
                    "select count(*) from sync_runs where id in (?, ?)",
                    (run_id, uuid.UUID(run_id).hex),
                ).fetchone()[0]
            seen.append((connection_id, run_id, mode, found))

    queued = await _schedule_connections("keitaro", FakeTask())

    assert queued >= 1
    mine = [row for row in seen if row[0] == str(connection_id)]
    assert len(mine) == 1
    # Главное: в момент отправки задания строка уже видна снаружи транзакции.
    assert mine[0][3] == 1
    async with SessionLocal() as db:
        run = await db.get(SyncRun, uuid.UUID(mine[0][1]))
        assert run is not None
        assert run.status == SyncStatus.queued
        await db.delete(run)
        stale = await db.get(IntegrationConnection, connection_id)
        await db.delete(stale)
        await db.commit()
