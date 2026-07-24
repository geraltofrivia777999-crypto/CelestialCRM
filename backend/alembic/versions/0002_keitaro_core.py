"""Keitaro synchronization core.

The initial migration creates metadata dynamically, so this migration checks
the live schema before changing it. That keeps both existing and fresh
installations safe.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_keitaro_core"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "integration_connections" in tables:
        columns = _columns(inspector, "integration_connections")
        if "timezone" not in columns:
            op.add_column(
                "integration_connections",
                sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
            )
        if "buyer_sub_id" not in columns:
            op.add_column(
                "integration_connections",
                sa.Column("buyer_sub_id", sa.Integer(), nullable=False, server_default="1"),
            )
        if "lookback_days" not in columns:
            op.add_column(
                "integration_connections",
                sa.Column("lookback_days", sa.Integer(), nullable=False, server_default="2"),
            )

    if "keitaro_stats_daily" in tables:
        columns = _columns(inspector, "keitaro_stats_daily")
        additions = {
            "campaign_external_id": sa.Column("campaign_external_id", sa.String(100)),
            "country_code": sa.Column("country_code", sa.String(12)),
            "leads": sa.Column("leads", sa.Integer(), nullable=False, server_default="0"),
            "sales": sa.Column("sales", sa.Integer(), nullable=False, server_default="0"),
            "rejected": sa.Column("rejected", sa.Integer(), nullable=False, server_default="0"),
            "cost": sa.Column(
                "cost", sa.Numeric(18, 4), nullable=False, server_default="0"
            ),
        }
        for name, column in additions.items():
            if name not in columns:
                op.add_column("keitaro_stats_daily", column)

    if "keitaro_groups" not in tables:
        op.create_table(
            "keitaro_groups",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("connection_id", sa.Uuid(), nullable=False),
            sa.Column("resource_type", sa.String(30), nullable=False),
            sa.Column("external_id", sa.String(100), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["connection_id"], ["integration_connections.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "connection_id",
                "resource_type",
                "external_id",
                name="uq_keitaro_group_external",
            ),
        )
        op.create_index("ix_keitaro_groups_workspace_id", "keitaro_groups", ["workspace_id"])
        op.create_index(
            "ix_keitaro_groups_connection_id", "keitaro_groups", ["connection_id"]
        )
        op.create_index(
            "ix_keitaro_groups_resource_type", "keitaro_groups", ["resource_type"]
        )

    if "keitaro_campaigns" not in tables:
        status_enum = (
            postgresql.ENUM(
                "active",
                "inactive",
                "blocked",
                name="status",
                create_type=False,
            )
            if bind.dialect.name == "postgresql"
            else sa.Enum("active", "inactive", "blocked", name="status")
        )
        op.create_table(
            "keitaro_campaigns",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("connection_id", sa.Uuid(), nullable=False),
            sa.Column("external_id", sa.String(100), nullable=False),
            sa.Column("name", sa.String(240), nullable=False),
            sa.Column("group_external_id", sa.String(100)),
            sa.Column("group_name", sa.String(200)),
            sa.Column("traffic_source_external_id", sa.String(100)),
            sa.Column("cost_type", sa.String(30)),
            sa.Column(
                "status",
                status_enum,
                nullable=False,
                server_default="active",
            ),
            sa.Column("external_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["connection_id"], ["integration_connections.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "connection_id", "external_id", name="uq_keitaro_campaign_external"
            ),
        )
        op.create_index(
            "ix_keitaro_campaigns_workspace_id", "keitaro_campaigns", ["workspace_id"]
        )
        op.create_index(
            "ix_keitaro_campaigns_connection_id", "keitaro_campaigns", ["connection_id"]
        )
        op.create_index("ix_keitaro_campaigns_status", "keitaro_campaigns", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "keitaro_campaigns" in tables:
        op.drop_table("keitaro_campaigns")
    if "keitaro_groups" in tables:
        op.drop_table("keitaro_groups")
