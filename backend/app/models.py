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
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Qyzylorda")
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
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="keitaro")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)
    sync_interval_minutes: Mapped[int] = mapped_column(default=15)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    buyer_sub_id: Mapped[int] = mapped_column(default=1)
    lookback_days: Mapped[int] = mapped_column(default=2)
    checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


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


class Partner(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "partners"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_partner_external"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active)
    status_overridden: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class Offer(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_offer_external"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_connections.id", ondelete="CASCADE")
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partners.id"))
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    geo: Mapped[str | None] = mapped_column(String(12), index=True)
    group_name: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.active, index=True)
    status_overridden: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    partner: Mapped[Partner | None] = relationship(lazy="selectin")


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
