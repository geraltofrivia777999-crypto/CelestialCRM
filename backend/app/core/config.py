from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    secret_key: str = Field(default="development-secret-change-me-please")
    field_encryption_key: str | None = None
    database_url: str = "sqlite+aiosqlite:///./celestial.db"
    redis_url: str = "redis://localhost:6379/0"
    frontend_url: str = "http://localhost:5173"
    cookie_secure: bool = False
    default_timezone: str = "Asia/Qyzylorda"
    default_currency: str = "USD"
    keitaro_sync_enabled: bool = False
    keitaro_sync_interval_minutes: int = 15
    admin_login: str = "admin"
    admin_password: str = "change-me-now"
    session_days: int = 14


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

