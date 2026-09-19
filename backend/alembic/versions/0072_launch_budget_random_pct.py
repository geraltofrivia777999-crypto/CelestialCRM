"""Budget randomization percent on launches.

Revision ID: 0072_launch_budget_random_pct
Revises: 0071_meta_template_owner_name

Разброс бюджета задают процентом (±N %), как в Dolphin. Пусто — прежние ±10 %.
"""

import sqlalchemy as sa
from alembic import op

revision = "0072_launch_budget_random_pct"
down_revision = "0071_meta_template_owner_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meta_launches", sa.Column("budget_randomize_pct", sa.Numeric(5, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("meta_launches", "budget_randomize_pct")
