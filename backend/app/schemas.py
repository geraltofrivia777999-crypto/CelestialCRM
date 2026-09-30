import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import ArticleStatus, OfferStatus, ProviderType, Status, TaskPriority
from app.services.meta_spend import validate_window as meta_spend_validate_window


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    login: str
    password: str


class PermissionOut(ORMModel):
    code: str
    description: str


class RoleOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    data_scope: str = "team"
    show_finance_summaries: bool = True
    # Системную роль (администратора) нельзя удалить — кнопке это нужно знать.
    is_system: bool = False
    permissions: list[PermissionOut] = Field(default_factory=list)


class UserParentOut(BaseModel):
    id: uuid.UUID
    name: str
    login: str


class UserOut(ORMModel):
    id: uuid.UUID
    name: str
    login: str
    status: Status
    role: RoleOut
    parents: list[UserParentOut] = Field(default_factory=list)
    keitaro_company_group: str | None = None
    keitaro_offer_group: str | None = None
    finance_tags: list[str] = Field(default_factory=list)
    team_name: str | None = None


class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    login: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    role_id: uuid.UUID
    status: Status = Status.active
    parent_ids: list[uuid.UUID] = Field(default_factory=list)
    # Подчинённые — та же связь с другой стороны: в базе она одна, но в карточке
    # человека удобнее назначать иерархию в обе стороны сразу.
    child_ids: list[uuid.UUID] = Field(default_factory=list)
    keitaro_company_group: str | None = None
    keitaro_offer_group: str | None = None
    finance_tags: list[str] = Field(default_factory=list, max_length=50)
    team_name: str | None = Field(default=None, max_length=120)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    login: str | None = Field(default=None, min_length=3, max_length=100)
    role_id: uuid.UUID | None = None
    status: Status | None = None
    parent_ids: list[uuid.UUID] | None = None
    child_ids: list[uuid.UUID] | None = None
    keitaro_company_group: str | None = Field(default=None, max_length=160)
    keitaro_offer_group: str | None = Field(default=None, max_length=160)
    finance_tags: list[str] | None = Field(default=None, max_length=50)
    team_name: str | None = Field(default=None, max_length=120)


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=255)
    # Чьи данные видит роль: весь воркспейс, своя ветка подчинённых или только
    # свои строки.
    data_scope: Literal["all", "team", "own"] = "team"
    # Видит ли роль сводки «Общая», «Tier1», «Tier2/3» в Финансах.
    show_finance_summaries: bool = True
    permission_codes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=255)
    data_scope: Literal["all", "team", "own"] | None = None
    show_finance_summaries: bool | None = None
    permission_codes: list[str] | None = None


class ServiceIn(BaseModel):
    name: str
    install_cost: Decimal = Decimal("0")
    commission_pct: Decimal = Decimal("0")
    status: Status = Status.active


class ServiceOut(ORMModel):
    id: uuid.UUID
    name: str
    install_cost: Decimal
    commission_pct: Decimal
    status: Status


class SpendProviderIn(BaseModel):
    name: str
    provider_type: ProviderType
    commission_pct: Decimal = Decimal("0")
    status: Status = Status.active


class SpendProviderOut(ORMModel):
    id: uuid.UUID
    name: str
    provider_type: ProviderType
    commission_pct: Decimal
    status: Status


class ConnectionCreate(BaseModel):
    name: str
    base_url: str
    api_key: str
    sync_interval_minutes: int = Field(default=15, ge=5, le=1440)
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=64)
    buyer_sub_id: int = Field(default=1, ge=1, le=10)
    lookback_days: int = Field(default=2, ge=1, le=14)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return validate_keitaro_base_url(value)


class ConnectionUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    status: Status | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    buyer_sub_id: int | None = Field(default=None, ge=1, le=10)
    lookback_days: int | None = Field(default=None, ge=1, le=14)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return validate_keitaro_base_url(value) if value is not None else None


def validate_keitaro_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Укажите полный URL вашего Keitaro-трекера")
    if parsed.netloc.lower() in {"admin-api.docs.keitaro.io", "docs.keitaro.io"}:
        raise ValueError("Нужен URL вашего трекера, а не адрес документации Keitaro")
    return normalized


class ConnectionOut(ORMModel):
    id: uuid.UUID
    name: str
    kind: str
    base_url: str
    status: Status
    sync_interval_minutes: int
    timezone: str
    buyer_sub_id: int
    lookback_days: int
    checkpoint_at: datetime | None
    last_sync_at: datetime | None


class MetaConnectionCreate(BaseModel):
    """Подключение Business Manager — ТЗ 3.2.

    `business_id` не обязателен: без него берутся кабинеты, доступные владельцу
    токена, с ним — кабинеты, принадлежащие конкретному Business Manager.
    """

    name: str = Field(min_length=1, max_length=120)
    access_token: str = Field(min_length=20)
    business_id: str | None = Field(default=None, max_length=100)
    # Чем выпущен токен. На запросы к Graph API не влияет — влияет на то, что
    # сказать человеку, когда токен умрёт, а умирают они по-разному.
    auth_method: Literal["system_user", "app_token", "session"] = "system_user"
    proxy_url: str | None = Field(default=None, max_length=500)
    user_agent: str | None = Field(default=None, max_length=500)
    sync_interval_minutes: int = Field(default=30, ge=15, le=1440)
    lookback_days: int = Field(default=3, ge=1, le=14)
    attribution_sub_id: int | None = Field(default=None, ge=1, le=10)
    # Шаг «Импорт» в мастере. Пустой список означает «все, что видно токеном»:
    # так ведёт себя подключение, созданное без мастера.
    import_accounts: list[str] = Field(default_factory=list, max_length=200)
    # Живая браузерная сессия мастера: проверка токена сессии идёт через её
    # контекст — обычный httpx-запрос Meta отклонит.
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("business_id")
    @classmethod
    def validate_business_id(cls, value: str | None) -> str | None:
        return normalize_business_id(value)

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy(cls, value: str | None) -> str | None:
        return validate_proxy_url(value)


def normalize_business_id(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    if not clean:
        return None
    if not clean.isdigit():
        raise ValueError("Business ID состоит только из цифр")
    return clean


class MetaConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    access_token: str | None = Field(default=None, min_length=20)
    business_id: str | None = Field(default=None, max_length=100)
    auth_method: Literal["system_user", "app_token", "session"] | None = None
    proxy_url: str | None = Field(default=None, max_length=500)
    user_agent: str | None = Field(default=None, max_length=500)
    status: Status | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=15, le=1440)
    lookback_days: int | None = Field(default=None, ge=1, le=14)
    attribution_sub_id: int | None = Field(default=None, ge=1, le=10)
    # Живая браузерная сессия (повторный вход через модалку) — для проверки
    # нового токена сессии из её контекста.
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("business_id")
    @classmethod
    def validate_business_id(cls, value: str | None) -> str | None:
        return normalize_business_id(value)


def validate_proxy_url(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    if not clean:
        return None
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
        raise ValueError(
            "Прокси задаётся в виде http://логин:пароль@хост:порт "
            "(или socks5://…) — без схемы клиент его не примет"
        )
    return clean


class MetaConnectionOut(ORMModel):
    id: uuid.UUID
    owner_id: uuid.UUID | None = None
    owner_name: str | None = None
    can_edit: bool = False
    name: str
    status: Status
    sync_interval_minutes: int
    lookback_days: int
    business_id: str | None = Field(default=None, validation_alias="external_account_id")
    auth_method: str = "system_user"
    proxy_url: str | None = None
    user_agent: str | None = None
    attribution_sub_id: int | None
    checkpoint_at: datetime | None
    last_sync_at: datetime | None


class MetaAccountUpdate(BaseModel):
    """Единственное, что в кабинете принадлежит CRM, а не Meta."""

    owner_id: uuid.UUID | None = None
    status: Status | None = None


class MetaAccountActionIn(BaseModel):
    """Действие с рекламным кабинетом в Meta из MetaAds v2.

    `spend_cap_action`: «set» — новый лимит, «reset» — обнулить потраченное
    с текущим лимитом, «delete» — снять лимит совсем.
    """

    action: Literal["rename", "spend_cap", "pixel"]
    name: str | None = Field(default=None, max_length=300)
    spend_cap: Decimal | None = Field(default=None, gt=0)
    spend_cap_action: Literal["set", "reset", "delete"] = "set"


class MetaGeoRuleIn(BaseModel):
    """Строка таблицы GEO-правил. Пустой порог — проверка выключена."""

    is_enabled: bool = True
    no_clicks: Decimal | None = Field(default=None, gt=0, le=1000000)
    no_insts: Decimal | None = Field(default=None, gt=0, le=1000000)
    no_regs: Decimal | None = Field(default=None, gt=0, le=1000000)
    no_deps: Decimal | None = Field(default=None, gt=0, le=1000000)
    max_avg_inst: Decimal | None = Field(default=None, gt=0, le=1000000)
    max_avg_reg: Decimal | None = Field(default=None, gt=0, le=1000000)
    max_avg_dep: Decimal | None = Field(default=None, gt=0, le=1000000)


class MetaGeoRuleSetIn(BaseModel):
    """Новое автоправило: сначала название и уровень, пороги GEO — внутри."""

    name: str = Field(min_length=1, max_length=160)
    level: Literal["campaign", "adset", "ad"] = "campaign"


class MetaGeoRuleSetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    level: Literal["campaign", "adset", "ad"] | None = None
    interval_minutes: Literal[15, 30, 60, 120, 240] | None = None
    auto_enabled: bool | None = None


class MetaConnectionPreview(BaseModel):
    """Шаг «Проверка» в мастере: токен уже введён, но ещё ничего не сохранено."""

    access_token: str = Field(min_length=20)
    business_id: str | None = Field(default=None, max_length=100)
    # Проверка идёт тем же маршрутом, что и работа: токен, привязанный к
    # прокси, без него кабинетов не покажет, и «проверка прошла» была бы
    # обещанием, которое не выполнится при первой синхронизации.
    proxy_url: str | None = Field(default=None, max_length=500)
    user_agent: str | None = Field(default=None, max_length=500)
    # Живая браузерная сессия мастера для токена сессии.
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("business_id")
    @classmethod
    def validate_business_id(cls, value: str | None) -> str | None:
        return normalize_business_id(value)

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy(cls, value: str | None) -> str | None:
        return validate_proxy_url(value)


class MetaSessionStart(BaseModel):
    """Запуск браузерной сессии для получения EAAB-токена.

    Прокси обязателен: браузер без него не стартует — это защита аккаунта от
    бана (cookies, показанные чужому IP, помечают сессию как угнанную).
    """

    # Экспорт cookies из расширений (JSON-массив с доменами и флагами) бывает
    # в десятки раз больше простой строки «имя=значение» — запас нужен щедрый.
    cookies: str = Field(default="", max_length=500000)
    proxy_url: str = Field(..., max_length=500)
    user_agent: str | None = Field(default=None, max_length=500)
    # Для повторного входа существующего подключения: сохранённая сессия
    # ляжет в <connection_id>.json, и синхронизация сможет обновлять токен сама.
    connection_id: str | None = Field(default=None, max_length=128)

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy(cls, value: str) -> str:
        validated = validate_proxy_url(value)
        if not validated:
            raise ValueError("Прокси обязателен для токена сессии (EAAB)")
        return validated


class MetaSessionAttach(BaseModel):
    """Привязка сохранённой браузерной сессии к подключению Meta."""

    connection_id: str = Field(..., max_length=128)


class MetaProxyCheck(BaseModel):
    proxy_url: str = Field(..., max_length=500)

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy(cls, value: str) -> str:
        validated = validate_proxy_url(value)
        if not validated:
            raise ValueError("Укажите прокси в виде http://логин:пароль@хост:порт")
        return validated


class MetaBundleCampaign(BaseModel):
    """Блок «Кампании» связки — ТЗ 3.5.

    `goal` это не `objective`: команда мыслит девятью целями, а Meta знает
    шесть. Пресет цели раскладывается на `objective` и `optimization_goal`
    сервером при сохранении, чтобы в кабинет ушло то, что ждёт Graph API.
    """

    goal: str = Field(default="leads", max_length=40)
    advantage: bool = False
    campaign_name: str = Field(default="{{bundle.name}}", max_length=200)
    budget_kind: Literal["daily", "lifetime"] = "daily"
    budget_level: Literal["campaign", "adset"] = "campaign"
    budget_currency: str = Field(default="USD", min_length=3, max_length=3)
    # Рандомизация бюджета: каждому кабинету достаётся сумма в пределах ±N %.
    # Одинаковая цифра на двадцати кабинетах — заметный след, и Meta это видит.
    budget_randomize: bool = False
    budget_randomize_pct: Decimal = Field(default=Decimal("10"), gt=0, le=90)
    adset_budget_limit: Decimal | None = Field(default=None, ge=0)
    bid_amount: Decimal | None = Field(default=None, ge=0)
    accelerated_delivery: bool = False
    special_ad_categories: list[str] = Field(default_factory=list, max_length=5)


class MetaBundleAdset(BaseModel):
    """Блок «Адсеты» связки. Возраст, пол, языки и интересы лежат в самой связке
    отдельными полями — здесь только то, чего в ней раньше не было."""

    adset_name: str = Field(default="adset #{{adset.number}}", max_length=200)
    attribution: str = Field(default="7d_click_1d_view", max_length=40)
    engaged_view: Literal["none", "1d", "7d"] = "1d"
    advantage_audience: bool = False
    age_randomize: bool = False
    # На сколько лет разбрасывать возраст. Ноль означает «на сколько-нибудь»:
    # включённый тумблер без числа не должен молча ничего не делать.
    age_randomize_years: int = Field(default=3, ge=1, le=10)
    location_type: str = Field(default="home", max_length=20)
    geo_regions: list[str] = Field(default_factory=list, max_length=100)
    geo_cities: list[str] = Field(default_factory=list, max_length=100)
    excluded_geo: list[str] = Field(default_factory=list, max_length=50)
    excluded_interests: list[dict] = Field(default_factory=list, max_length=100)
    # Названия языков рядом с их ID: в колонку `languages` уходят одни ID, а
    # показать в форме «6» вместо «English (US)» — значит заставить баера
    # держать справочник Meta в голове.
    language_labels: list[dict] = Field(default_factory=list, max_length=50)
    targeting_expansion: bool = True
    auto_placements: bool = True
    devices: Literal["all", "desktop", "mobile"] = "all"
    os: Literal["all", "android", "ios"] = "all"
    android_smartphone: bool = True
    android_tablet: bool = True
    # Только нижняя граница: в `user_os` Meta принимает «версия и выше», поля
    # под верхнюю границу в Graph API нет, и рисовать её значило бы обещать
    # фильтр, которого не будет.
    android_min: str = Field(default="", max_length=10)
    ios_iphone: bool = True
    ios_ipad: bool = True
    ios_ipod: bool = True
    ios_min: str = Field(default="", max_length=10)
    wifi_only: bool = False

    @field_validator("excluded_geo")
    @classmethod
    def validate_excluded(cls, value: list[str]) -> list[str]:
        codes = [str(code).strip().upper() for code in value if str(code).strip()]
        if any(len(code) != 2 or not code.isalpha() for code in codes):
            raise ValueError("GEO указывается двухбуквенными кодами стран, например DE")
        return codes


class MetaEntityActionItem(BaseModel):
    """Объект структуры и то, что с ним сделать. `id` — id объекта в Meta."""

    id: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, max_length=300)
    daily_budget: Decimal | None = Field(default=None, gt=0)
    bid_strategy: str | None = Field(default=None, max_length=60)
    bid_amount: Decimal | None = Field(default=None, gt=0)


class MetaEntityActionIn(BaseModel):
    """Действие над отмеченными кампаниями, адсетами или объявлениями."""

    level: Literal["campaigns", "adsets", "ads"]
    action: Literal[
        "start", "pause", "archive", "unarchive", "delete", "duplicate", "rename", "budget"
    ]
    items: list[MetaEntityActionItem] = Field(min_length=1, max_length=100)


class MetaBundleAdText(BaseModel):
    """Тексты объявления на одном языке — для мультиязычной связки.

    `language` — локаль Meta («en_GB»), `language_name` — человеческое имя: по
    нему публикация находит числовой ID локали для правил показа на языке.
    """

    language: str = Field(min_length=2, max_length=20)
    language_name: str | None = Field(default=None, max_length=80)
    headline: str | None = Field(default=None, max_length=600)
    primary_text: str | None = Field(default=None, max_length=3000)
    description: str | None = Field(default=None, max_length=600)


class MetaBundleAd(BaseModel):
    """Блок «Объявления» связки. Тексты допускают spintax — `{вариант|вариант}`.

    Разворачивается spintax при публикации, а не здесь: у каждого объявления
    должен получиться свой вариант, а связка одна на все.
    """

    ad_name: str = Field(default="ad #{{ad.number}}", max_length=200)
    multilingual: bool = False
    # Мультиязычная связка: свой заголовок, текст и описание на каждый язык.
    # При заливе из неё сразу включаются «Языки» с этими текстами.
    texts: list[MetaBundleAdText] = Field(default_factory=list, max_length=30)
    headline: str | None = Field(default=None, max_length=600)
    primary_text: str | None = Field(default=None, max_length=3000)
    description: str | None = Field(default=None, max_length=600)
    link_url: str | None = Field(default=None, max_length=2000)
    multi_advertiser: bool = False
    advantage_creative: bool = False


class MetaBundleSettings(BaseModel):
    """Связка целиком тремя блоками — так же, как её собирают в мастере."""

    campaign: MetaBundleCampaign = Field(default_factory=MetaBundleCampaign)
    adset: MetaBundleAdset = Field(default_factory=MetaBundleAdset)
    ad: MetaBundleAd = Field(default_factory=MetaBundleAd)


class MetaTemplateBase(BaseModel):
    """Шаблон залива — ТЗ 3.5. Значения проверяются по справочникам Meta."""

    objective: Literal[
        "OUTCOME_SALES",
        "OUTCOME_LEADS",
        "OUTCOME_TRAFFIC",
        "OUTCOME_ENGAGEMENT",
        "OUTCOME_AWARENESS",
        "OUTCOME_APP_PROMOTION",
    ] = "OUTCOME_LEADS"
    optimization_goal: Literal[
        "OFFSITE_CONVERSIONS",
        "LINK_CLICKS",
        "LANDING_PAGE_VIEWS",
        "LEAD_GENERATION",
        "IMPRESSIONS",
        "REACH",
        "VALUE",
        "POST_ENGAGEMENT",
        "PAGE_LIKES",
        "APP_INSTALLS",
        "CONVERSATIONS",
    ] = "LINK_CLICKS"
    billing_event: Literal["IMPRESSIONS", "LINK_CLICKS"] = "IMPRESSIONS"
    bid_strategy: Literal[
        "LOWEST_COST_WITHOUT_CAP", "LOWEST_COST_WITH_BID_CAP", "COST_CAP"
    ] = "LOWEST_COST_WITHOUT_CAP"
    geo: list[str] = Field(default_factory=list, max_length=50)
    age_min: int = Field(default=18, ge=18, le=65)
    age_max: int = Field(default=65, ge=18, le=65)
    genders: list[int] = Field(default_factory=list)
    languages: list[int] = Field(default_factory=list)
    placements: dict = Field(default_factory=dict)
    interests: list[dict] = Field(default_factory=list)
    daily_budget: Decimal | None = Field(default=None, ge=0)
    lifetime_budget: Decimal | None = Field(default=None, ge=0)
    page_id: str | None = Field(default=None, max_length=60)
    pixel_id: str | None = Field(default=None, max_length=60)
    custom_event_type: str | None = Field(default=None, max_length=60)
    call_to_action: str = Field(default="LEARN_MORE", max_length=40)
    notes: str | None = None
    settings: MetaBundleSettings = Field(default_factory=MetaBundleSettings)

    @field_validator("geo")
    @classmethod
    def validate_geo(cls, value: list[str]) -> list[str]:
        codes = [str(code).strip().upper() for code in value if str(code).strip()]
        if any(len(code) != 2 or not code.isalpha() for code in codes):
            raise ValueError("GEO указывается двухбуквенными кодами стран, например DE")
        return codes

    @field_validator("genders")
    @classmethod
    def validate_genders(cls, value: list[int]) -> list[int]:
        # 1 — мужчины, 2 — женщины. Пустой список означает «все», и это не то же
        # самое, что перечислить оба: Meta трактует их одинаково, но пустой
        # список короче и не ломается при добавлении новых значений.
        if any(item not in (1, 2) for item in value):
            raise ValueError("Пол задаётся значениями 1 (мужчины) и 2 (женщины)")
        return value


class MetaTemplateCreate(MetaTemplateBase):
    name: str = Field(min_length=1, max_length=160)


class MetaTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    objective: str | None = Field(default=None, max_length=60)
    optimization_goal: str | None = Field(default=None, max_length=60)
    billing_event: str | None = Field(default=None, max_length=40)
    bid_strategy: str | None = Field(default=None, max_length=60)
    geo: list[str] | None = None
    age_min: int | None = Field(default=None, ge=18, le=65)
    age_max: int | None = Field(default=None, ge=18, le=65)
    genders: list[int] | None = None
    languages: list[int] | None = None
    placements: dict | None = None
    interests: list[dict] | None = None
    daily_budget: Decimal | None = Field(default=None, ge=0)
    lifetime_budget: Decimal | None = Field(default=None, ge=0)
    page_id: str | None = Field(default=None, max_length=60)
    pixel_id: str | None = Field(default=None, max_length=60)
    custom_event_type: str | None = Field(default=None, max_length=60)
    call_to_action: str | None = Field(default=None, max_length=40)
    notes: str | None = None
    status: Status | None = None
    settings: MetaBundleSettings | None = None


class MetaLaunchAdText(BaseModel):
    """Тексты объявления на одном языке."""

    language: str = Field(default="", max_length=12)
    # Человеческое имя языка («English (US)»). Meta в multi-language ads
    # принимает числовые ID локалей, а не коды, — по имени их ищем при
    # публикации живым токеном (adlocale-словарь).
    language_name: str | None = Field(default=None, max_length=120)
    headline: str | None = Field(default=None, max_length=600)
    description: str | None = Field(default=None, max_length=600)
    primary_text: str | None = Field(default=None, max_length=3000)
    link_url: str | None = Field(default=None, max_length=2000)
    call_to_action: str | None = Field(default=None, max_length=40)
    # Свой креатив этого языка: у каждого языка объявления может быть своя
    # картинка/видео (правило показа ссылается на него через adlabel).
    creative_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class MetaLaunchAd(BaseModel):
    """Одно объявление залива.

    Языков может быть несколько: Meta показывает зрителю текст на его языке
    сама, поэтому это одно объявление в кабинете, а не по объявлению на язык.
    """

    texts: list[MetaLaunchAdText] = Field(default_factory=list, max_length=20)
    creative_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class MetaLaunchFields(BaseModel):
    """Всё, что описывает залив, кроме кабинета и креативов.

    Кабинет и креативы вынесены в наследников: обычный залив создаётся на один
    кабинет, а мастер «Залить» — сразу на несколько, и креативы там адресуются
    по кабинету (один и тот же файл в другом кабинете имеет другой хэш).
    """

    name: str = Field(min_length=1, max_length=240)
    template_id: uuid.UUID | None = None
    offer_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    geo: str | None = Field(default=None, max_length=12)
    daily_budget: Decimal = Field(default=Decimal("0"), ge=0)
    spend_limit: Decimal | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None
    link_url: str | None = Field(default=None, max_length=2000)
    primary_text: str | None = None
    headline: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=240)
    call_to_action: str = Field(default="LEARN_MORE", max_length=40)
    page_id: str | None = Field(default=None, max_length=60)
    pixel_id: str | None = Field(default=None, max_length=60)
    activate_on_publish: bool = False
    # Блок «Время» мастера. `publish_at` — когда создавать объекты в кабинете,
    # `start_at` — когда им начать крутиться. Это разные вещи: залить ночью и
    # стартовать в полночь понедельника — обычная просьба.
    publish_at: datetime | None = None
    start_at: datetime | None = None
    pause_campaigns: bool = False
    pause_adsets: bool = False
    pause_ads: bool = False
    adset_count: int = Field(default=1, ge=1, le=20)
    url_tags: str | None = Field(default=None, max_length=1000)
    display_link: str | None = Field(default=None, max_length=240)
    # Кастомный нейминг: шаблон имени кампании этого кабинета. Поддерживает те
    # же макросы, что и связка («{{cab.name}}», «{{date}}»…); пустое значение
    # оставляет шаблон связки.
    campaign_name: str | None = Field(default=None, max_length=240)
    # Бенефициар/плательщик DSA-прозрачности. Meta требует его для рекламы на
    # ЕС; одно значение уходит в оба поля адсета.
    beneficiary: str | None = Field(default=None, max_length=255)
    # --- Расширенный режим мастера ---
    # Цель кампании и количество кампаний поверх связки.
    objective: str | None = Field(default=None, max_length=40)
    campaign_count: int = Field(default=1, ge=1, le=20)
    # Блок «Бюджет и ставка»: пусто — берётся из связки.
    budget_level: Literal["campaign", "adset"] | None = None
    budget_kind: Literal["daily", "lifetime"] | None = None
    budget_randomize: bool = False
    budget_randomize_pct: Decimal | None = Field(default=None, gt=0, le=90)
    adset_budget_limit: Decimal | None = Field(default=None, ge=0)
    bid_strategy: str | None = Field(default=None, max_length=40)
    # Автоправила, которые будут привязаны к созданным объектам залива.
    rule_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    # Теги (adlabels) после создания: {"level","names","mode"}.
    tags: dict | None = None
    # --- Расширенный режим v2: цель и прочее поверх связки ---
    custom_event_type: str | None = Field(default=None, max_length=60)
    attribution: str | None = Field(default=None, max_length=20)
    engaged_view: str | None = Field(default=None, max_length=5)
    budget_limit_min: Decimal | None = Field(default=None, ge=0)
    budget_limit_max: Decimal | None = Field(default=None, ge=0)
    # Периоды увеличения бюджета: [{"start_at","end_at","kind","amount"}].
    budget_increases: list[dict] | None = Field(default=None, max_length=50)
    rule_group: str | None = Field(default=None, max_length=160)

    @field_validator("link_url")
    @classmethod
    def validate_link(cls, value: str | None) -> str | None:
        if not value:
            return None
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ссылка должна начинаться с http:// или https://")
        return value.strip()


class MetaLaunchCreate(MetaLaunchFields):
    """Залив — ТЗ 3.3.

    Бюджет и ссылка обязательны уже здесь, а не только при публикации: залив
    без них не является учётной записью о заливе, а является заготовкой.
    """

    account_id: uuid.UUID
    creative_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class MetaLaunchBatch(MetaLaunchFields):
    """Одна связка на несколько кабинетов — третий шаг мастера «Залить».

    Ограничение в 25 кабинетов не бюрократия: каждый залив — это три записи в
    Meta, и пачка на сотню кабинетов упёрлась бы в лимит запросов кабинета
    раньше, чем доехала до конца.
    """

    account_ids: list[uuid.UUID] = Field(min_length=1, max_length=25)
    creatives_by_account: dict[uuid.UUID, list[uuid.UUID]] = Field(default_factory=dict)
    # Что задано на конкретный кабинет: своя страница, пиксель, ссылка и бюджет.
    # Всё остальное общее — кабинетов в пачке до двадцати пяти, и повторять для
    # каждого весь залив было бы переписыванием формы.
    overrides: dict[uuid.UUID, dict] = Field(default_factory=dict)
    # Объявления по кабинетам: у каждого свои тексты, языки и креативы.
    ads_by_account: dict[uuid.UUID, list[MetaLaunchAd]] = Field(default_factory=dict)
    publish: bool = False
    # Пауза между кабинетами: заливы уходят в очередь не одновременно, а через
    # заданный интервал. Двадцать кабинетов, стартующих в одну секунду с одним
    # креативом, — это ровно тот след, из-за которого прилетает бан.
    account_delay_seconds: int = Field(default=0, ge=0, le=3600)


class MetaLaunchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=240)
    account_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None
    offer_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    geo: str | None = Field(default=None, max_length=12)
    daily_budget: Decimal | None = Field(default=None, ge=0)
    spend_limit: Decimal | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None
    link_url: str | None = Field(default=None, max_length=2000)
    primary_text: str | None = None
    headline: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=240)
    call_to_action: str | None = Field(default=None, max_length=40)
    page_id: str | None = Field(default=None, max_length=60)
    pixel_id: str | None = Field(default=None, max_length=60)
    activate_on_publish: bool | None = None
    creative_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)


# Настройки объекта — строки, а не числа: сравнивать их можно только на
# совпадение и вхождение, а порог для них — тоже строка.
RULE_TEXT_METRICS = frozenset(
    {"entity_name", "campaign_name", "objective", "buying_type"}
)
RULE_TEXT_OPERATORS = frozenset({"eq", "ne", "in", "nin"})


class MetaRuleCondition(BaseModel):
    """Одно условие правила. Несколько соединяются И.

    Набор метрик и операторов повторяет то, что умеет движок правил
    (`meta_metrics.METRIC_LABELS` и `meta_rules.OPERATORS`): форма показывает
    их все, и сузить список здесь означало бы отдавать 422 на половину
    выбранного в ней.
    """

    metric: Literal[
        "spend", "roi", "profit", "revenue", "cpl", "cpc", "ctr", "leads", "clicks",
        "impressions", "link_clicks", "link_ctr", "results", "cpa", "cpm",
        "result_cr", "sales", "reach", "pixel_leads", "pixel_purchases",
        "actions_total", "entity_name", "campaign_name", "objective", "buying_type",
        "spend_cap", "bid_amount", "daily_budget", "lifetime_budget",
        "spend_total", "spend_day_pct", "spend_total_pct",
    ] = "roi"
    operator: Literal["lt", "lte", "gt", "gte", "eq", "ne", "in", "nin"] = "lt"
    # Порог числовой у метрик-чисел и строковый у настроек объекта.
    value: Decimal | str = Decimal("0")

    @model_validator(mode="after")
    def validate_pair(self) -> "MetaRuleCondition":
        if self.metric in RULE_TEXT_METRICS:
            if self.operator not in RULE_TEXT_OPERATORS:
                raise ValueError(
                    "Текстовое условие сравнивается только на =, !=, ∈ или ∉"
                )
            if not str(self.value).strip():
                raise ValueError("Укажите текст для сравнения")
        elif isinstance(self.value, str):
            # Числовая метрика с пустым полем — почти наверняка недозаполненная
            # строка условия, а не «сравнить с нулём».
            try:
                self.value = Decimal(self.value.strip() or "x")
            except (ArithmeticError, ValueError) as exc:
                raise ValueError("Укажите число для сравнения") from exc
        return self


class MetaRuleCreate(BaseModel):
    """Автоправило — ТЗ 3.8.

    `min_spend` по умолчанию не ноль: правило, которое срабатывает на кампании с
    парой кликов, приносит больше вреда, чем пользы.

    Пустой список условий разрешён намеренно: «остановить все активные
    объявления» — осмысленное правило, у которого условий нет.
    """

    name: str = Field(min_length=1, max_length=160)
    account_id: uuid.UUID | None = None
    launch_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    level: Literal["campaign", "adset", "ad"] = "campaign"
    scope_kind: Literal["cabinet", "campaign"] = "cabinet"
    campaign_external_id: str | None = Field(default=None, max_length=100)
    entity_status: Literal["active", "paused", "any"] = "active"
    window: Literal[
        "today", "yesterday", "last_2d", "last_3d", "last_7d",
        "last_14d", "last_28d", "last_30d", "month"
    ] = "today"
    schedule_kind: Literal["always", "daily_midnight", "custom"] = "always"
    # {"days": [1..7], "intervals": [{"begin":"HH:MM","end":"HH:MM"}]}.
    schedule: dict | None = None
    convert_currency: bool = False
    currency: str | None = Field(default=None, max_length=8)
    conditions: list[MetaRuleCondition] = Field(default_factory=list, max_length=10)
    min_spend: Decimal = Field(default=Decimal("10"), ge=0)
    action: Literal[
        "notify", "pause", "resume",
        "increase_budget", "decrease_budget",
        "change_budget", "change_bid",
    ] = "notify"
    action_value: Decimal | None = Field(default=None, ge=0, le=100000)
    action_sign: Literal["plus", "minus"] = "plus"
    action_mode: Literal["pct", "sum"] = "pct"
    action_max: Decimal | None = Field(default=None, ge=0)
    budget_kind: Literal["daily", "lifetime"] = "daily"
    is_enabled: bool = True
    frequency_minutes: int = Field(default=60, ge=15, le=1440)
    cooldown_minutes: int = Field(default=180, ge=0, le=10080)

    @model_validator(mode="after")
    def validate_action_value(self) -> "MetaRuleCreate":
        if self.action in {"increase_budget", "decrease_budget"}:
            if not self.action_value:
                raise ValueError("Для изменения бюджета укажите процент")
            if self.level == "ad":
                raise ValueError(
                    "У объявления нет собственного бюджета — выберите кампанию или адсет"
                )
        if self.action in {"change_budget", "change_bid"} and not self.action_value:
            raise ValueError("Укажите, на сколько менять")
        if self.action == "change_budget" and self.level == "ad":
            raise ValueError(
                "У объявления нет собственного бюджета — выберите кампанию или адсет"
            )
        # Ставка живёт только на адсете: на кампании и объявлении менять нечего.
        if self.action == "change_bid" and self.level != "adset":
            raise ValueError("Ставка задаётся на адсете — выберите уровень «Адсет»")
        if self.scope_kind == "campaign" and not self.campaign_external_id:
            raise ValueError("Выберите кампанию, с которой работает правило")
        return self


class MetaSpendCommitIn(BaseModel):
    """Привязка расхода кампаний за отрезок времени к офферу.

    Окно задаётся от часа одного дня до часа другого и может переходить через
    полночь. Полуинтервал: «с 12:00 по 16:00» это часы 12, 13, 14 и 15 — иначе
    шестнадцатый час попадал бы и в это окно, и в следующее. Медиаборд живёт
    записями «день + баер + оффер», поэтому длинное окно сервер раскладывает
    на посуточные части сам.
    """

    date_from: date
    hour_from: int = Field(ge=0, le=23)
    date_to: date
    hour_to: int = Field(ge=1, le=24)
    campaign_ids: list[str] = Field(min_length=1, max_length=50)
    offer_id: uuid.UUID
    buyer_id: uuid.UUID
    provider_id: uuid.UUID

    @model_validator(mode="after")
    def validate_window(self) -> "MetaSpendCommitIn":
        try:
            meta_spend_validate_window(
                self.date_from, self.hour_from, self.date_to, self.hour_to
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class MetaRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    account_id: uuid.UUID | None = None
    launch_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    level: Literal["campaign", "adset", "ad"] | None = None
    scope_kind: Literal["cabinet", "campaign"] | None = None
    campaign_external_id: str | None = Field(default=None, max_length=100)
    entity_status: Literal["active", "paused", "any"] | None = None
    window: Literal[
        "today", "yesterday", "last_2d", "last_3d", "last_7d",
        "last_14d", "last_28d", "last_30d", "month"
    ] | None = None
    schedule_kind: Literal["always", "daily_midnight", "custom"] | None = None
    schedule: dict | None = None
    convert_currency: bool | None = None
    currency: str | None = Field(default=None, max_length=8)
    conditions: list[MetaRuleCondition] | None = Field(default=None, max_length=10)
    frequency_minutes: int | None = Field(default=None, ge=15, le=1440)
    min_spend: Decimal | None = Field(default=None, ge=0)
    action: str | None = Field(default=None, max_length=30)
    action_value: Decimal | None = Field(default=None, ge=0, le=100000)
    action_sign: Literal["plus", "minus"] | None = None
    action_mode: Literal["pct", "sum"] | None = None
    action_max: Decimal | None = Field(default=None, ge=0)
    budget_kind: Literal["daily", "lifetime"] | None = None
    is_enabled: bool | None = None
    cooldown_minutes: int | None = Field(default=None, ge=0, le=10080)


class MetaCommentFetch(BaseModel):
    """Задание на загрузку комментариев по выбранным постам."""

    account_id: uuid.UUID
    # Пусто — берём все посты кабинета, у которых есть активные объявления:
    # чистят обычно то, что крутится прямо сейчас.
    posts: list[str] = Field(default_factory=list, max_length=200)
    active_only: bool = True


class MetaCommentAction(BaseModel):
    """Массовое действие над отмеченными комментариями.

    `delete` необратим — Meta не отдаёт удалённый комментарий никогда, поэтому
    интерфейс подтверждает его отдельно, а сюда приходит уже осознанный выбор.
    """

    account_id: uuid.UUID
    action: Literal["hide", "unhide", "delete"]
    comments: list[str] = Field(min_length=1, max_length=500)


class MetaRuleGroupCreate(BaseModel):
    """Группа правил: имя и, опционально, правила, входящие в неё."""

    name: str = Field(min_length=1, max_length=160)
    rule_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class MetaRuleGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    rule_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)


def normalize_color(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", clean):
        raise ValueError("Цвет задаётся в виде #RRGGBB")
    return clean.upper()


class TaskStatusCreate(BaseModel):
    """Пользовательская колонка канбана — ТЗ 8.1."""

    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#9B9292", max_length=16)
    is_terminal: bool = False
    position: int | None = Field(default=None, ge=0, le=100)

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        return normalize_color(value)


class TaskStatusUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, max_length=16)
    is_terminal: bool | None = None
    position: int | None = Field(default=None, ge=0, le=100)

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        return normalize_color(value)


TaskFieldKind = Literal[
    "text",
    "textarea",
    "number",
    "money",
    "date",
    "select",
    "labels",
    "checkbox",
    "user",
    "url",
    "file",
]
# Типы со списком вариантов. Для остальных `options` игнорируется.
OPTION_FIELD_KINDS = {"select", "labels"}
CURRENCIES = {"USD", "EUR", "RUB", "KZT", "UAH", "GBP", "TRY", "BRL"}


def normalize_field_config(kind: str, config: dict | None) -> dict:
    """Оставить только те настройки, которые для этого типа что-то значат.

    Хранить чужие ключи нельзя: тип поля не меняется после создания, и мусор в
    настройках пережил бы любое редактирование, а на экране выглядел бы как
    настоящая настройка.
    """
    source = config or {}
    if kind == "money":
        currency = str(source.get("currency") or "USD").upper()
        if currency not in CURRENCIES:
            raise ValueError(f"Валюта {currency} не поддерживается")
        return {"currency": currency}
    if kind == "number":
        try:
            precision = int(source.get("precision", 0))
        except (TypeError, ValueError):
            raise ValueError("Число знаков после запятой должно быть числом") from None
        if not 0 <= precision <= 4:
            raise ValueError("Число знаков после запятой — от 0 до 4")
        return {"precision": precision}
    if kind == "file":
        try:
            limit = int(source.get("max_files", 10))
        except (TypeError, ValueError):
            raise ValueError("Лимит файлов должен быть числом") from None
        if not 1 <= limit <= 20:
            raise ValueError("Лимит файлов — от 1 до 20")
        return {"max_files": limit}
    return {}


class TaskFieldCreate(BaseModel):
    """Пользовательское поле задачи — ТЗ 8.1."""

    name: str = Field(min_length=1, max_length=120)
    kind: TaskFieldKind = "text"
    options: list[str] = Field(default_factory=list, max_length=50)
    config: dict = Field(default_factory=dict)
    is_required: bool = False
    show_always: bool = True
    position: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_options(self) -> "TaskFieldCreate":
        if self.kind in OPTION_FIELD_KINDS and not self.options:
            raise ValueError("У списка значений должен быть хотя бы один вариант")
        if self.kind not in OPTION_FIELD_KINDS:
            self.options = []
        else:
            self.options = _unique_options(self.options)
        self.config = normalize_field_config(self.kind, self.config)
        return self


class TaskFieldUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    options: list[str] | None = Field(default=None, max_length=50)
    config: dict | None = None
    is_required: bool | None = None
    show_always: bool | None = None
    position: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def clean_options(self) -> "TaskFieldUpdate":
        if self.options is not None:
            self.options = _unique_options(self.options)
            if not self.options:
                raise ValueError("У списка значений должен быть хотя бы один вариант")
        return self


def _unique_options(values: list[str]) -> list[str]:
    """Варианты без пустых и без повторов, с сохранением порядка.

    Повтор здесь не безобиден: значение хранится строкой, и два одинаковых
    варианта в выпадающем списке невозможно различить при чтении карточки.
    """
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()[:120]
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        result.append(text)
    return result


class TaskSectionCreate(BaseModel):
    """Раздел доски задач — отдел со своими карточками и своими правами."""

    title: str = Field(min_length=1, max_length=120)
    position: int | None = Field(default=None, ge=0, le=100)


class TaskSectionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    position: int | None = Field(default=None, ge=0, le=100)
    # Шаблон по умолчанию этого раздела; `null` снимает его.
    default_template_id: uuid.UUID | None = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    section_id: uuid.UUID | None = None
    status_id: uuid.UUID | None = None
    priority: TaskPriority = TaskPriority.medium
    start_date: date | None = None
    due_date: date | None = None
    assignee_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    custom_values: dict = Field(default_factory=dict)
    field_ids: list[uuid.UUID] = Field(default_factory=list, max_length=40)
    template_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    section_id: uuid.UUID | None = None
    status_id: uuid.UUID | None = None
    priority: TaskPriority | None = None
    start_date: date | None = None
    due_date: date | None = None
    assignee_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    custom_values: dict | None = None
    field_ids: list[uuid.UUID] | None = Field(default=None, max_length=40)
    template_id: uuid.UUID | None = None


class TaskMove(BaseModel):
    """Перетаскивание карточки — ТЗ 8.1.

    `position` это индекс в целевой колонке, а не абсолютный порядок: клиент
    знает, между какими карточками бросили, но не знает их номеров.
    """

    status_id: uuid.UUID
    position: int = Field(default=0, ge=0)


class TaskTemplateCreate(BaseModel):
    """Шаблон задачи — набор полей и значения по умолчанию для них.

    Стандартные поля карточки шаблон не задаёт: название, приоритет, колонку,
    срок, описание и исполнителей всё равно выбирают под конкретную задачу.
    """

    name: str = Field(min_length=1, max_length=160)
    custom_values: dict = Field(default_factory=dict)
    field_ids: list[uuid.UUID] = Field(default_factory=list, max_length=40)
    is_default: bool = False


class TaskTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    custom_values: dict | None = None
    field_ids: list[uuid.UUID] | None = Field(default=None, max_length=40)
    is_default: bool | None = None


class KnowledgeSectionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    icon: str | None = Field(default=None, max_length=16)


class KnowledgeSectionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    icon: str | None = Field(default=None, max_length=16)
    position: int | None = Field(default=None, ge=0, le=1000)


class KnowledgeArticleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    section_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    blocks: list = Field(default_factory=list)
    status: ArticleStatus = ArticleStatus.draft


class KnowledgeArticleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    section_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    blocks: list | None = None
    status: ArticleStatus | None = None
    position: int | None = Field(default=None, ge=0, le=10000)


class SectionAccessRule(BaseModel):
    """Правило доступа к разделу — либо для роли, либо для человека.

    Одинаково описывает раздел базы знаний и раздел доски задач: права там
    называются и ведут себя одинаково, различается только умолчание для раздела
    без правил.
    """

    role_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    can_view: bool = True
    can_create: bool = False
    can_edit: bool = False
    can_delete: bool = False
    can_manage: bool = False

    @model_validator(mode="after")
    def validate_rights(self) -> "SectionAccessRule":
        # Правило про роль и человека сразу неоднозначно: непонятно, что делать,
        # когда роль закрывает раздел, а имя открывает.
        if (self.role_id is None) == (self.user_id is None):
            raise ValueError("Правило задаётся либо для роли, либо для пользователя")
        # Право без просмотра бессмысленно: редактировать невидимое нельзя.
        if not self.can_view and any(
            (self.can_create, self.can_edit, self.can_delete, self.can_manage)
        ):
            raise ValueError("Права выдаются только вместе с просмотром раздела")
        return self


class KnowledgeAccessUpdate(BaseModel):
    rules: list[SectionAccessRule] = Field(default_factory=list, max_length=100)


class TaskSectionAccessUpdate(BaseModel):
    rules: list[SectionAccessRule] = Field(default_factory=list, max_length=100)


class TelegramBotIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class AlertChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # chat_id приходит строкой: у супергрупп он отрицательный и длиннее, чем
    # помещается в int без потери точности в JavaScript.
    chat_id: str = Field(min_length=1, max_length=64)
    thread_id: str | None = Field(default=None, max_length=32)
    status: Status = Status.active


class AlertCondition(BaseModel):
    """Одно условие: поле, оператор и то, с чем сравниваем.

    `extra="forbid"` здесь не придирчивость: условие и группа различаются
    только набором ключей, и без запрета лишних полей условие спокойно прошло
    бы валидацию как пустая группа.
    """

    model_config = ConfigDict(extra="forbid")

    field: Literal["campaign_group", "offer"]
    operator: Literal["in", "not_in", "contains", "not_contains"] = "in"
    values: list[str] = Field(default_factory=list, max_length=200)
    text: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_value(self) -> "AlertCondition":
        if self.operator in {"in", "not_in"}:
            if not self.values:
                raise ValueError("Выберите хотя бы одно значение")
        elif not self.text.strip():
            raise ValueError("Укажите, что должно содержаться")
        return self


class AlertConditionGroup(BaseModel):
    """Узел дерева условий. Вложенность — ровно один уровень."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["and", "or"] = "and"
    items: list["AlertCondition | AlertConditionGroup"] = Field(
        default_factory=list, max_length=20
    )

    @model_validator(mode="after")
    def validate_depth(self) -> "AlertConditionGroup":
        for item in self.items:
            if isinstance(item, AlertConditionGroup):
                if any(isinstance(nested, AlertConditionGroup) for nested in item.items):
                    raise ValueError("Группа внутри группы — только один уровень")
                if not item.items:
                    raise ValueError("Пустая группа условий ничего не значит")
        return self


AlertConditionGroup.model_rebuild()


AlertWindow = Literal[
    "today", "yesterday", "last_3d", "last_7d", "last_14d", "last_30d",
    "this_week", "last_week", "month", "last_month",
]


class AlertRuleIn(BaseModel):
    """Уведомление в Telegram — ТЗ 9.1.

    Два вида и ничего между ними: сообщение на каждый депозит либо сводка по
    расписанию. Универсального конструктора условий здесь нет намеренно — он
    позволял собрать что угодно, а команде нужны ровно эти два сценария.
    """

    name: str = Field(min_length=1, max_length=160)
    status: Status = Status.active
    kind: Literal["deposit", "report"] = "deposit"
    channel_id: uuid.UUID
    thread_id: str | None = Field(default=None, max_length=32)
    # Условия депозитного уведомления: дерево И/ИЛИ по двум полям — группе
    # кампаний и офферу. Пустое дерево означает «любые депозиты»: это самый
    # частый случай, а не забытая настройка.
    conditions: AlertConditionGroup = Field(default_factory=AlertConditionGroup)
    # Отчёт: период сводки и когда её слать.
    window: AlertWindow = "today"
    schedule: str = Field(default="daily_09", min_length=1, max_length=24)
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=64)
    message_template: str | None = Field(default=None, max_length=2000)

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, value: str) -> str:
        presets = {
            "every_15", "every_30", "hourly", "every_4h",
            "daily_09", "daily_18", "twice", "workdays_10",
        }
        if value in presets or re.fullmatch(r"daily_(?:[01]\d|2[0-3])[0-5]\d", value):
            return value
        raise ValueError("Некорректное расписание")


class AlertTestIn(AlertRuleIn):
    """То же правило, но для пробной отправки — оно ещё не сохранено."""


class CapRuleIn(BaseModel):
    """Капа на связку офферов.

    Офферов может быть несколько — их показатели складываются: партнёрка обычно
    выдаёт общий лимит на связку, а не на каждый оффер по отдельности.
    """

    name: str = Field(min_length=1, max_length=160)
    status: Status = Status.active
    # Канал остался для совместимости: старый клиент шлёт одно поле, новый —
    # список. Валидатор сводит их в `channel_ids`, а `channel_id` держит первый.
    channel_id: uuid.UUID | None = None
    channel_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    thread_id: str | None = Field(default=None, max_length=32)
    offer_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    user_id: uuid.UUID | None = None
    metric: Literal["sales", "leads", "installs", "spend"] = "sales"
    limit_value: Decimal = Field(gt=0, le=Decimal("100000000"))
    period: Literal["day", "week", "month", "total"] = "day"
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=64)
    notify_at: list[int] = Field(default_factory=lambda: [100], max_length=10)

    @model_validator(mode="after")
    def validate_cap(self) -> "CapRuleIn":
        channels = list(dict.fromkeys(
            ([self.channel_id] if self.channel_id else []) + list(self.channel_ids)
        ))
        if not channels:
            raise ValueError("Выберите хотя бы один канал")
        self.channel_ids = channels
        self.channel_id = channels[0]
        if not self.offer_ids and self.user_id is None:
            raise ValueError("Выберите офферы или пользователя, на кого ставится CAP")
        for percent in self.notify_at:
            if not 1 <= percent <= 1000:
                raise ValueError("Порог уведомления — от 1 до 1000 %")
        return self


class SalaryTier(BaseModel):
    """Уровень сетки: потолок суммы и ставка. Пустой потолок — «и выше»."""

    up_to: Decimal | None = None
    percent: Decimal = Field(ge=-1000, le=1000)


class SalaryComponentIn(BaseModel):
    kind: Literal["percent", "fixed", "grid", "deduction"]
    base: str | None = Field(default=None, max_length=40)
    percent: Decimal | None = Field(default=None, ge=-1000, le=1000)
    amount: Decimal | None = Field(default=None, ge=-1000000000, le=1000000000)
    tiers: list[SalaryTier] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_shape(self) -> "SalaryComponentIn":
        # Каждый тип требует своего набора полей. Молча принять компонент без
        # базы значило бы завести правило, которое всегда считает ноль.
        if self.kind == "percent":
            if not self.base:
                raise ValueError("У процента должна быть база")
            if self.percent is None:
                raise ValueError("Укажите процент")
        elif self.kind == "grid":
            if not self.base:
                raise ValueError("У сетки должна быть база")
            if not self.tiers:
                raise ValueError("Добавьте хотя бы один уровень сетки")
        elif self.kind == "fixed":
            if self.amount is None:
                raise ValueError("Укажите сумму")
        elif self.kind == "deduction":
            if self.base and self.percent is None:
                raise ValueError("Укажите процент вычета")
            if not self.base and self.amount is None:
                raise ValueError("Укажите сумму вычета")
        return self


class SalaryRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    status: Status = Status.active
    mode: Literal["replace", "add"] = "replace"
    scope: Literal["role", "user"] = "role"
    role_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    components: list[SalaryComponentIn] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_rule(self) -> "SalaryRuleCreate":
        if self.scope == "role" and self.role_id is None:
            raise ValueError("Выберите роль")
        if self.scope == "user" and self.user_id is None:
            raise ValueError("Выберите пользователя")
        if self.scope != "role":
            self.role_id = None
        if self.scope != "user":
            self.user_id = None
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("Дата начала позже даты окончания")
        if not self.components:
            raise ValueError("Добавьте хотя бы один компонент формулы")
        return self


class SalaryRuleUpdate(SalaryRuleCreate):
    """Правило заменяется целиком: частичная правка формулы из компонентов
    потребовала бы отдельных идентификаторов строк, а править их по одной
    незачем — формулу пересобирают."""


class OfferOut(ORMModel):
    id: uuid.UUID
    external_id: str | None
    name: str
    geo: str | None
    cap: str | None
    cpa: Decimal
    cpa_currency: str
    kpi: str | None
    comment: str | None
    group_name: str | None
    status: OfferStatus
    keitaro_state: Status
    is_starred: bool
    # Когда строка появилась в CRM. Нужна разделу «Оффера»: там сортируют от
    # нового к старому, а в таблице этой колонки нет.
    created_at: datetime


class OfferIn(BaseModel):
    """Оффер, заведённый руками в разделе «Оффера».

    Статус сюда не входит: его определяет назначение — «Не занят», пока
    оффер ничей, «Активен» у тимлида, «В работе» когда тимлид отдал его
    баерам. Холд и Стоп ставятся отдельной ручкой.
    """

    name: str = Field(min_length=1, max_length=240)
    # ID оффера в партнёрском сервисе: по нему депозиты из ПП находят оффер
    # и раскладываются в книгу байера автоматически.
    external_id: str | None = Field(default=None, max_length=100)
    geo: str | None = Field(default=None, max_length=64)
    cap: str | None = Field(default=None, max_length=160)
    # Ставка партнёрки: с ней оффер уезжает в книгу баера готовым.
    cpa: Decimal = Field(default=Decimal("0"), ge=0)
    cpa_currency: Literal["USD", "EUR"] = "USD"
    # KPI и комментарий читают, открыв оффер, а не в таблице — поэтому длина
    # человеческая, а не «влезет в колонку».
    kpi: str | None = Field(default=None, max_length=4000)
    comment: str | None = Field(default=None, max_length=4000)
    partner_id: uuid.UUID | None = None
    # Через какую интеграцию с ПП приходят депозиты по этому офферу.
    partner_integration_id: uuid.UUID | None = None
    lead_ids: list[uuid.UUID] = Field(default_factory=list)
    # Капа каждого тимлида: общий лимит оффера они делят между собой, поэтому
    # цифра спрашивается там же, где отмечают самих тимлидов. Ключа нет —
    # капа этого тимлида остаётся прежней, пустая строка её снимает.
    caps: dict[uuid.UUID, str] = Field(default_factory=dict)
    buyer_ids: list[uuid.UUID] = Field(default_factory=list)


class PartnerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AssignBuyers(BaseModel):
    buyer_ids: list[uuid.UUID]


class AssignLeads(BaseModel):
    """Тимлиды оффера и капа каждого из них.

    Капа приходит отдельным словарём, а не рядом с id: списком тимлидов
    пользуются и форма оффера, и эта ручка, а цифру спрашивают только здесь.
    Лишние ключи (тимлид, которого сняли) просто игнорируются.
    """

    lead_ids: list[uuid.UUID]
    caps: dict[uuid.UUID, str] = Field(default_factory=dict)


class CatalogStatusUpdate(BaseModel):
    status: Status


class OfferStatusUpdate(BaseModel):
    status: OfferStatus


class OfferStarUpdate(BaseModel):
    is_starred: bool


class MediaRecordIn(BaseModel):
    """Manual media input.

    `spend_calculated` is intentionally absent: it is owned by the
    "Агенты и платёжки" block and is only ever written by
    `PUT /media-records/{id}/values`.
    """

    record_date: date
    buyer_id: uuid.UUID
    offer_id: uuid.UUID
    installs: int | None = None
    registrations: int | None = None
    ftd: int | None = None
    revenue: Decimal | None = None
    spend_override: Decimal | None = None


MEDIA_MANUAL_FIELDS = ("installs", "registrations", "ftd", "revenue", "spend_override")


class MediaServiceValueIn(BaseModel):
    service_id: uuid.UUID
    quantity: Decimal = Decimal("0")
    manual_cost_override: Decimal | None = None


class MediaSpendValueIn(BaseModel):
    provider_id: uuid.UUID
    base_amount: Decimal = Decimal("0")
    manual_amount_override: Decimal | None = None


class MediaValuesIn(BaseModel):
    """Replaces the blocks it carries and leaves the others untouched.

    An omitted block (`None`) is not the same as an empty one: the Медиаборд modal
    edits agents/payments only, so it must not delete the service values it no
    longer shows, while an explicit `[]` still clears a block.
    """

    services: list[MediaServiceValueIn] | None = None
    spend_providers: list[MediaSpendValueIn] | None = None


class MediaDaySpendIn(BaseModel):
    """Расход за день без разбивки по офферам.

    Баер тратит на день, а не на оффер: в кабинете стоит общий бюджет, и
    раскладывать его по офферам он не может. Поэтому сумма приходит на пару
    «день — баер», а сервер делит её поровну между офферами этого дня.

    Ровно одно из двух: `spend` — итог дня одной цифрой (ручная фиксация),
    `providers` — тот же итог, но разложенный по агентам и платёжкам.
    """

    record_date: date
    buyer_id: uuid.UUID
    # Тир, к которому относится расход: «T1», «T23» или пусто — весь день.
    # Книга в финансах своя на каждый тир, и расход дня, размазанный по обоим,
    # приезжал бы туда неправильно: баер знает, где потратил.
    tier: Literal["T1", "T23"] | None = None
    spend: Decimal | None = None
    providers: list[MediaSpendValueIn] | None = None


class FinanceRecordIn(BaseModel):
    record_date: date
    buyer_id: uuid.UUID
    offer_id: uuid.UUID
    link: str | None = None
    rent: Decimal = Decimal("0")
    spend: Decimal = Decimal("0")
    spend_override: Decimal | None = None
    qual: Decimal = Decimal("0")
    revenue: Decimal = Decimal("0")
    salary: Decimal = Decimal("0")
    source: str = "manual"


class FinanceServiceValueIn(BaseModel):
    service_id: uuid.UUID
    quantity: Decimal = Decimal("0")
    manual_cost_override: Decimal | None = None


class FinanceSpendValueIn(BaseModel):
    provider_id: uuid.UUID
    base_amount: Decimal = Decimal("0")
    manual_amount_override: Decimal | None = None


class FinanceValuesIn(BaseModel):
    services: list[FinanceServiceValueIn] = Field(default_factory=list)
    spend_providers: list[FinanceSpendValueIn] = Field(default_factory=list)
    qual: Decimal | None = None
    spend_override: Decimal | None = None


class FinanceDayIn(BaseModel):
    """Автоматический спенд рассчитывает сервер, manual_spend явно заменяет его."""

    spend_buyer: Decimal = Decimal("0")
    manual_spend: Decimal | None = None
    spend_agent: Decimal = Decimal("0")
    costs: Decimal = Decimal("0")


class FinanceOfferTagIn(BaseModel):
    """Строка под оффером: имя тега и депозиты по дням.

    SOK — обычный тег с таким именем, а не отдельное поле: его переименовывают
    и заводят рядом другие.
    """

    # Пустое имя — нормальное состояние: тег только что создали и ещё не назвали.
    # Подставлять за пользователя «SOK» нельзя, имя строки задаёт он сам.
    name: str = Field(default="", max_length=120)
    # День месяца → депозиты. Дни без депозитов просто отсутствуют.
    values: dict[int, Decimal] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: dict[int, Decimal]) -> dict[int, Decimal]:
        return {day: amount for day, amount in value.items() if 1 <= day <= 31}


class CountryTiersIn(BaseModel):
    """Полный список стран Tier1. Всё, чего в нём нет, — Tier2/3."""

    tier1: list[str] = Field(default_factory=list, max_length=300)


class FinanceBookOfferIn(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    partner: str | None = Field(default=None, max_length=160)
    geo: str | None = Field(default=None, max_length=12)
    rate: Decimal = Field(default=Decimal("0"), ge=0)
    # Ставка бывает в евро. Доход всё равно считается в долларах — по курсу книги.
    rate_currency: Literal["USD", "EUR"] = "USD"
    # Ссылка на оффер справочника, если строка приехала из «Офферов».
    source_offer_id: uuid.UUID | None = None
    locked_fields: list[Literal["name", "partner", "geo", "rate", "rate_currency"]] = Field(
        default_factory=list, max_length=5
    )
    # Одноразовая команда замка: при открытии сразу вернуть справочные значения.
    sync_from_catalog: bool = False
    tags: list[FinanceOfferTagIn] = Field(default_factory=list)


class FinanceBookIn(BaseModel):
    buyer_id: uuid.UUID
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    # Таблица тира: у баера их две, и каждая сохраняется отдельно.
    tier: Literal["T1", "T23"] = "T1"
    # Курс евро к доллару для этого месяца. Ноль и отрицательный курс не бывают,
    # а верхняя граница отсекает опечатку вроде «1085» вместо «1.085».
    eur_usd_rate: Decimal = Field(default=Decimal("1"), gt=0, le=1000)
    # Оставлено для совместимости со старым фронтендом. Сервер не доверяет
    # этому значению и рассчитывает перенос по предыдущим книгам самостоятельно.
    prev_minus: Decimal = Field(default=Decimal("0"), ge=0)
    days: dict[int, FinanceDayIn] = Field(default_factory=dict)
    offers: list[FinanceBookOfferIn] = Field(default_factory=list)

    @field_validator("days")
    @classmethod
    def validate_days(cls, value: dict[int, FinanceDayIn]) -> dict[int, FinanceDayIn]:
        return {day: entry for day, entry in value.items() if 1 <= day <= 31}


class FinancePartnersTagIn(BaseModel):
    """Одна строка сводки «Партнёрки»: чей оффер, какой тег и его депозиты.

    Оффер приходит либо строкой книги (`book_offer_id`), либо справочным
    (`source_offer_id`) — во втором случае строка в книге баера ещё не заведена
    и создаётся при первом же введённом числе.
    """

    buyer_id: uuid.UUID
    book_offer_id: uuid.UUID | None = None
    source_offer_id: uuid.UUID | None = None
    tag_id: uuid.UUID | None = None
    name: str = Field(default="", max_length=120)
    values: dict[int, Decimal] = Field(default_factory=dict)
    # Удалить тег вместе с его депозитами за месяц.
    drop: bool = False


class FinancePartnersIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    tags: list[FinancePartnersTagIn] = Field(default_factory=list, max_length=500)


class PreferenceIn(BaseModel):
    value: dict = Field(default_factory=dict)


class Page(BaseModel):
    items: list
    total: int
    limit: int
    offset: int


class DashboardSummary(BaseModel):
    leads: int
    sales: int
    epl: Decimal | None
    revenue: Decimal
    spend: Decimal
    profit: Decimal
    roi: Decimal | None
    working_offers: list[dict] = Field(default_factory=list)
    working_offers_total: int = 0
    series: list[dict] = Field(default_factory=list)


class APIError(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
