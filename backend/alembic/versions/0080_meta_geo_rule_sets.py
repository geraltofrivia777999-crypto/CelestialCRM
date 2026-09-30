"""Named GEO auto-rules: settings and GEO rows move inside a rule set.

Existing per-workspace settings and GEO rows become one rule set called
«Автоправило 1», so nothing configured before this migration is lost.

Revision ID: 0080_meta_geo_rule_sets
Revises: 0079_meta_geo_rules
"""

import json
import uuid

import sqlalchemy as sa

from alembic import op

revision = "0080_meta_geo_rule_sets"
down_revision = "0079_meta_geo_rules"
branch_labels = None
depends_on = None

FIRST_NAME = "Автоправило 1"


def upgrade() -> None:
    op.create_table(
        "meta_geo_rule_sets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("level", sa.String(10), server_default="campaign", nullable=False),
        sa.Column("interval_minutes", sa.Integer(), server_default="30", nullable=False),
        sa.Column("auto_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_meta_geo_rule_set_name"),
    )
    op.create_index("ix_meta_geo_rule_sets_workspace_id", "meta_geo_rule_sets", ["workspace_id"])
    op.add_column("meta_geo_rules", sa.Column(
        "rule_set_id", sa.Uuid(), sa.ForeignKey("meta_geo_rule_sets.id", ondelete="CASCADE"),
    ))
    op.add_column("meta_geo_rule_events", sa.Column(
        "rule_set_id", sa.Uuid(), sa.ForeignKey("meta_geo_rule_sets.id", ondelete="SET NULL"),
    ))
    op.add_column("meta_geo_rule_events", sa.Column("rule_name", sa.String(160)))

    bind = op.get_bind()
    settings = {
        row.workspace_id: row for row in bind.execute(sa.text(
            "SELECT workspace_id, level, interval_minutes, auto_enabled, last_run_at, "
            "last_run_result FROM meta_geo_rule_settings"
        ))
    }
    workspaces = set(settings) | {
        row.workspace_id for row in bind.execute(
            sa.text("SELECT DISTINCT workspace_id FROM meta_geo_rules")
        )
    }
    sets = sa.table(
        "meta_geo_rule_sets",
        sa.column("id", sa.Uuid()), sa.column("workspace_id", sa.Uuid()),
        sa.column("name", sa.String()), sa.column("level", sa.String()),
        sa.column("interval_minutes", sa.Integer()), sa.column("auto_enabled", sa.Boolean()),
        sa.column("last_run_at", sa.DateTime(timezone=True)),
        sa.column("last_run_result", sa.JSON()),
    )
    for workspace_id in workspaces:
        config = settings.get(workspace_id)
        set_id = uuid.uuid4()
        bind.execute(sets.insert().values(
            id=set_id, workspace_id=workspace_id, name=FIRST_NAME,
            level=config.level if config else "campaign",
            interval_minutes=config.interval_minutes if config else 30,
            auto_enabled=config.auto_enabled if config else False,
            last_run_at=config.last_run_at if config else None,
            last_run_result=(config.last_run_result or {}) if config else {},
        ))
        bind.execute(
            sa.text("UPDATE meta_geo_rules SET rule_set_id = :set_id WHERE workspace_id = :ws"),
            {"set_id": set_id, "ws": workspace_id},
        )
        bind.execute(
            sa.text(
                "UPDATE meta_geo_rule_events SET rule_set_id = :set_id, rule_name = :name "
                "WHERE workspace_id = :ws"
            ),
            {"set_id": set_id, "name": FIRST_NAME, "ws": workspace_id},
        )

    op.alter_column("meta_geo_rules", "rule_set_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_constraint("uq_meta_geo_rule_country", "meta_geo_rules", type_="unique")
    op.create_unique_constraint(
        "uq_meta_geo_rule_set_country", "meta_geo_rules", ["rule_set_id", "country_code"]
    )
    op.create_index("ix_meta_geo_rules_rule_set_id", "meta_geo_rules", ["rule_set_id"])
    op.create_index(
        "ix_meta_geo_rule_events_rule_set_id", "meta_geo_rule_events", ["rule_set_id"]
    )
    op.drop_table("meta_geo_rule_settings")


def downgrade() -> None:
    op.create_table(
        "meta_geo_rule_settings",
        sa.Column("workspace_id", sa.Uuid(),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("level", sa.String(10), server_default="campaign", nullable=False),
        sa.Column("interval_minutes", sa.Integer(), server_default="30", nullable=False),
        sa.Column("auto_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    bind = op.get_bind()
    # До этой миграции на воркспейс было одно правило: оставляем самое раннее.
    first_sets = list(bind.execute(sa.text(
        "SELECT DISTINCT ON (workspace_id) id, workspace_id, level, interval_minutes, "
        "auto_enabled, last_run_at, last_run_result FROM meta_geo_rule_sets "
        "ORDER BY workspace_id, created_at"
    )))
    keep = [row.id for row in first_sets]
    for row in first_sets:
        bind.execute(
            sa.text(
                "INSERT INTO meta_geo_rule_settings (workspace_id, level, interval_minutes, "
                "auto_enabled, last_run_at, last_run_result) VALUES (:ws, :level, :interval, "
                ":auto, :last, CAST(:result AS JSON))"
            ),
            {"ws": row.workspace_id, "level": row.level, "interval": row.interval_minutes,
             "auto": row.auto_enabled, "last": row.last_run_at,
             "result": json.dumps(row.last_run_result or {})},
        )
    if keep:
        bind.execute(
            sa.text("DELETE FROM meta_geo_rules WHERE rule_set_id <> ALL(:keep)"),
            {"keep": keep},
        )
    else:
        bind.execute(sa.text("DELETE FROM meta_geo_rules"))
    op.drop_index("ix_meta_geo_rule_events_rule_set_id", table_name="meta_geo_rule_events")
    op.drop_index("ix_meta_geo_rules_rule_set_id", table_name="meta_geo_rules")
    op.drop_constraint("uq_meta_geo_rule_set_country", "meta_geo_rules", type_="unique")
    op.create_unique_constraint(
        "uq_meta_geo_rule_country", "meta_geo_rules", ["workspace_id", "country_code"]
    )
    op.drop_column("meta_geo_rule_events", "rule_name")
    op.drop_column("meta_geo_rule_events", "rule_set_id")
    op.drop_column("meta_geo_rules", "rule_set_id")
    op.drop_index("ix_meta_geo_rule_sets_workspace_id", table_name="meta_geo_rule_sets")
    op.drop_table("meta_geo_rule_sets")
