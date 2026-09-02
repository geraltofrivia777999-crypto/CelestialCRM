"""Расширенный режим залива: кампании, цель, бюджет, правила и теги.

Dolphin-подобный функционал мастера: несколько кампаний в одном заливе,
переопределение цели и блока «Бюджет и ставка» поверх связки, привязка
автоправил к созданным объектам и навешивание adlabels (тегов) на объекты
созданного залива.
"""

import sqlalchemy as sa

from alembic import op

revision = "0043_launch_advanced"
down_revision = "0042_launch_dsa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meta_launches", sa.Column("objective", sa.String(length=40), nullable=True))
    op.add_column(
        "meta_launches",
        sa.Column("campaign_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "meta_launches", sa.Column("budget_kind", sa.String(length=10), nullable=True)
    )
    op.add_column(
        "meta_launches", sa.Column("budget_level", sa.String(length=10), nullable=True)
    )
    op.add_column(
        "meta_launches",
        sa.Column("budget_randomize", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "meta_launches",
        sa.Column("adset_budget_limit", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "meta_launches", sa.Column("bid_strategy", sa.String(length=40), nullable=True)
    )
    op.add_column("meta_launches", sa.Column("rule_ids", sa.JSON(), nullable=True))
    op.add_column("meta_launches", sa.Column("tags", sa.JSON(), nullable=True))


def downgrade() -> None:
    for column in (
        "tags",
        "rule_ids",
        "bid_strategy",
        "adset_budget_limit",
        "budget_randomize",
        "budget_level",
        "budget_kind",
        "campaign_count",
        "objective",
    ):
        op.drop_column("meta_launches", column)
