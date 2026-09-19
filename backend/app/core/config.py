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
    default_timezone: str = "Europe/Moscow"
    default_currency: str = "USD"
    keitaro_sync_enabled: bool = False
    keitaro_sync_interval_minutes: int = 15
    # Журнал конверсий опрашивается отдельно от общей синхронизации: депозит
    # должен доехать до чата за минуту, а полный круг идёт четверть часа.
    keitaro_conversions_interval_minutes: int = 1
    # The Keitaro offer group the "Оффера" module is limited to.
    keitaro_offers_group: str = "OFFERS"
    # Вложения базы знаний (ТЗ 8.2). Каталог обязан быть на постоянном томе —
    # в контейнере без него статьи потеряют картинки при первом же пересоздании.
    upload_dir: str = "/app/uploads"
    # Через Cloudflare один HTTP-запрос ограничен примерно сотней мегабайт.
    # Оставляем запас под multipart-заголовки, чтобы файл до 90 МБ стабильно
    # проходил внешний прокси и уже проверялся приложением.
    upload_max_bytes: int = Field(default=90 * 1024 * 1024, gt=0)
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
    alerts_interval_minutes: int = 1
    # --- Модерация комментариев ---
    # Пауза между вызовами Graph внутри одного задания. Комментарии Meta
    # считает пользовательским действием, и пачка удалений без пауз — самый
    # быстрый способ получить чекпоинт на аккаунте. Секунда с небольшим примерно
    # соответствует темпу ручной чистки.
    meta_comments_delay_ms: int = 1200
    # Потолок на одно задание. Не столько про лимиты Graph, сколько про то,
    # чтобы случайный «выделить всё» не превратился в тысячу удалений.
    meta_comments_max_per_job: int = 300
    # Сколько страниц по 100 комментариев тянуть с одного поста.
    meta_comments_pages_per_post: int = 5
    # Постов за одно задание на загрузку.
    meta_comments_max_posts: int = 50
    meta_api_base: str = "https://graph.facebook.com"
    # --- Partner Integration Service (раздел «Интеграция ПП») ---
    # Сервис живёт рядом с CRM и сам ходит в партнёрские программы. Его адрес и
    # общий ключ — настройка развёртывания, а не воркспейса: в интерфейсе их не
    # вводят и не видят. В карточке интеграции указывают доступы к самой ПП.
    partner_service_base_url: str = "http://partner-integrations:8000"
    partner_service_token: str = ""
    # Платформа ПП → шаблон коннектора на сервисе. Сервис хранит их под
    # числовыми id; актуальные видно в его GET /api/v1/integrations. Если id
    # разъедутся, переопределяется через .env одной строкой JSON.
    partner_platform_templates: dict[str, int] = {
        "affise": 1,
        "alanbase": 2,
        "afftech": 3,
    }

    # --- Recruitment Service (раздел «Рекрутинг») ---
    # Отдельный сервис команды на той же VPS: шаблоны поиска HH, автопоиск,
    # скоринг кандидатов. CRM только читает и пишет через его HTTP API —
    # своими данными (кандидаты, запуски) сервис владеет сам.
    recruitment_api_base: str = "http://recruitment-api:8000"
    # Общий секрет INTERNAL_SERVICE_TOKEN из .env Recruitment Service. Пусто —
    # раздел отвечает «сервис не настроен», а не падает на каждом запросе.
    recruitment_service_token: str = ""
    # Единственное место, где живёт версия Graph API. Meta выпускает новую примерно
    # раз в квартал и снимает поддержку старой через два года — поднимать здесь,
    # а не искать по коду.
    meta_graph_version: str = "v23.0"
    # --- Браузер для подключений «Токен сессии (EAAB)» ---
    # Токен сессии живёт, пока жива сессия аккаунта в браузере, поэтому его
    # получают не вставкой готовой строки, а из живого браузера с cookies и
    # прокси — как это делает сам человек, открывший Ads Manager. Браузер всегда
    # headful (headless заметно сильнее палится) и всегда запускается только
    # через проверенный прокси: иначе cookies «увидят» чужой IP, и Meta забанит
    # аккаунт, а не токен.
    meta_session_enabled: bool = True
    # Канал браузера: "chrome" — установленный Google Chrome (лучше для
    # антидетекта), "" — встроенный Chromium Playwright. При недоступности
    # канала происходит автоматический откат на Chromium.
    meta_browser_channel: str = "chrome"
    meta_browser_headless: bool = False
    meta_browser_viewport_width: int = 1366
    meta_browser_viewport_height: int = 768
    meta_browser_locale: str = "ru-RU"
    meta_browser_timezone: str = "Europe/Moscow"
    meta_browser_language_header: str = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    # Сколько ждём ручной вход через VNC (капча/чекпойнт автоматику не пропускают).
    meta_login_timeout_sec: int = 900
    meta_page_timeout_ms: int = 45000
    # Сохранённые браузерные сессии: после входа storage_state пишется на диск,
    # чтобы при смерти токена синхронизация могла восстановить сессию, заново
    # извлечь EAAB и продолжить работу без участия человека.
    meta_session_dir: str = "/app/meta_sessions"
    meta_session_ttl_hours: int = 72
    meta_facebook_url: str = "https://www.facebook.com"
    meta_facebook_login_url: str = "https://www.facebook.com/login"
    meta_ads_manager_url: str = "https://adsmanager.facebook.com/adsmanager/manage/"
    admin_login: str = "admin"
    admin_password: str = "change-me-now"
    session_days: int = 14


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
