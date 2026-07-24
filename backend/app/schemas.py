import uuid
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import ProviderType, Status


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


class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    login: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    role_id: uuid.UUID
    status: Status = Status.active
    parent_ids: list[uuid.UUID] = Field(default_factory=list)
    keitaro_company_group: str | None = None
    keitaro_offer_group: str | None = None


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    login: str | None = Field(default=None, min_length=3, max_length=100)
    role_id: uuid.UUID | None = None
    status: Status | None = None
    parent_ids: list[uuid.UUID] | None = None
    keitaro_company_group: str | None = Field(default=None, max_length=160)
    keitaro_offer_group: str | None = Field(default=None, max_length=160)


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=255)
    permission_codes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=255)
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
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
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


class OfferOut(ORMModel):
    id: uuid.UUID
    external_id: str
    name: str
    geo: str | None
    group_name: str | None
    status: Status
    status_overridden: bool


class AssignBuyers(BaseModel):
    buyer_ids: list[uuid.UUID]


class CatalogStatusUpdate(BaseModel):
    status: Status


class MediaRecordIn(BaseModel):
    record_date: date
    buyer_id: uuid.UUID
    offer_id: uuid.UUID
    installs: int | None = None
    registrations: int | None = None
    ftd: int | None = None
    revenue: Decimal | None = None
    spend_calculated: Decimal = Decimal("0")
    spend_override: Decimal | None = None
    source: str = "manual"


class MediaServiceValueIn(BaseModel):
    service_id: uuid.UUID
    quantity: Decimal = Decimal("0")
    manual_cost_override: Decimal | None = None


class MediaSpendValueIn(BaseModel):
    provider_id: uuid.UUID
    base_amount: Decimal = Decimal("0")
    manual_amount_override: Decimal | None = None


class MediaValuesIn(BaseModel):
    services: list[MediaServiceValueIn] = Field(default_factory=list)
    spend_providers: list[MediaSpendValueIn] = Field(default_factory=list)


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
    series: list[dict] = Field(default_factory=list)


class APIError(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
