from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    # Медленные запросы (браузерные сессии Meta, прокси-проверки) держат
    # соединение подольше, а параллельных потребителей много: 5+10 на проде
    # высыхали, и API начинал отдавать 500 на ровном месте.
    pool_size=15,
    max_overflow=25,
    pool_timeout=15,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session

