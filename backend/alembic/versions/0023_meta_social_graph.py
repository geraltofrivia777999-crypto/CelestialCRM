"""Социальные аккаунты, фан-пейджи и бизнес-менеджеры Meta.

Обзор Meta Ads собирается по восьми уровням, а в базе жили только кабинеты и
кампании. Здесь появляются три недостающих: социальный аккаунт (владелец
токена), бизнес-менеджеры на нём и фан-пейджи, от лица которых крутится реклама.

Кабинет получает ссылки на БМ и на социальный аккаунт — кабинет бывает и
«личным», без БМа. Объявление получает `page_external_id`: уровень фан-пейджей
собирается именно по нему. Дневная статистика — `link_clicks`, потому что общие
клики Meta включают лайки и развороты текста, а мерят трафик по ссылке.
"""

import sqlalchemy as sa

from alembic import op

revision = "0023_meta_social_graph"
down_revision = "0022_manual_offers"
branch_labels = None
depends_on = None


def _status_enum(bind) -> sa.types.TypeEngine:
    """Тип `status` уже создан первой миграцией — переиспользуем, а не создаём."""
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects import postgresql

        return postgresql.ENUM(
            "active", "inactive", "blocked", name="status", create_type=False
        )
    return sa.Enum("active", "inactive", "blocked", name="status")


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "meta_social_accounts" not in tables:
        op.create_table(
            "meta_social_accounts",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("connection_id", sa.Uuid(), nullable=False),
            sa.Column("external_id", sa.String(length=100), nullable=False),
            sa.Column("name", sa.String(length=240), nullable=False),
            sa.Column("owner_id", sa.Uuid(), nullable=True),
            sa.Column(
                "status",
                _status_enum(bind),
                nullable=False,
                server_default="active",
            ),
            sa.Column("external_payload", sa.JSON(), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["connection_id"], ["integration_connections.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "connection_id", "external_id", name="uq_meta_social_external"
            ),
        )
        op.create_index(
            "ix_meta_social_accounts_workspace_id", "meta_social_accounts", ["workspace_id"]
        )
        op.create_index(
            "ix_meta_social_accounts_connection_id",
            "meta_social_accounts",
            ["connection_id"],
        )
        op.create_index(
            "ix_meta_social_accounts_owner_id", "meta_social_accounts", ["owner_id"]
        )
        op.create_index("ix_meta_social_accounts_status", "meta_social_accounts", ["status"])

    if "meta_businesses" not in tables:
        op.create_table(
            "meta_businesses",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("connection_id", sa.Uuid(), nullable=False),
            sa.Column("social_account_id", sa.Uuid(), nullable=True),
            sa.Column("external_id", sa.String(length=100), nullable=False),
            sa.Column("name", sa.String(length=240), nullable=False),
            sa.Column("verification_status", sa.String(length=60), nullable=True),
            sa.Column("external_payload", sa.JSON(), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["connection_id"], ["integration_connections.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["social_account_id"], ["meta_social_accounts.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "connection_id", "external_id", name="uq_meta_business_external"
            ),
        )
        op.create_index("ix_meta_businesses_workspace_id", "meta_businesses", ["workspace_id"])
        op.create_index(
            "ix_meta_businesses_connection_id", "meta_businesses", ["connection_id"]
        )
        op.create_index(
            "ix_meta_businesses_social_account_id", "meta_businesses", ["social_account_id"]
        )

    if "meta_fan_pages" not in tables:
        op.create_table(
            "meta_fan_pages",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("connection_id", sa.Uuid(), nullable=False),
            sa.Column("social_account_id", sa.Uuid(), nullable=True),
            sa.Column("business_id", sa.Uuid(), nullable=True),
            sa.Column("external_id", sa.String(length=100), nullable=False),
            sa.Column("name", sa.String(length=240), nullable=False),
            sa.Column("category", sa.String(length=120), nullable=True),
            sa.Column("external_payload", sa.JSON(), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["connection_id"], ["integration_connections.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["social_account_id"], ["meta_social_accounts.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["business_id"], ["meta_businesses.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "connection_id", "external_id", name="uq_meta_page_external"
            ),
        )
        op.create_index("ix_meta_fan_pages_workspace_id", "meta_fan_pages", ["workspace_id"])
        op.create_index("ix_meta_fan_pages_connection_id", "meta_fan_pages", ["connection_id"])
        op.create_index(
            "ix_meta_fan_pages_social_account_id", "meta_fan_pages", ["social_account_id"]
        )
        op.create_index("ix_meta_fan_pages_business_id", "meta_fan_pages", ["business_id"])

    account_columns = _columns(inspector, "meta_ad_accounts")
    if "business_id" not in account_columns:
        op.add_column("meta_ad_accounts", sa.Column("business_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_meta_account_business",
            "meta_ad_accounts",
            "meta_businesses",
            ["business_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_meta_ad_accounts_business_id", "meta_ad_accounts", ["business_id"]
        )
    if "social_account_id" not in account_columns:
        op.add_column(
            "meta_ad_accounts", sa.Column("social_account_id", sa.Uuid(), nullable=True)
        )
        op.create_foreign_key(
            "fk_meta_account_social",
            "meta_ad_accounts",
            "meta_social_accounts",
            ["social_account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_meta_ad_accounts_social_account_id",
            "meta_ad_accounts",
            ["social_account_id"],
        )

    if "page_external_id" not in _columns(inspector, "meta_entities"):
        op.add_column(
            "meta_entities", sa.Column("page_external_id", sa.String(length=100), nullable=True)
        )
        op.create_index(
            "ix_meta_entities_page_external_id", "meta_entities", ["page_external_id"]
        )

    if "link_clicks" not in _columns(inspector, "meta_stats_daily"):
        op.add_column(
            "meta_stats_daily",
            sa.Column("link_clicks", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "link_clicks" in _columns(inspector, "meta_stats_daily"):
        op.drop_column("meta_stats_daily", "link_clicks")
    if "page_external_id" in _columns(inspector, "meta_entities"):
        op.drop_index("ix_meta_entities_page_external_id", table_name="meta_entities")
        op.drop_column("meta_entities", "page_external_id")

    account_columns = _columns(inspector, "meta_ad_accounts")
    if "social_account_id" in account_columns:
        op.drop_constraint("fk_meta_account_social", "meta_ad_accounts", type_="foreignkey")
        op.drop_index("ix_meta_ad_accounts_social_account_id", table_name="meta_ad_accounts")
        op.drop_column("meta_ad_accounts", "social_account_id")
    if "business_id" in account_columns:
        op.drop_constraint("fk_meta_account_business", "meta_ad_accounts", type_="foreignkey")
        op.drop_index("ix_meta_ad_accounts_business_id", table_name="meta_ad_accounts")
        op.drop_column("meta_ad_accounts", "business_id")

    for table in ("meta_fan_pages", "meta_businesses", "meta_social_accounts"):
        if table in tables:
            op.drop_table(table)
