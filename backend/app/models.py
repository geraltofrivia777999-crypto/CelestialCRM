import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Status(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    blocked = "blocked"


class OfferStatus(str, enum.Enum):
    """Где оффер находится в нашем процессе, а не в Keitaro.

    Состояние самого Keitaro живёт в `Offer.keitaro_state`; эти значения
    принадлежат команде и меняются только из CRM.

    Три из них ставит сам процесс назначения: «Не занят» — оффер только
    заведён, «Активен» — он у тимлида, «В работе» — тимлид раздал его баерам.
    «Холд» и «Стоп» ставит человек, и назначение их не перебивает.
    """

    active = "active"
    working = "working"
    hold = "hold"
    stop = "stop"
    free = "free"


class LaunchStatus(str, enum.Enum):
    """Где залив находится в нашем процессе, а не в Meta.

    `paused` и `active` отражают состояние кампании в кабинете и обновляются
    синхронизацией; остальные значения меняются только из CRM.
    """

    draft = "draft"
    publishing = "publishing"
    paused = "paused"
    active = "active"
    stopped = "stopped"
    failed = "failed"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ArticleStatus(str, enum.Enum):
    """Черновик виден только автору и тем, кто может редактировать раздел."""

    draft = "draft"
    published = "published"
    archived = "archived"


class ProviderType(str, enum.Enum):
    agent = "agent"
    payment = "payment"


class SyncStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"


class Workspace(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class Permission(UUIDMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class Role(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_role_workspace_name"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    # Чьи данные видит роль: "all" — весь воркспейс, "team" — себя и своих
    # подчинённых, "own" — только свои. Права отвечают за разделы, а это — за
    # строки внутри них: тимлид и баер открывают Медиаборд одинаково, но видят
    # в нём разное.
    data_scope: Mapped[str] = mapped_column(
        String(10), default="team", server_default="team", nullable=False
    )
    # Сводки «Общая», «Tier1», «Tier2/3» в Финансах. Отдельно от области
    # доступа: тимлиду своя команда нужна, а общий срез по воркспейсу — не всем.
    show_finance_summaries: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions", lazy="selectin"
    )


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("workspace_id", "login", name="uq_user_workspace_login"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    login: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active, index=True)
    keitaro_company_group: Mapped[str | None] = mapped_column(String(160))
    keitaro_offer_group: Mapped[str | None] = mapped_column(String(160))
    # Теги для Финансов: каждый становится строкой под оффером, когда оффер
    # назначают этому человеку. Первый — группа офферов Keitaro.
    finance_tags: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    team_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[Role] = relationship(lazy="selectin")


class UserParent(Base):
    __tablename__ = "user_parents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    parent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )


class Session(UUIDMixin, Base):
    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Service(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_service_workspace_name"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    install_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    commission_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=0)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)


class SpendProvider(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "spend_providers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_provider_workspace_name"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[ProviderType] = mapped_column(Enum(ProviderType))
    commission_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=0)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)


class IntegrationConnection(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_connection_workspace_name"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # Владелец нужен прежде всего Meta Ads: у каждого баера свои токены и
    # кабинеты, тимлид видит подключения своей команды, администратор — все.
    # Поле nullable только для старых подключений, созданных до появления
    # персональной области видимости; такие строки доступны администратору.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="keitaro")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)
    sync_interval_minutes: Mapped[int] = mapped_column(default=15)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    buyer_sub_id: Mapped[int] = mapped_column(default=1)
    lookback_days: Mapped[int] = mapped_column(default=2)
    checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Заполняются только у kind="meta": Business Manager и номер sub_id в Keitaro,
    # в котором лежит ID кампании Meta. Второе и связывает расход из кабинета
    # с доходом из трекера.
    external_account_id: Mapped[str | None] = mapped_column(String(100))
    attribution_sub_id: Mapped[int | None] = mapped_column()
    # Чем именно выпущен токен Meta. Само подключение от этого не меняется —
    # Graph API любой токен принимает одинаково, — но живут они по-разному, и
    # когда синхронизация встанет, ответ «почему» зависит от способа.
    auth_method: Mapped[str] = mapped_column(
        String(20), default="system_user", server_default="system_user", nullable=False
    )
    # Через какой адрес ходить в Meta. Нужен там, где кабинеты живут за своим
    # прокси: запрос из другой сети она к ним просто не пустит.
    proxy_url: Mapped[str | None] = mapped_column(String(500))
    # Свой User-Agent. Пусто — уходит httpx-овский по умолчанию.
    user_agent: Mapped[str | None] = mapped_column(String(500))


class SyncRun(UUIDMixin, Base):
    __tablename__ = "sync_runs"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus), default=SyncStatus.queued)
    mode: Mapped[str] = mapped_column(String(30), default="incremental")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_pct: Mapped[int] = mapped_column(default=0)
    rows_processed: Mapped[int] = mapped_column(default=0)
    # Признак жизни прогона: его двигает сам прогон на каждом шаге. Прогон,
    # убитый перезапуском контейнера, перестаёт его двигать и освобождается
    # через минуты, а не висит «running» до общего потолка в два часа, держа
    # расписание подключения.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class KeitaroStatDaily(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "keitaro_stats_daily"
    __table_args__ = (
        UniqueConstraint("connection_id", "dimension_key", name="uq_keitaro_stat_dimension"),
        Index("ix_keitaro_stats_date", "workspace_id", "record_date"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    offer_external_id: Mapped[str | None] = mapped_column(String(100))
    campaign_external_id: Mapped[str | None] = mapped_column(String(100))
    country_code: Mapped[str | None] = mapped_column(String(12))
    dimension_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sub_values: Mapped[dict] = mapped_column(JSON, default=dict)
    clicks: Mapped[int] = mapped_column(default=0)
    unique_clicks: Mapped[int] = mapped_column(default=0)
    conversions: Mapped[int] = mapped_column(default=0)
    registrations: Mapped[int] = mapped_column(default=0)
    leads: Mapped[int] = mapped_column(default=0)
    sales: Mapped[int] = mapped_column(default=0)
    rejected: Mapped[int] = mapped_column(default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    revenue: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)


class KeitaroConversion(UUIDMixin, Base):
    """Одна конверсия из журнала Keitaro — ТЗ 9.1.

    Дневной отчёт для уведомления о депозите не годится: он знает «три продажи
    за день по этой кампании», а сообщение должно содержать конкретный депозит —
    его время клика и его sub_id. Поэтому журнал конверсий тянется отдельно.

    `seen_at` — когда строку увидели мы, а не когда она случилась в трекере.
    По нему правило и понимает, что нового: конверсия может приехать с
    задержкой и с временем в прошлом, и курсор по её собственному времени
    молча пропустил бы её.
    """

    __tablename__ = "keitaro_conversions"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_keitaro_conversion"),
        Index("ix_keitaro_conversions_seen", "workspace_id", "seen_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    # lead | sale | rejected — как их называет сам трекер.
    status: Mapped[str] = mapped_column(String(30), default="", index=True)
    conversion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    click_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    campaign_external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    campaign_name: Mapped[str | None] = mapped_column(String(300))
    campaign_group_id: Mapped[str | None] = mapped_column(String(100), index=True)
    # Имя группы хранится рядом с её id: условие «содержит» сравнивает текст,
    # а тянуть его из справочника на каждую конверсию — лишний запрос ради
    # строки, которая на момент конверсии уже была известна.
    campaign_group_name: Mapped[str | None] = mapped_column(String(300))
    offer_external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    offer_name: Mapped[str | None] = mapped_column(String(300))
    country_code: Mapped[str | None] = mapped_column(String(12))
    revenue: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    payout: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    # sub_id_1 … sub_id_10 одним полем: их десять, колонками они бы только
    # раздули таблицу, а читаются они целиком и только в макросах сообщения.
    sub_values: Mapped[dict] = mapped_column(JSON, default=dict)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KeitaroGroup(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "keitaro_groups"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "resource_type",
            "external_id",
            name="uq_keitaro_group_external",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    resource_type: Mapped[str] = mapped_column(String(30), index=True)
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(default=0)


class KeitaroCampaign(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "keitaro_campaigns"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_keitaro_campaign_external"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    group_external_id: Mapped[str | None] = mapped_column(String(100))
    group_name: Mapped[str | None] = mapped_column(String(200))
    traffic_source_external_id: Mapped[str | None] = mapped_column(String(100))
    cost_type: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active, index=True)
    external_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MetaSocialAccount(UUIDMixin, TimestampMixin, Base):
    """Социальный аккаунт Facebook — верхний уровень обзора.

    Это владелец токена: на нём заводят бизнес-менеджеры и держат фан-пейджи.
    Один аккаунт на подключение — Graph API отдаёт ровно того, кому принадлежит
    токен, и разных владельцев у одного токена не бывает.
    """

    __tablename__ = "meta_social_accounts"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_meta_social_external"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    # Ответственный со стороны CRM — назначается руками, синхронизация его не трогает.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active, index=True)
    external_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MetaBusiness(UUIDMixin, TimestampMixin, Base):
    """Бизнес-менеджер. Заводится на социальном аккаунте, держит кабинеты и ФП."""

    __tablename__ = "meta_businesses"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_meta_business_external"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_social_accounts.id", ondelete="SET NULL"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    verification_status: Mapped[str | None] = mapped_column(String(60))
    external_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MetaFanPage(UUIDMixin, TimestampMixin, Base):
    """Фан-пейдж — от его лица откручивается объявление.

    Висит либо прямо на социальном аккаунте, либо на бизнес-менеджере: Meta
    допускает оба варианта, поэтому обе ссылки необязательные.
    """

    __tablename__ = "meta_fan_pages"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_meta_page_external"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_social_accounts.id", ondelete="SET NULL"), index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_businesses.id", ondelete="SET NULL"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    external_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MetaAdAccount(UUIDMixin, TimestampMixin, Base):
    """Рекламный кабинет Meta — ТЗ 3.2.

    Всё, кроме `owner_id` и `status`, приходит из Graph API и перезаписывается
    каждой синхронизацией. Эти два поля принадлежат CRM: ответственный за кабинет
    назначается руками, а `status` позволяет убрать кабинет с глаз, не трогая Meta.
    """

    __tablename__ = "meta_ad_accounts"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_meta_account_external"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    # Кабинет живёт либо на БМе, либо прямо на социальном аккаунте («личный»).
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_businesses.id", ondelete="SET NULL"), index=True
    )
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_social_accounts.id", ondelete="SET NULL"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    account_status: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    timezone_name: Mapped[str | None] = mapped_column(String(64))
    spend_cap: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    amount_spent: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active, index=True)
    external_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MetaEntity(UUIDMixin, TimestampMixin, Base):
    """Кампания, группа объявлений или объявление — три уровня в одной таблице.

    Разносить их по трём таблицам смысла нет: поля совпадают, а связь всегда идёт
    по `parent_external_id`, как в самом Graph API.
    """

    __tablename__ = "meta_entities"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_meta_entity_external"),
        Index("ix_meta_entities_level", "workspace_id", "level"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100))
    parent_external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    # Только у объявления: фан-пейдж, от лица которого оно откручивается.
    # Уровень ФП в обзоре собирается именно по этой колонке.
    page_external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    # Пост, который крутится объявлением, — «<page_id>_<post_id>». Именно под
    # ним живут комментарии, и одному посту может соответствовать несколько
    # объявлений, поэтому чистка идёт по постам, а не по объявлениям.
    post_external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    effective_status: Mapped[str | None] = mapped_column(String(40))
    objective: Mapped[str | None] = mapped_column(String(60))
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    lifetime_budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    external_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MetaSpendCommit(UUIDMixin, Base):
    """Расход кампании за отрезок дня, отнесённый на оффер — ТЗ 2.4.4.

    Баер льёт один оффер с 12:00 до 16:00, потом другой, и расход одной и той же
    кампании делится между ними. Дневная статистика Meta этого не знает: она
    отдаёт сумму за сутки, поэтому окно берётся из почасовой разбивки и
    записывается сюда.

    Таблица нужна не для отчёта, а чтобы фиксация была обратимой и
    неповторимой. Без неё второй клик по той же кнопке молча удваивал бы расход
    в Медиаборде, а ошибочную привязку нельзя было бы снять — только вычитать
    руками из чужой записи.

    Часы полуинтервалом `[hour_from, hour_to)`: «с 12:00 по 16:00» — это часы
    12, 13, 14 и 15. Иначе граничный час попадал бы в оба окна сразу.
    """

    __tablename__ = "meta_spend_commits"
    __table_args__ = (
        Index("ix_meta_commit_window", "workspace_id", "record_date", "campaign_external_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    hour_from: Mapped[int] = mapped_column(nullable=False)
    hour_to: Mapped[int] = mapped_column(nullable=False)
    campaign_external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    campaign_name: Mapped[str | None] = mapped_column(String(300))
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_ad_accounts.id", ondelete="CASCADE")
    )
    media_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_records.id", ondelete="CASCADE"), index=True
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spend_providers.id"))
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"))
    # Расход как его отдала Meta, до процента агента: процент может поменяться,
    # и пересчитать запись без исходной суммы было бы не из чего.
    base_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MetaStatDaily(UUIDMixin, TimestampMixin, Base):
    """День × объявление из Meta Insights — ТЗ 3.7.

    Здесь только то, что знает сам кабинет: расход, показы, клики. Лиды, продажи и
    доход приходят из Keitaro и присоединяются по ID кампании в sub_id — Meta про
    выплаты партнёрки ничего не знает.
    """

    __tablename__ = "meta_stats_daily"
    __table_args__ = (
        UniqueConstraint("connection_id", "dimension_key", name="uq_meta_stat_dimension"),
        Index("ix_meta_stats_date", "workspace_id", "record_date"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"), index=True
    )
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    campaign_external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    adset_external_id: Mapped[str | None] = mapped_column(String(100))
    ad_external_id: Mapped[str | None] = mapped_column(String(100))
    country_code: Mapped[str | None] = mapped_column(String(12))
    dimension_key: Mapped[str] = mapped_column(String(64), nullable=False)
    impressions: Mapped[int] = mapped_column(default=0)
    clicks: Mapped[int] = mapped_column(default=0)
    # Клики именно по ссылке. Meta считает их отдельно от общих кликов, куда
    # входят лайки и разворачивание текста, — по ним и меряют трафик.
    link_clicks: Mapped[int] = mapped_column(default=0, server_default="0")
    reach: Mapped[int] = mapped_column(default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    # Пиксельные конверсии кабинета. Полезны, когда пиксель стоит, но деньгами их
    # считать нельзя — в отчёте они идут отдельно от лидов Keitaro.
    pixel_leads: Mapped[int] = mapped_column(default=0)
    pixel_purchases: Mapped[int] = mapped_column(default=0)
    actions: Mapped[dict] = mapped_column(JSON, default=dict)


class MetaTemplate(UUIDMixin, TimestampMixin, Base):
    """Шаблон залива — ТЗ 3.5.

    Хранит ровно то, что потом уходит в Graph API при создании adset: цель,
    таргет, плейсменты, бюджет и оптимизацию. Ничего вычисляемого здесь нет —
    шаблон это заготовка параметров, а не отдельная сущность в Meta.
    """

    __tablename__ = "meta_templates"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "created_by_id", "name", name="uq_meta_template_owner_name"
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    objective: Mapped[str] = mapped_column(String(60), default="OUTCOME_SALES")
    optimization_goal: Mapped[str] = mapped_column(String(60), default="OFFSITE_CONVERSIONS")
    billing_event: Mapped[str] = mapped_column(String(40), default="IMPRESSIONS")
    bid_strategy: Mapped[str] = mapped_column(String(60), default="LOWEST_COST_WITHOUT_CAP")
    # Страны в формате ISO-3166-1 alpha-2 — именно так их ждёт targeting.geo_locations.
    geo: Mapped[list] = mapped_column(JSON, default=list)
    age_min: Mapped[int] = mapped_column(default=18)
    age_max: Mapped[int] = mapped_column(default=65)
    genders: Mapped[list] = mapped_column(JSON, default=list)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    # publisher_platforms и позиции внутри них. Пустой словарь означает
    # автоматические плейсменты — для Meta это лучший вариант по умолчанию.
    placements: Mapped[dict] = mapped_column(JSON, default=dict)
    interests: Mapped[list] = mapped_column(JSON, default=list)
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    lifetime_budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    page_id: Mapped[str | None] = mapped_column(String(60))
    pixel_id: Mapped[str | None] = mapped_column(String(60))
    custom_event_type: Mapped[str | None] = mapped_column(String(60))
    call_to_action: Mapped[str] = mapped_column(String(40), default="LEARN_MORE")
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active, index=True)
    # Остальная связка тремя блоками — campaign, adset, ad. Отдельными колонками
    # это три десятка сквозных полей Graph API, по которым мы никогда не ищем и
    # не считаем; проверяет их схема MetaBundleSettings, а не база.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class MetaCreative(UUIDMixin, TimestampMixin, Base):
    """Изображение или видео, загруженное в кабинет — ТЗ 3.6.

    Файл живёт в Meta, у нас остаётся только ссылка на него: `external_hash`
    для картинок (Meta адресует их хэшем) и `external_id` для видео. Привязка к
    кабинету обязательна: один и тот же файл в другом кабинете — другой хэш.
    """

    __tablename__ = "meta_creatives"
    __table_args__ = (
        Index("ix_meta_creatives_account", "account_id", "kind"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10), default="image")
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(240))
    mime_type: Mapped[str | None] = mapped_column(String(80))
    byte_size: Mapped[int] = mapped_column(default=0)
    external_hash: Mapped[str | None] = mapped_column(String(120), index=True)
    external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    permalink_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active, index=True)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    external_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MetaLaunch(UUIDMixin, TimestampMixin, Base):
    """Залив — ТЗ 3.3 и 3.4.

    Учётная запись команды и одновременно рецепт публикации. ID созданных в Meta
    объектов записываются сюда по мере создания, поэтому повторная публикация не
    задваивает кампанию: этап, у которого ID уже есть, пропускается.
    """

    __tablename__ = "meta_launches"
    __table_args__ = (
        Index("ix_meta_launches_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"), index=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_templates.id", ondelete="SET NULL")
    )
    offer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"))
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partners.id", ondelete="SET NULL")
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    geo: Mapped[str | None] = mapped_column(String(12), index=True)
    daily_budget: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    spend_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    link_url: Mapped[str | None] = mapped_column(Text)
    primary_text: Mapped[str | None] = mapped_column(Text)
    headline: Mapped[str | None] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(String(240))
    call_to_action: Mapped[str] = mapped_column(String(40), default="LEARN_MORE")
    page_id: Mapped[str | None] = mapped_column(String(60))
    pixel_id: Mapped[str | None] = mapped_column(String(60))
    # Строкой, а не типом Postgres: колонка заведена как varchar, и с
    # native-перечислением SQLAlchemy дописывал бы к сравнению приведение к
    # несуществующему типу `launchstatus` — фильтр по статусу падал с ошибкой.
    status: Mapped[LaunchStatus] = mapped_column(
        Enum(LaunchStatus, native_enum=False, length=20),
        default=LaunchStatus.draft,
        index=True,
    )
    # Кампания всегда создаётся на паузе. Этот флаг решает, снимать ли её с паузы
    # сразу после публикации — деньги начинают тратиться именно в этот момент.
    activate_on_publish: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Объявления залива: у каждого свои тексты, свои языки и свои креативы.
    # Список, а не таблица, потому что живёт он ровно один залив и наружу
    # ничем, кроме публикации, не используется. Пусто — старое поведение,
    # по объявлению на креатив.
    ads: Mapped[list] = mapped_column(JSON, default=list)
    # Сколько копий адсета завести в кампании. Копии дают Meta несколько групп
    # на обучение при одном и том же таргете.
    adset_count: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False)
    # Параметры к ссылке (`url_tags`) и то, что видно в объявлении вместо неё.
    url_tags: Mapped[str | None] = mapped_column(Text)
    display_link: Mapped[str | None] = mapped_column(String(240))
    # Кастомный нейминг этого кабинета: шаблон имени кампании поверх шаблона
    # связки. Пусто — имя задаёт связка.
    campaign_name: Mapped[str | None] = mapped_column(String(240))
    # Бенефициар/плательщик DSA-прозрачности; уходит в оба поля адсета.
    beneficiary: Mapped[str | None] = mapped_column(String(255))
    # --- Расширенный режим мастера («как в Dolphin») ---
    # Цель кампании поверх связки; пусто — цель из связки.
    objective: Mapped[str | None] = mapped_column(String(40))
    # Сколько кампаний завести одним заливом (каждая со своими адсетами).
    campaign_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    # Блок «Бюджет и ставка»: уровень (campaign|adset), тип (daily|lifetime),
    # разброс ±10%, лимит адсета и стратегия ставок — поверх связки.
    budget_level: Mapped[str | None] = mapped_column(String(10))
    budget_kind: Mapped[str | None] = mapped_column(String(10))
    budget_randomize: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # На сколько процентов разбрасывать бюджет (±N %). Пусто — ±10 %.
    budget_randomize_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    adset_budget_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    bid_strategy: Mapped[str | None] = mapped_column(String(40))
    # Автоправила, привязанные к этому заливу: их launch_id проставляется,
    # и движок правил считает их только по объектам залива.
    rule_ids: Mapped[list | None] = mapped_column(JSON)
    # Теги (adlabels Meta) для созданных объектов:
    # {"level": "campaign|adset|ad", "names": [...], "mode": "add|remove"}.
    tags: Mapped[dict | None] = mapped_column(JSON)
    # --- Расширенный режим v2 ---
    # Цель кампании, событие пикселя, окно конверсии, вовлечённые просмотры —
    # поверх связки (пусто — из связки).
    custom_event_type: Mapped[str | None] = mapped_column(String(60))
    attribution: Mapped[str | None] = mapped_column(String(20))
    engaged_view: Mapped[str | None] = mapped_column(String(5))
    # Лимит адсета: минимум/максимум бюджета (в USD).
    budget_limit_min: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    budget_limit_max: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    # Запланированное увеличение бюджета:
    # [{"start_at","end_at","kind":"sum|pct","amount","applied"}].
    budget_increases: Mapped[list | None] = mapped_column(JSON)
    # Имя группы автоправил (логика групп появится позже — пока сохраняем).
    rule_group: Mapped[str | None] = mapped_column(String(160))
    # Когда создавать объекты в кабинете. Пусто — сразу; будущее время означает
    # «залив запланирован», и его подхватит планировщик.
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # Когда объявлениям начать крутиться. Это не то же самое, что дата залива:
    # залить ночью и стартовать в полночь понедельника — обычная просьба.
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Что оставить на паузе после публикации. Кампания создаётся на паузе
    # всегда; эти флаги решают, что не снимать с неё при активации.
    pause_campaigns: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    pause_adsets: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    pause_ads: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    campaign_external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    adset_external_id: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    external_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MetaLaunchCreative(UUIDMixin, Base):
    """Креатив в заливе. Одно объявление на креатив — так их видно по отдельности."""

    __tablename__ = "meta_launch_creatives"
    __table_args__ = (
        UniqueConstraint("launch_id", "creative_id", name="uq_meta_launch_creative"),
    )

    launch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_launches.id", ondelete="CASCADE"), index=True
    )
    creative_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_creatives.id", ondelete="CASCADE"), index=True
    )
    ad_external_id: Mapped[str | None] = mapped_column(String(100))
    creative_external_id: Mapped[str | None] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(default=0)


class MetaOperation(UUIDMixin, Base):
    """Журнал записывающих вызовов Graph API.

    Нужен не для красоты: Meta не поддерживает ключи идемпотентности, поэтому
    единственный способ разобраться, что именно было создано перед обрывом, —
    писать каждый вызов до отправки и дописывать результат после.
    """

    __tablename__ = "meta_operations"
    __table_args__ = (
        Index("ix_meta_operations_launch", "launch_id", "kind"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    launch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_launches.id", ondelete="CASCADE")
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_rules.id", ondelete="SET NULL")
    )
    comment_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_comment_jobs.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    target_external_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class MetaRule(UUIDMixin, TimestampMixin, Base):
    """Автоправило — ТЗ 3.8.

    Условие считается по нашей же статистике, а не по правилам внутри Meta: ROI
    Meta не знает, доход приходит из Keitaro. `min_spend` не украшение — без него
    правило сработает на кампании с тремя кликами и нулевым доходом.
    """

    __tablename__ = "meta_rules"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_meta_rule_name"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"), index=True
    )
    launch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_launches.id", ondelete="CASCADE")
    )
    # Группа правил (Правила FB / Группы правил).
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_rule_groups.id", ondelete="SET NULL"), index=True
    )
    # С чем работать: campaign | adset | ad. Действие применяется к объекту
    # этого уровня, и метрики считаются по нему же.
    level: Mapped[str] = mapped_column(String(10), default="campaign", server_default="campaign")
    # Scope: весь кабинет или только конкретная кампания (и её вложенные
    # объекты). Для scope=campaign заполняется campaign_external_id.
    scope_kind: Mapped[str] = mapped_column(
        String(10), default="cabinet", server_default="cabinet"
    )
    campaign_external_id: Mapped[str | None] = mapped_column(String(100))
    # Какие статусы брать: active | paused | any.
    entity_status: Mapped[str] = mapped_column(
        String(10), default="active", server_default="active"
    )
    # Период статы: today | yesterday | last_2d | last_3d | last_7d | last_14d |
    # last_28d | last_30d | month.
    window: Mapped[str] = mapped_column(String(12), default="today", server_default="today")
    # Расписание: always | daily_midnight | custom. Для custom — JSON
    # {"days": [1..7], "intervals": [{"begin":"HH:MM","end":"HH:MM"}]}.
    schedule_kind: Mapped[str] = mapped_column(
        String(12), default="always", server_default="always"
    )
    schedule: Mapped[dict | None] = mapped_column(JSON)
    # Конвертация денежных метрик к валюте правила (курсы фиксированы в коде).
    convert_currency: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    currency: Mapped[str | None] = mapped_column(String(8))
    # Список условий, соединённых И: [{"metric","operator","value"}].
    # Пустой список — правило без условий: срабатывает на всём, что попало в
    # область. Это осмысленный режим («остановить все активные объявления»),
    # поэтому пустоту здесь не запрещаем.
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    min_spend: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    action: Mapped[str] = mapped_column(String(30), default="notify")
    action_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    # Уточнение действия с бюджетом/ставкой: знак (plus|minus — «+/-»),
    # режим (pct|sum — «%»/«сумма»), максимум (потолок бюджета/ставки) и вид
    # бюджета (daily|lifetime) для «Изменить бюджет».
    action_sign: Mapped[str] = mapped_column(
        String(6), default="plus", server_default="plus"
    )
    action_mode: Mapped[str] = mapped_column(
        String(8), default="pct", server_default="pct"
    )
    action_max: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    budget_kind: Mapped[str] = mapped_column(
        String(10), default="daily", server_default="daily"
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    # Как часто правило вообще проверяется. Отличается от cooldown: частота —
    # это «когда смотреть», cooldown — «как скоро можно сработать ещё раз».
    frequency_minutes: Mapped[int] = mapped_column(default=60, server_default="60")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Пауза между срабатываниями по одному и тому же объекту. Без неё правило
    # «поднять бюджет на 20%» удваивает его за час.
    cooldown_minutes: Mapped[int] = mapped_column(default=180)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class MetaRuleGroup(UUIDMixin, TimestampMixin, Base):
    """Группа правил: удобно применять и выключать наборы правок разом."""

    __tablename__ = "meta_rule_groups"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class MetaRuleEvent(UUIDMixin, Base):
    """Срабатывание правила: что увидели, что сделали и получилось ли."""

    __tablename__ = "meta_rule_events"
    __table_args__ = (
        Index("ix_meta_rule_events_created", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_rules.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meta_ad_accounts.id", ondelete="SET NULL")
    )
    campaign_external_id: Mapped[str | None] = mapped_column(String(100))
    campaign_name: Mapped[str | None] = mapped_column(String(300))
    metric: Mapped[str] = mapped_column(String(20))
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    action: Mapped[str] = mapped_column(String(30))
    applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    message: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Partner(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "partners"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_partner_external"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), nullable=True
    )
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)
    status_overridden: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class Offer(UUIDMixin, TimestampMixin, Base):
    """Оффер справочника.

    Строки заводятся вручную в разделе «Оффера» (`connection_id` пуст) либо
    приходят из Keitaro для Медиаборда, Финансов и Meta. Разделяет их именно
    `connection_id`: у ручного оффера нет ни трекера, ни внешнего id, поэтому
    синхронизация его не видит и не перезаписывает.
    """

    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_offer_external"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partners.id"))
    # У синхронизированного оффера — его id в Keitaro. У заведённого руками —
    # id этого же оффера в партнёрской программе: по нему депозиты из ПП
    # находят оффер и раскладываются в книгу баера. Отдельной таблицы привязок
    # нет намеренно: оффер и так заводят руками, и его номер у партнёрки —
    # такое же его свойство, как гео или ставка.
    external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    # Числовой суррогат оффера для Partner Integration Service: он принимает
    # `crm_offer_id` только целым числом и на UUID отвечает 422. Номер выдаётся
    # один раз при первой отправке привязки и дальше не меняется — им же
    # сервис помечает факты, которые возвращает в `/stats`.
    partner_ref: Mapped[int | None] = mapped_column(Integer, index=True)
    # Через какую интеграцию с ПП приходят депозиты по этому офферу. Номер
    # `external_id` принадлежит одной партнёрке, и без этой ссылки он уходил бы
    # всем сразу: у второй ПП тот же номер значит другой оффер, и депозиты
    # приехали бы не туда.
    partner_integration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partner_integrations.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    geo: Mapped[str | None] = mapped_column(String(12), index=True)
    # Капа — свободный текст: «300/день», «по договорённости», «—».
    # Числом её не сделать, команда пишет условие партнёрки как есть.
    cap: Mapped[str | None] = mapped_column(String(160))
    # Ставка партнёрки за целевое действие. В отличие от капы это число: с ним
    # оффер уезжает в книгу баера готовым к работе, без переписывания руками.
    cpa: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, server_default="0")
    cpa_currency: Mapped[str] = mapped_column(
        String(3), default="USD", server_default="USD"
    )
    # KPI и комментарий — длинный текст, который читают не в таблице, а когда
    # открывают конкретный оффер. Отдельными столбцами они бы съели ширину и
    # всё равно не поместились.
    kpi: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    group_name: Mapped[str | None] = mapped_column(String(160), index=True)
    status: Mapped[OfferStatus] = mapped_column(
        Enum(OfferStatus), default=OfferStatus.free, server_default="free", index=True
    )
    # Keitaro decides this one; the sync overwrites it on every run.
    keitaro_state: Mapped[Status] = mapped_column(
        Enum(Status), default=Status.active, server_default="active", index=True
    )
    is_starred: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    partner: Mapped[Partner | None] = relationship(lazy="selectin")


class OfferLead(Base):
    """Тимлид, которому отдан оффер, — первая ступень назначения."""

    __tablename__ = "offer_leads"

    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Капа именно этого тимлида по этому офферу. Партнёрка даёт общий лимит на
    # оффер, а тимлиды делят его между собой — поэтому цифра живёт на связке,
    # а не на самом оффере.
    cap: Mapped[str | None] = mapped_column(String(160))


class OfferBuyer(Base):
    __tablename__ = "offer_buyers"

    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )


class MediaRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "media_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "record_date", "buyer_id", "offer_id", name="uq_media_record"
        ),
        Index("ix_media_filters", "workspace_id", "record_date", "buyer_id", "offer_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offers.id"))
    installs: Mapped[int | None]
    registrations: Mapped[int | None]
    ftd: Mapped[int | None]
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    spend_calculated: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    spend_override: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    source: Mapped[str] = mapped_column(String(30), default="manual")
    manual_fields: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    external_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MediaServiceValue(UUIDMixin, Base):
    __tablename__ = "media_service_values"
    __table_args__ = (
        UniqueConstraint("media_record_id", "service_id", name="uq_media_service"),
    )

    media_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_records.id", ondelete="CASCADE")
    )
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    manual_cost_override: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))


class MediaSpendValue(UUIDMixin, Base):
    __tablename__ = "media_spend_values"
    __table_args__ = (
        UniqueConstraint("media_record_id", "provider_id", name="uq_media_provider"),
    )

    media_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_records.id", ondelete="CASCADE")
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spend_providers.id"))
    base_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    manual_amount_override: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))


class FinanceRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "import_key", name="uq_finance_import_key"),
        Index("ix_finance_filters", "workspace_id", "record_date", "buyer_id", "offer_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offers.id"))
    link: Mapped[str | None] = mapped_column(String(1000))
    import_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rent: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    spend_override: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    qual: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    revenue: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    salary: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    source: Mapped[str] = mapped_column(String(30), default="manual")


class FinanceServiceValue(UUIDMixin, Base):
    __tablename__ = "finance_service_values"
    __table_args__ = (
        UniqueConstraint("finance_record_id", "service_id", name="uq_finance_service"),
    )

    finance_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_records.id", ondelete="CASCADE")
    )
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    manual_cost_override: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))


class FinanceSpendValue(UUIDMixin, Base):
    __tablename__ = "finance_spend_values"
    __table_args__ = (
        UniqueConstraint("finance_record_id", "provider_id", name="uq_finance_provider"),
    )

    finance_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_records.id", ondelete="CASCADE")
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spend_providers.id"))
    base_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    manual_amount_override: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))


class FinanceBook(UUIDMixin, TimestampMixin, Base):
    """Месяц одного баера: то, что раньше было отдельной вкладкой гугл-таблицы.

    Всё, что здесь лежит, заполняет финансист руками. Из Keitaro сюда ничего не
    приходит — цифры сверяются с кабинетами, а не с трекером.
    """

    __tablename__ = "finance_books"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "buyer_id",
            "year",
            "month",
            "tier",
            name="uq_finance_book_period",
        ),
        Index("ix_finance_books_period", "workspace_id", "year", "month"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    year: Mapped[int] = mapped_column(nullable=False)
    month: Mapped[int] = mapped_column(nullable=False)
    # Тир книги: `T1` или `T23`. Баер ведёт две отдельные таблицы, потому что
    # спенд по Tier1 и Tier2/3 приходит из разных кабинетов и разными суммами —
    # делить общий расход между тирами было нечем, кроме пропорции.
    tier: Mapped[str] = mapped_column(String(4), nullable=False, server_default="T1")
    # Автоматический входящий долг: вычитается из зарплаты, в дневные расчёты
    # не входит. Самый ранний сохранённый остаток служит начальным балансом для
    # данных, перенесённых из прежней ручной версии.
    prev_minus: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, server_default="0")
    # Курс евро к доллару для этого месяца. Книга всегда считается в долларах, а
    # ставки евровых офферов переводятся по этому курсу. Курс хранится в самой
    # книге, а не берётся живым на каждый расчёт: иначе профит и зарплата за
    # закрытый месяц менялись бы каждый день вместе с рынком.
    eur_usd_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), default=1, server_default="1"
    )


class CountryTier(UUIDMixin, Base):
    """Страна, отнесённая к тиру, — «Настройки → Тиры стран».

    В таблице лежит только то, что отличается от умолчания: страна без строки
    считается Tier2/3. Хранить все две сотни стран ради одного и того же
    ответа незачем, а список Tier1 команда правит по договорённостям с
    партнёрками — сегодня Португалия первый тир, завтра нет.
    """

    __tablename__ = "country_tiers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_country_tier_code"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # Alpha-2, как и везде в CRM: гео проходит через `normalize_geo`.
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    tier: Mapped[str] = mapped_column(String(4), nullable=False)


class FinanceBookOffer(UUIDMixin, Base):
    __tablename__ = "finance_book_offers"

    book_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_books.id", ondelete="CASCADE"), index=True
    )
    # Откуда строка приехала. Назначение баера тянет оффер в его книгу, и по
    # этой ссылке повторное назначение узнаёт свою строку вместо того, чтобы
    # заводить дубль с тем же названием.
    source_offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), index=True
    )
    # Порядок задаёт финансист перетаскиванием блоков, поэтому он хранится, а не
    # выводится из имени.
    position: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    partner: Mapped[str | None] = mapped_column(String(160))
    # Гео оффера. Тир из него берётся справочником «Настройки → Тиры стран» и
    # не хранится: иначе правка справочника не догнала бы уже заведённые книги,
    # и один и тот же оффер в двух местах отвечал бы по-разному.
    geo: Mapped[str | None] = mapped_column(String(12))
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, server_default="0")
    # Валюта ставки: офферы бывают и в евро. Доход всё равно считается в долларах —
    # евровая ставка переводится по курсу книги.
    rate_currency: Mapped[str] = mapped_column(
        String(3), default="USD", server_default="USD"
    )
    # Поля, которые финансист заполнил вручную и защитил от обновления из
    # справочника «Оффера». Замок хранится на строке книги.
    locked_fields: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )


class FinanceBookDay(UUIDMixin, Base):
    __tablename__ = "finance_book_days"
    __table_args__ = (UniqueConstraint("book_id", "day", name="uq_finance_book_day"),)

    book_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_books.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[int] = mapped_column(nullable=False)
    spend_buyer: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, server_default="0")
    # null у media_spend — прежняя ручная запись, ещё не сверенная с доской.
    media_spend: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    # Явная замена автоматической суммы. null — использовать медиаборд; 0 — ручной ноль.
    manual_spend: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    # Сверка с кабинетом: в расчёты не входит, но финансист её ведёт.
    spend_agent: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, server_default="0")
    costs: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, server_default="0")


class FinanceOfferTag(UUIDMixin, Base):
    """Именованная строка под оффером, по которой вводятся депозиты за день.

    SOK — просто первый такой тег, а не отдельная сущность: команда переименовывает
    его и заводит рядом свои — долёты, RAF, что угодно.
    """

    __tablename__ = "finance_offer_tags"

    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_book_offers.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class FinanceTagDay(UUIDMixin, Base):
    __tablename__ = "finance_tag_days"
    __table_args__ = (UniqueConstraint("tag_id", "day", name="uq_finance_tag_day"),)

    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_offer_tags.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[int] = mapped_column(nullable=False)
    # Депозиты за день по этому тегу; доход оффера — их сумма × ставка.
    deposits: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, server_default="0")


class TaskSection(UUIDMixin, TimestampMixin, Base):
    """Раздел доски задач — отдельная доска отдела.

    Список плоский, без дерева: раздел здесь означает отдел («Дизайнеры»,
    «Баеры»), а отделы друг в друга не вкладываются. Колонки, поля и шаблоны
    остаются общими на воркспейс — разделы делят между собой карточки и права,
    а не устройство доски.
    """

    __tablename__ = "task_sections"
    __table_args__ = (
        Index("ix_task_sections_order", "workspace_id", "position"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(default=0)
    # Шаблон, с которым открывается новая задача этого раздела. У каждого отдела
    # свой бриф: дизайнеру нужны «Вид крео» и «Формат», баеру — совсем другое.
    default_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("task_templates.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class TaskSectionAccess(UUIDMixin, Base):
    """Права на раздел доски — устроены как права базы знаний (ТЗ 8.2).

    Правило адресовано либо роли, либо человеку: заполнена ровно одна ссылка.
    Именное правило сильнее ролевого — иначе раздел «только для Ирины»
    описывался бы ролью под одного человека.

    Раздел без единого правила ведёт себя как доска до появления разделов:
    виден всем с `workspace.view`, и в нём можно заводить задачи. Правка и
    удаление чужих карточек — отдельные права: без них остаётся обычное правило
    карточки (свои задачи и те, где ты исполнитель).
    """

    __tablename__ = "task_section_access"
    __table_args__ = (
        UniqueConstraint("section_id", "role_id", name="uq_task_section_access_role"),
        UniqueConstraint("section_id", "user_id", name="uq_task_section_access_user"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_sections.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    can_view: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_create: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TaskStatus(UUIDMixin, TimestampMixin, Base):
    """Колонка канбан-доски — ТЗ 8.1.

    Пять статусов заводит seed, остальные создаёт команда. `is_system` защищает
    базовые от удаления, `is_terminal` отмечает колонки, где задача считается
    завершённой, — по нему строится «сколько ещё в работе».
    """

    __tablename__ = "task_statuses"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_task_status_code"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#9B9292")
    position: Mapped[int] = mapped_column(default=0, index=True)
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    is_terminal: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class TaskField(UUIDMixin, TimestampMixin, Base):
    """Пользовательское поле задачи — ТЗ 8.1.

    Значения полей лежат в `Task.custom_values` одним JSON, а не отдельной
    таблицей: полей у задачи единицы, и любой запрос всё равно тянет карточку
    целиком. Ключ значения — `id` поля, поэтому переименование поля не ломает
    уже заполненные задачи.
    """

    __tablename__ = "task_fields"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_task_field_name"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="text")
    options: Mapped[list] = mapped_column(JSON, default=list)
    # Настройки, зависящие от типа: валюта для «Сумма», подпись кнопки для
    # «Ссылка». Отдельным JSON, а не колонками, — у каждого типа они свои.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    position: Mapped[int] = mapped_column(default=0)
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Поле доски против поля шаблона. Общее поле есть в каждой карточке;
    # выключенное показывается только тем задачам, чей шаблон его подключил, —
    # иначе бриф на креатив тянул бы за собой поля бухгалтерии.
    show_always: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class Task(UUIDMixin, TimestampMixin, Base):
    """Карточка задачи — ТЗ 8.1.

    `position` задаёт порядок внутри колонки: канбан переставляют мышью, и без
    собственного порядка карточки прыгали бы при каждом обновлении.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_board", "workspace_id", "status_id", "position"),
        Index("ix_tasks_section", "workspace_id", "section_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # Раздел задаётся всегда: карточка без раздела не попала бы ни на одну
    # доску. Раздел не удаляют, пока в нём есть задачи, — их переносят.
    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_sections.id", ondelete="RESTRICT"), index=True
    )
    status_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_statuses.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Дата начала и срок — разные вещи: по первой видно, когда задачу берут в
    # работу, по второй — когда её ждут сделанной.
    start_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority), default=TaskPriority.medium, index=True
    )
    position: Mapped[int] = mapped_column(default=0)
    custom_values: Mapped[dict] = mapped_column(JSON, default=dict)
    # Поля, созданные прямо из этой карточки. Определения остаются в общем
    # справочнике (так сохраняются тип и настройки), но другие задачи их не
    # показывают, пока поле не добавили в их собственный список или шаблон.
    field_ids: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    # По какому шаблону заведена карточка — от этого зависит набор её полей.
    # Удаление шаблона обнуляет ссылку, но поля с уже заполненными значениями
    # из карточки не пропадают.
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("task_templates.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignees: Mapped[list["TaskAssignee"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan"
    )


class TaskStatusEvent(UUIDMixin, Base):
    """Переход задачи в статус: кто и когда. Название статуса копируется —
    колонку могут переименовать или удалить, а история должна читаться."""

    __tablename__ = "task_status_events"
    __table_args__ = (Index("ix_task_status_events_task", "task_id", "created_at"),)

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    status_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("task_statuses.id", ondelete="SET NULL")
    )
    status_name: Mapped[str] = mapped_column(String(80), default="", server_default="")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TaskAssignee(Base):
    """Исполнитель. Их может быть несколько — ТЗ 8.1."""

    __tablename__ = "task_assignees"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class TaskTemplate(UUIDMixin, TimestampMixin, Base):
    """Шаблон задачи — ТЗ 8.1.

    Задаёт набор пользовательских полей карточки и их значения по умолчанию.
    Стандартные поля — название, приоритет, колонку, срок, описание,
    исполнителей — шаблон не трогает: их заполняют под конкретную задачу.

    Ссылки на поля идут по ID: удалённое поле просто перестаёт подставляться,
    а шаблон продолжает работать.
    """

    __tablename__ = "task_templates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_task_template_name"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    custom_values: Mapped[dict] = mapped_column(JSON, default=dict)
    # Какие поля шаблон выводит в карточку и в каком порядке. Список ID, а не
    # связь: порядок здесь смысловой (бриф читают сверху вниз), и хранить его
    # в отдельной таблице ради этого незачем.
    field_ids: Mapped[list] = mapped_column(JSON, default=list)
    # Шаблон, который подставляется в новую задачу сам. Один на воркспейс:
    # команда работает по одному брифу, и выбирать его каждый раз заново —
    # лишний шаг в самом частом действии раздела.
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class KnowledgeSection(UUIDMixin, TimestampMixin, Base):
    """Раздел базы знаний — ТЗ 8.2. Дерево строится через `parent_id`."""

    __tablename__ = "knowledge_sections"
    __table_args__ = (
        Index("ix_knowledge_sections_tree", "workspace_id", "parent_id", "position"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_sections.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(default=0)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class KnowledgeArticle(UUIDMixin, TimestampMixin, Base):
    """Статья — ТЗ 8.2.

    Содержимое хранится списком блоков в JSON, как в Notion, а не готовым HTML:
    из блоков можно перерисовать статью в любом виде, а из HTML обратно блоки
    уже не собрать. `search_text` — плоская выжимка тех же блоков, по ней и
    ищем: по JSON полнотекстовый поиск не построить.
    """

    __tablename__ = "knowledge_articles"
    __table_args__ = (
        Index("ix_knowledge_articles_section", "section_id", "position"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_sections.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_articles.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    blocks: Mapped[list] = mapped_column(JSON, default=list)
    search_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ArticleStatus] = mapped_column(
        Enum(ArticleStatus), default=ArticleStatus.draft, index=True
    )
    position: Mapped[int] = mapped_column(default=0)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeAccess(UUIDMixin, Base):
    """Права на раздел базы знаний — ТЗ 8.2.

    Правило адресовано либо роли, либо конкретному человеку: заполнена ровно
    одна из ссылок. Именное правило сильнее ролевого — иначе раздел «только для
    Ирины» невозможно было бы описать, не заводя роль под одного человека.

    Правило наследуется вниз по дереву: заданное на корневом разделе действует и
    на вложенные, пока у вложенного нет собственного. Раздел без единого правила
    открыт всем, у кого есть `knowledge.view` — иначе первый же созданный раздел
    оказался бы невидимым даже своему автору.
    """

    __tablename__ = "knowledge_access"
    __table_args__ = (
        UniqueConstraint("section_id", "role_id", name="uq_knowledge_access_role"),
        UniqueConstraint("section_id", "user_id", name="uq_knowledge_access_user"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_sections.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    can_view: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_create: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class KnowledgeAttachment(UUIDMixin, Base):
    """Вложение воркспейса: файл статьи (ТЗ 8.2) или файл задачи (ТЗ 8.1).

    Сам файл лежит на диске, в базе только метаданные и путь. Отдаётся всегда
    через API с проверкой прав: раздел может быть закрыт для роли, и прямая
    ссылка на файл не должна обходить это.

    Имя таблицы историческое — вложения появились в базе знаний, а поля задач
    типа «Файлы» переиспользуют то же хранилище и ту же проверку загрузки.
    Заполнена ровно одна из ссылок; обе пустые — файл ещё не закреплён и
    удаляется фоновой уборкой.
    """

    __tablename__ = "knowledge_attachments"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    # Запись интервью в карточке кандидата. Третья возможная привязка: без неё
    # уборщик незакреплённых файлов удалил бы запись через сутки.
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recruitment_candidates.id", ondelete="CASCADE"), index=True
    )
    file_name: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    byte_size: Mapped[int] = mapped_column(default=0)
    storage_path: Mapped[str] = mapped_column(String(400), nullable=False)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TelegramBot(UUIDMixin, TimestampMixin, Base):
    """Бот команды для уведомлений — ТЗ 9.

    Один на воркспейс: у команды один бот и несколько чатов, а не наоборот.
    Хранить токен в каждом канале значило бы менять его в пяти местах при
    первой же ротации.

    Токен лежит зашифрованным и через API не возвращается — по нему можно
    писать от имени команды куда угодно.
    """

    __tablename__ = "telegram_bots"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_telegram_bot_workspace"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    username: Mapped[str | None] = mapped_column(String(120))
    token_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class AlertChannel(UUIDMixin, TimestampMixin, Base):
    """Чат, куда уходят уведомления — ТЗ 9.1, 9.2.

    `thread_id` — тема супергруппы: в общий чат команды сыпать алерты по CAP
    нельзя, их читают отдельные люди.
    """

    __tablename__ = "alert_channels"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)


class AlertRule(UUIDMixin, TimestampMixin, Base):
    """Уведомление в Telegram — ТЗ 9.1.

    Два вида, и они устроены по-разному:

    * `deposit` — сообщение на каждый депозит из журнала конверсий Keitaro.
      Смотрит не на числа, а на события: пришла продажа — ушло сообщение.
    * `report` — сводка по расписанию: период, лиды, продажи, доход, расход,
      профит, ROI. Область у неё всегда вся команда.

    Универсального «дерева условий» здесь больше нет. Оно позволяло собрать
    что угодно, но команде нужны ровно эти два сценария, а всё остальное только
    множило способы ошибиться.

    `cursor_at` — у уведомления о депозитах: до какого момента конверсии уже
    разосланы. По времени самой конверсии двигать курсор нельзя, трекер отдаёт
    их с задержкой и задним числом; поэтому курсор идёт по `seen_at` — когда
    строку увидели мы.
    """

    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)
    # deposit | report
    kind: Mapped[str] = mapped_column(String(16), default="deposit")
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alert_channels.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[str | None] = mapped_column(String(32))
    # Условия уведомления о депозитах: {"op": "and", "items": [условие | группа]}.
    # Условие — {"field", "operator", "values" | "text"}. Полей ровно два —
    # группа кампаний Keitaro и оффер, — но одного списка отмеченных значений
    # не хватало: «всё, кроме этой группы» галочками не выразить.
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    # Отчёт: период сводки, расписание и таймзона, по которой оно считается.
    window: Mapped[str] = mapped_column(String(16), default="today")
    schedule: Mapped[str] = mapped_column(
        String(24), default="daily_09", server_default="daily_09", nullable=False
    )
    timezone: Mapped[str] = mapped_column(
        String(64), default="Europe/Moscow", server_default="Europe/Moscow", nullable=False
    )
    message_template: Mapped[str | None] = mapped_column(Text)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapRule(UUIDMixin, TimestampMixin, Base):
    """CAP на оффер и оповещение о его исчерпании — ТЗ 9.2.

    `notify_at` — пороги в процентах: команде нужно знать не только про сотый
    процент, но и про восьмидесятый, пока ещё можно перелить трафик.
    `notified_percent` помнит максимальный уже отправленный порог, поэтому один
    и тот же рубеж не приходит дважды за период.
    """

    __tablename__ = "cap_rules"
    __table_args__ = (
        Index("ix_cap_rules_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alert_channels.id", ondelete="CASCADE"), index=True
    )
    # Каналов может быть несколько: один и тот же лимит ждут и в чате команды,
    # и у тимлида. `channel_id` — первый из них: на нём держится внешний ключ,
    # и по нему же уходит сообщение у капы, заведённой до этой возможности.
    channel_ids: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    # Несколько офферов на одну капу: их показатели складываются. Одного поля
    # не хватало — партнёрка обычно даёт общий лимит на связку офферов, а не
    # на каждый по отдельности.
    offer_ids: Mapped[list] = mapped_column(JSON, default=list)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    # Что ограничиваем: ftd | leads | installs | spend
    metric: Mapped[str] = mapped_column(String(40), default="ftd")
    limit_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    # day | week | month
    period: Mapped[str] = mapped_column(String(16), default="day")
    # Таймзона сброса счётчика. Без неё «каждый день в 00:00» означало бы
    # полночь сервера, а команда живёт по своему времени.
    timezone: Mapped[str] = mapped_column(
        String(64), default="Europe/Moscow", server_default="Europe/Moscow"
    )
    # Тема супергруппы для этой конкретной капы. У канала есть свой thread_id,
    # но капы одного канала часто разводят по разным темам.
    thread_id: Mapped[str | None] = mapped_column(String(32))
    notify_at: Mapped[list] = mapped_column(JSON, default=list)
    notified_percent: Mapped[int] = mapped_column(default=0)
    notified_period: Mapped[str | None] = mapped_column(String(24))
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertEvent(UUIDMixin, Base):
    """Что и когда ушло в Telegram — ТЗ 9.1, 9.2.

    Журнал нужен не для красоты: когда алерт не пришёл, единственный способ
    отличить «правило не сработало» от «Telegram не принял» — это запись с
    текстом ошибки.
    """

    __tablename__ = "alert_events"
    __table_args__ = (
        Index("ix_alert_events_workspace_time", "workspace_id", "created_at"),
        Index("ix_alert_events_pending", "delivered", "next_attempt_at"),
        Index("uq_alert_events_event_key", "event_key", unique=True),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    alert_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True
    )
    cap_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cap_rules.id", ondelete="CASCADE"), index=True
    )
    # Куда именно ушло это событие. У капы каналов может быть несколько, и на
    # каждый заводится своё событие: доставка в один чат не должна зависеть от
    # того, приняла ли сообщение другая группа. Пусто — канал правила.
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alert_channels.id", ondelete="SET NULL"), index=True
    )
    rule_name: Mapped[str] = mapped_column(String(160), default="")
    kind: Mapped[str] = mapped_column(String(16), default="trigger")
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    message: Mapped[str] = mapped_column(Text, default="")
    # Идемпотентный ключ превращает журнал в надёжную очередь: один депозит,
    # временной слот отчёта или порог CAP нельзя поставить в неё дважды, даже
    # если планировщик и синхронизация Keitaro проснулись одновременно.
    event_key: Mapped[str | None] = mapped_column(String(220))
    delivered: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SalaryRule(UUIDMixin, TimestampMixin, Base):
    """Правило расчёта зарплаты — «Настройки → Расчет ЗП».

    Правило адресуется роли или конкретному человеку. Именное сильнее
    ролевого: у людей с одной ролью бывают разные договорённости, а вот
    «всем сразу» на практике не встречалось — зарплата всегда чья-то.
    `mode` решает, что делать с тем, что дало ролевое правило: «заменить»
    отбрасывает его целиком, «дополнить» добавляет свои компоненты к уже
    собранным.

    Даты действия хранятся здесь, а не в компонентах: правило меняют целиком,
    когда договорённость с человеком поменялась, и старое должно остаться в
    истории — иначе пересчёт закрытого месяца дал бы другую сумму.
    """

    __tablename__ = "salary_rules"
    __table_args__ = (
        Index("ix_salary_rules_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)
    # replace — заменить правила пошире, add — дополнить их.
    mode: Mapped[str] = mapped_column(String(16), default="replace")
    # role | user
    scope: Mapped[str] = mapped_column(String(16), default="role")
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    position: Mapped[int] = mapped_column(default=0)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    components: Mapped[list["SalaryComponent"]] = relationship(
        "SalaryComponent",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SalaryComponent.position",
    )


class SalaryComponent(UUIDMixin, Base):
    """Одна строка формулы правила.

    Правило почти всегда состоит из нескольких частей: процент от профита плюс
    сетка по профиту команды плюс фиксированный оклад. Поэтому компоненты
    отдельной таблицей, а не одним полем формулы: их складывают, переставляют и
    удаляют по одному.
    """

    __tablename__ = "salary_components"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("salary_rules.id", ondelete="CASCADE"), index=True
    )
    # percent | fixed | grid | deduction
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    base: Mapped[str | None] = mapped_column(String(40))
    percent: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    # Уровни сетки: [{"up_to": "5000", "percent": "10"}, ...]; последний может
    # быть без потолка.
    tiers: Mapped[list] = mapped_column(JSON, default=list)
    position: Mapped[int] = mapped_column(default=0)


class UserPreference(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "user_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_preference_key"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditEvent(UUIDMixin, Base):
    __tablename__ = "audit_events"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class MetaComment(UUIDMixin, Base):
    """Снимок комментария под постом объявления.

    Комментарии кешируются, а не читаются из Meta на каждый показ, по трём
    причинам. Фильтры и поиск по тексту должны работать по всему набору, а не по
    той странице, что Graph отдал последней. Массовая чистка обязана знать, что
    именно уже удалено, — второй заход по тому же id получил бы ошибку. И
    главное: удалённый комментарий Meta не возвращает никогда, поэтому текст
    сохраняется здесь до удаления — иначе в CRM не осталось бы следа, за что
    именно человека вычистили.
    """

    __tablename__ = "meta_comments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "external_id", name="uq_meta_comment_external"),
        Index("ix_meta_comments_post", "account_id", "post_external_id"),
        Index("ix_meta_comments_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    post_external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    page_external_id: Mapped[str | None] = mapped_column(String(100))
    # Ответ на другой комментарий: ветки чистят целиком, и в списке их надо
    # отличать от корневых.
    parent_external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    author_external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    author_name: Mapped[str | None] = mapped_column(String(300))
    message: Mapped[str] = mapped_column(Text, default="")
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reply_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Признаки считаются один раз при загрузке: по ним фильтруют, и гонять
    # регулярку по всей таблице на каждый запрос списка незачем.
    has_link: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_phone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # visible | hidden | deleted — наш взгляд на комментарий после наших действий.
    status: Mapped[str] = mapped_column(
        String(10), default="visible", server_default="visible", nullable=False
    )
    created_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class MetaCommentJob(UUIDMixin, Base):
    """Задание на загрузку или чистку комментариев.

    Отдельная сущность, а не просто набор операций: и чтение, и удаление идут с
    паузой между вызовами — пачка запросов в несколько потоков к комментариям
    самый быстрый способ получить чекпоинт на аккаунте. Заданию нужен прогресс,
    отмена на середине и запрет на второй параллельный запуск по тому же
    кабинету.
    """

    __tablename__ = "meta_comment_jobs"
    __table_args__ = (
        Index("ix_meta_comment_jobs_account", "account_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    # fetch — забрать комментарии; delete / hide / unhide — применить действие.
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    # queued | running | done | failed | cancelled
    status: Mapped[str] = mapped_column(
        String(10), default="queued", server_default="queued", nullable=False, index=True
    )
    # Что обрабатываем: {"posts": [...]} для fetch, {"comments": [...]} для действий.
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Ставится из интерфейса; движок смотрит на него между вызовами, поэтому
    # отмена срабатывает на следующем комментарии, а не рвёт текущий запрос.
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecruitmentCandidate(UUIDMixin, TimestampMixin, Base):
    """Кандидат в найме — этапы, которые ведёт CRM.

    Recruitment Service владеет находками и их триажем (`pending` → `added` /
    `skipped`) и намеренно не хранит воронку найма: скрининг, интервью, оффер,
    отказ — это наша сторона. Здесь и живёт этап.

    Поля профиля продублированы снимком на момент добавления. Не ради скорости:
    сервис может быть недоступен, а доска найма должна открываться и работать —
    человек на интервью не перестаёт существовать оттого, что упал контейнер
    рекрутинга. Свежие данные подмешиваются к снимку, когда сервис отвечает.
    """

    __tablename__ = "recruitment_candidates"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "external_id", name="uq_recruitment_candidate_external"
        ),
        Index("ix_recruitment_candidates_stage", "workspace_id", "stage"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # id находки в Recruitment Service — по нему подтягиваем свежий профиль.
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # screening | interview | tech_interview | offer | hired | rejected
    stage: Mapped[str] = mapped_column(
        String(24), default="screening", server_default="screening", nullable=False
    )
    position_title: Mapped[str | None] = mapped_column(String(300))
    geo: Mapped[str | None] = mapped_column(String(160))
    source: Mapped[str | None] = mapped_column(String(30))
    tier: Mapped[str | None] = mapped_column(String(10))
    score: Mapped[int | None] = mapped_column(Integer)
    salary_expectation: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    experience_months: Mapped[int | None] = mapped_column(Integer)
    external_url: Mapped[str | None] = mapped_column(String(500))
    # Остальной снимок находки одним полем: имя, дата рождения, город, ник в
    # телеграме, текст отклика и файл. Колонкой на каждое поле это была бы
    # миграция под каждую правку сервиса — а состав того, что он отдаёт, ещё
    # меняется. В расчётах эти данные не участвуют, только показываются.
    profile: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    # Позиция, на которую рассматриваем, — не та, что стоит в резюме:
    # `position_title` это снимок из HH, а здесь решение команды.
    target_position: Mapped[str | None] = mapped_column(String(300))
    # Телеграм кандидата: в резюме HH его нет, а переписка идёт там.
    telegram_contact: Mapped[str | None] = mapped_column(String(120))
    # Запись интервью — ссылка на встречу или загруженный файл. Одним полем:
    # для нанимающего это одно и то же «где посмотреть интервью».
    interview_record: Mapped[str | None] = mapped_column(String(500))
    stage_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    added_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Убран с доски. Строку не удаляем: сервис по-прежнему считает находку
    # разобранной и при следующем открытии доски вернул бы её обратно —
    # карточка «удалялась» и тут же появлялась снова. Отметка помнит решение
    # CRM: на доске такого кандидата нет, а повторно завести его можно из
    # «Откликов» — тогда отметка снимается.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(UUIDMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key", name="uq_idempotency_workspace_key"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PartnerIntegration(UUIDMixin, TimestampMixin, Base):
    """Интеграция с Partner Integration Service — депозиты по тегам баеров.

    Сервис хранит факты «дата — оффер — тег — депозиты», собранные из
    партнёрских программ. CRM привязывает свои офферы к офферам ПП и
    раскладывает депозит в книгу баера: по тегу из ПП — строка тега в книге,
    и значения попадают в те же ячейки, что раньше заполнялись руками.
    """

    __tablename__ = "partner_integrations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "name", name="uq_partner_integration_name"
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    partner_name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Платформа, на которой работает ПП: affise | alanbase | afftech. По ней
    # сервис выбирает готовый шаблон коннектора — вручную его не собирают.
    platform: Mapped[str] = mapped_column(String(40), default="", server_default="")
    # Адрес и ключ самой партнёрки, а не сервиса: сервис у CRM один, его адрес
    # живёт в настройках развёртывания. Эти доступы CRM передаёт сервису при
    # создании интеграции, дальше в ПП ходит он.
    base_url: Mapped[str] = mapped_column(String(300), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(12))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    # id этой же интеграции в Partner Integration Service. Конфиг коннектора к
    # ПП (connector_config, credentials) заводится на стороне сервиса — там он
    # и живёт; CRM только ссылается на готовую интеграцию, чтобы заводить в ней
    # привязки офферов и просить синк. Пусто — работаем только на чтение
    # /stats, и привязки придётся заводить на сервисе руками.
    external_id: Mapped[str | None] = mapped_column(String(64))


class PartnerPendingTag(UUIDMixin, Base):
    """Тег, которому не нашлось строки в финансах, — деньги ждут человека.

    Молча выбрасывать такие факты нельзя: это реальные депозиты, и «синк прошёл,
    записей 0» вместо них — худший из возможных ответов. Разбирается такая
    строка не настройкой, а тем, что тег заводят в книге баера под нужным
    оффером: следующий синк за тот же период разложит всё задним числом.
    """

    __tablename__ = "partner_pending_tags"
    __table_args__ = (
        UniqueConstraint("workspace_id", "tag_key", name="uq_partner_pending_tag"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    tag_key: Mapped[str] = mapped_column(String(120), nullable=False)
    tag: Mapped[str] = mapped_column(String(120), nullable=False)
    # Почему не разложили: no_buyer (тег никому не принадлежит) или
    # ambiguous (тег встречается в книгах у нескольких баеров).
    reason: Mapped[str] = mapped_column(
        String(12), default="no_buyer", server_default="no_buyer", nullable=False
    )
    facts_count: Mapped[int] = mapped_column(default=0, server_default="0")
    deposits_total: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, server_default="0"
    )
    sample_offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PartnerSyncRun(UUIDMixin, Base):
    """История синков интеграции — статусы и ошибки видно прямо в CRM."""

    __tablename__ = "partner_sync_runs"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_integrations.id", ondelete="CASCADE"), index=True
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), default="running", server_default="running", nullable=False
    )
    records_upserted: Mapped[int] = mapped_column(default=0, server_default="0")
    # Факты, которые пришли, но в книги не легли: тег без баера, оффер не
    # найден, битая дата. Без этого «успех, записей 0» выглядит нормой.
    records_pending: Mapped[int] = mapped_column(default=0, server_default="0")
    records_skipped: Mapped[int] = mapped_column(default=0, server_default="0")
    # Первые причины пропусков — чтобы отладка не требовала лезть в логи.
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(
        String(10), default="manual", server_default="manual"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
