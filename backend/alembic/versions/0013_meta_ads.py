"""Meta Ads: кабинеты, кампании и дневная статистика.

Подключение живёт в общей таблице integration_connections с kind="meta" — вместе
с ней бесплатно достаются шифрование токена, история запусков и карточки в
Настройках. Meta-специфичные поля добавлены туда же и остаются NULL у Keitaro.
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0013_meta_ads"
down_revision = "0012_remove_partners_view"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("meta.view", "meta view"),
    ("meta.manage", "meta manage"),
]


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    # 0001 поднимает схему через metadata.create_all, поэтому на чистой базе новые
    # таблицы и колонки уже существуют к моменту этой ревизии. Проверки ниже делают
    # её одинаково безопасной и для чистой базы, и для рабочей на 0012.
    inspector = _inspector()
    tables = set(inspector.get_table_names())
    connection_columns = {
        column["name"] for column in inspector.get_columns("integration_connections")
    }
    for name, column in [
        ("external_account_id", sa.Column("external_account_id", sa.String(length=100))),
        ("attribution_sub_id", sa.Column("attribution_sub_id", sa.Integer())),
    ]:
        if name not in connection_columns:
            op.add_column("integration_connections", column)

    if "meta_ad_accounts" in tables:
        _grant_permissions()
        return

    op.create_table(
        "meta_ad_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("account_status", sa.String(length=40), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("timezone_name", sa.String(length=64), nullable=True),
        sa.Column("spend_cap", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount_spent", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("balance", sa.Numeric(18, 2), nullable=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("external_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "external_id", name="uq_meta_account_external"),
    )
    op.create_index("ix_meta_ad_accounts_workspace_id", "meta_ad_accounts", ["workspace_id"])
    op.create_index("ix_meta_ad_accounts_connection_id", "meta_ad_accounts", ["connection_id"])
    op.create_index("ix_meta_ad_accounts_owner_id", "meta_ad_accounts", ["owner_id"])
    op.create_index("ix_meta_ad_accounts_status", "meta_ad_accounts", ["status"])

    op.create_table(
        "meta_entities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("parent_external_id", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("effective_status", sa.String(length=40), nullable=True),
        sa.Column("objective", sa.String(length=60), nullable=True),
        sa.Column("daily_budget", sa.Numeric(18, 2), nullable=True),
        sa.Column("lifetime_budget", sa.Numeric(18, 2), nullable=True),
        sa.Column("external_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", "external_id", name="uq_meta_entity_external"),
    )
    op.create_index("ix_meta_entities_workspace_id", "meta_entities", ["workspace_id"])
    op.create_index("ix_meta_entities_connection_id", "meta_entities", ["connection_id"])
    op.create_index("ix_meta_entities_account_id", "meta_entities", ["account_id"])
    op.create_index("ix_meta_entities_parent", "meta_entities", ["parent_external_id"])
    op.create_index("ix_meta_entities_level", "meta_entities", ["workspace_id", "level"])

    op.create_table(
        "meta_stats_daily",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("campaign_external_id", sa.String(length=100), nullable=True),
        sa.Column("adset_external_id", sa.String(length=100), nullable=True),
        sa.Column("ad_external_id", sa.String(length=100), nullable=True),
        sa.Column("country_code", sa.String(length=12), nullable=True),
        sa.Column("dimension_key", sa.String(length=64), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reach", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spend", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("pixel_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pixel_purchases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "dimension_key", name="uq_meta_stat_dimension"),
    )
    op.create_index("ix_meta_stats_account_id", "meta_stats_daily", ["account_id"])
    op.create_index("ix_meta_stats_campaign", "meta_stats_daily", ["campaign_external_id"])
    op.create_index("ix_meta_stats_date", "meta_stats_daily", ["workspace_id", "record_date"])

    _grant_permissions()


def _grant_permissions() -> None:
    """Выдать meta.* существующим ролям.

    Администратор получает оба права, Team Lead и Buyer — только просмотр:
    подключать кабинеты и назначать ответственных должен тот же, кто ведёт
    настройки интеграций.
    """
    bind = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("description", sa.String()),
    )
    roles = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("name", sa.String()))
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )

    permission_ids: dict[str, uuid.UUID] = {}
    for code, description in PERMISSIONS:
        existing = bind.execute(
            sa.select(permissions.c.id).where(permissions.c.code == code)
        ).scalar_one_or_none()
        if existing is None:
            existing = uuid.uuid4()
            bind.execute(
                sa.insert(permissions).values(
                    id=existing, code=code, description=description
                )
            )
        permission_ids[code] = existing

    grants = {
        "Administrator": ["meta.view", "meta.manage"],
        "Team Lead": ["meta.view"],
        "Buyer": ["meta.view"],
    }
    links = []
    for role_name, codes in grants.items():
        role_id = bind.execute(
            sa.select(roles.c.id).where(roles.c.name == role_name)
        ).scalar_one_or_none()
        if role_id is None:
            continue
        for code in codes:
            already = bind.execute(
                sa.select(role_permissions.c.role_id).where(
                    role_permissions.c.role_id == role_id,
                    role_permissions.c.permission_id == permission_ids[code],
                )
            ).first()
            if already is None:
                links.append(
                    {"role_id": role_id, "permission_id": permission_ids[code]}
                )
    if links:
        bind.execute(sa.insert(role_permissions), links)


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE code IN ('meta.view', 'meta.manage'))"
    )
    op.execute("DELETE FROM permissions WHERE code IN ('meta.view', 'meta.manage')")
    op.drop_table("meta_stats_daily")
    op.drop_table("meta_entities")
    op.drop_table("meta_ad_accounts")
    op.drop_column("integration_connections", "attribution_sub_id")
    op.drop_column("integration_connections", "external_account_id")
