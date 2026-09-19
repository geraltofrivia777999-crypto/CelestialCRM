"""Role finance summaries switch

Revision ID: 0069_role_finance_summaries
Revises: 0068_utilities_tab_permissions
"""

import sqlalchemy as sa
from alembic import op

revision = "0069_role_finance_summaries"
down_revision = "0068_utilities_tab_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "show_finance_summaries", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    # До этого сводки скрывались у роли «Только свои данные». Сохраняем то же
    # поведение: переключатель у таких ролей выключен.
    op.execute("UPDATE roles SET show_finance_summaries = false WHERE data_scope = 'own'")


def downgrade() -> None:
    op.drop_column("roles", "show_finance_summaries")
