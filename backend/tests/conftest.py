import os
import tempfile
from pathlib import Path

import pytest

TEST_DB = Path(tempfile.gettempdir()) / "celestial_crm_test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-that-is-long-enough-for-tests"
os.environ["ADMIN_LOGIN"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-password"
# Браузерные сессии Meta Ads не должны трогать реальные каталоги проекта.
os.environ["META_SESSION_DIR"] = str(Path(tempfile.gettempdir()) / "celestial_meta_sessions")
# Вложения — туда же: по умолчанию это /app/uploads, каталог контейнера. На
# машине разработчика и на раннере CI его создать нельзя, и тесты падали с
# PermissionError ещё до первой проверки.
os.environ["UPLOAD_DIR"] = str(Path(tempfile.gettempdir()) / "celestial_uploads")
# Тесты ходят по http://test: Secure-куки с продового .env там просто не
# отправятся, и весь сьют упадёт в 401. Явно выключаем.
os.environ["COOKIE_SECURE"] = "false"

from app.core.database import engine  # noqa: E402
from app.models import Base  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def database():
    if TEST_DB.exists():
        TEST_DB.unlink()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await seed()
    yield
    await engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()

