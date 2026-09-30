"""GEO auto-rules for MetaAds v2: thresholds, settings and trigger history.

Revision ID: 0079_meta_geo_rules
Revises: 0078_sync_run_heartbeat
"""

import sqlalchemy as sa

from alembic import op

revision = "0079_meta_geo_rules"
down_revision = "0078_sync_run_heartbeat"
branch_labels = None
depends_on = None

THRESHOLDS = (
    "no_clicks", "no_insts", "no_regs", "no_deps", "max_avg_inst", "max_avg_reg", "max_avg_dep",
)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "meta_geo_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        *[sa.Column(name, sa.Numeric(12, 2)) for name in THRESHOLDS],
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "country_code", name="uq_meta_geo_rule_country"),
    )
    op.create_index("ix_meta_geo_rules_workspace_id", "meta_geo_rules", ["workspace_id"])
    op.create_table(
        "meta_geo_rule_settings",
        sa.Column("workspace_id", sa.Uuid(),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("level", sa.String(10), server_default="campaign", nullable=False),
        sa.Column("interval_minutes", sa.Integer(), server_default="30", nullable=False),
        sa.Column("auto_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_result", sa.JSON(), nullable=False, server_default="{}"),
        *_timestamps(),
    )
    op.create_table(
        "meta_geo_rule_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("account_external_id", sa.String(100)),
        sa.Column("account_name", sa.String(240)),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.Text()),
        *_timestamps(),
    )
    op.create_index(
        "ix_meta_geo_rule_events_ws_created", "meta_geo_rule_events", ["workspace_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_meta_geo_rule_events_ws_created", table_name="meta_geo_rule_events")
    op.drop_table("meta_geo_rule_events")
    op.drop_table("meta_geo_rule_settings")
    op.drop_index("ix_meta_geo_rules_workspace_id", table_name="meta_geo_rules")
    op.drop_table("meta_geo_rules")
