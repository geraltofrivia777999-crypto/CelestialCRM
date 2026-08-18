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
    # The Keitaro offer group the "Оффера" module is limited to.
    keitaro_offers_group: str = "OFFERS"
    # Вложения базы знаний (ТЗ 8.2). Каталог обязан быть на постоянном томе —
    # в контейнере без него статьи потеряют картинки при первом же пересоздании.
    upload_dir: str = "/app/uploads"
    upload_max_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    # Общий лимит файлов базы знаний на один воркспейс. При загрузке строка
    # воркспейса блокируется, поэтому параллельные запросы не обойдут квоту.
    upload_workspace_quota_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    # Несвязанный файл ждёт сохранения статьи. Если редактор закрыли, устаревший
    # файл будет удалён при следующей загрузке в этот воркспейс.
    upload_unbound_ttl_hours: int = Field(default=24, ge=1)
    meta_sync_enabled: bool = False
    meta_sync_interval_minutes: int = 30
    # Автоправила выключены по умолчанию и отдельно от синхронизации: правило с
    # действием останавливает кампании и меняет бюджеты, и включать это заодно с
    # чтением статистики нельзя.
    meta_rules_enabled: bool = False
    meta_rules_interval_minutes: int = 30
    # Уведомления «Утилит» выключены по умолчанию: включённое расписание на
    # пустых правилах молчит, но включённое на непроверенных — пишет в чат
    # команды, и первое же ложное срабатывание стоит доверия к разделу.
    alerts_enabled: bool = False
    alerts_interval_minutes: int = 5
    meta_api_base: str = "https://graph.facebook.com"
    # Единственное место, где живёт версия Graph API. Meta выпускает новую примерно
    # раз в квартал и снимает поддержку старой через два года — поднимать здесь,
    # а не искать по коду.
    meta_graph_version: str = "v23.0"
    admin_login: str = "admin"
    admin_password: str = "change-me-now"
    session_days: int = 14


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
