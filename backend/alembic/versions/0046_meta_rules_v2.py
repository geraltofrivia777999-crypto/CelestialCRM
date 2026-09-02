"""Автоправила v2: группы, расписание, scope, конвертация, действия.

«Правила FB» — один вариант типа (по расписанию) с тремя частотами (постоянно,
каждую полночь, своё расписание по дням недели и интервалам времени); scope —
весь кабинет или только кампания; конвертация валют; действия с бюджетом и
ставкой получили режим сумма/процент, знак и максимум. Группы правил — отдельная
сущность, правило может входить в группу.
"""

import sqlalchemy as sa

from alembic import op

revision = "0046_meta_rules_v2"
down_revision = "0044_launch_advanced_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meta_rule_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), index=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.add_column("meta_rules", sa.Column("group_id", sa.Uuid(), sa.ForeignKey("meta_rule_groups.id", ondelete="SET NULL"), index=True))
    op.add_column("meta_rules", sa.Column("schedule_kind", sa.String(length=12), nullable=False, server_default="always"))
    op.add_column("meta_rules", sa.Column("schedule", sa.JSON(), nullable=True))
    op.add_column("meta_rules", sa.Column("convert_currency", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("meta_rules", sa.Column("currency", sa.String(length=8), nullable=True))
    op.add_column("meta_rules", sa.Column("scope_kind", sa.String(length=10), nullable=False, server_default="cabinet"))
    op.add_column("meta_rules", sa.Column("campaign_external_id", sa.String(length=100), nullable=True))
    op.add_column("meta_rules", sa.Column("action_sign", sa.String(length=6), nullable=False, server_default="plus"))
    op.add_column("meta_rules", sa.Column("action_mode", sa.String(length=8), nullable=False, server_default="pct"))
    op.add_column("meta_rules", sa.Column("action_max", sa.Numeric(18, 2), nullable=True))
    op.add_column("meta_rules", sa.Column("budget_kind", sa.String(length=10), nullable=False, server_default="daily"))


def downgrade() -> None:
    for column in (
        "budget_kind", "action_max", "action_mode", "action_sign",
        "campaign_external_id", "scope_kind", "currency", "convert_currency",
        "schedule", "schedule_kind", "group_id",
    ):
        op.drop_column("meta_rules", column)
    op.drop_table("meta_rule_groups")
