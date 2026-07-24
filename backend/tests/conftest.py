import os
import tempfile
from pathlib import Path

import pytest

TEST_DB = Path(tempfile.gettempdir()) / "celestial_crm_test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-that-is-long-enough-for-tests"
os.environ["ADMIN_LOGIN"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-password"

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

