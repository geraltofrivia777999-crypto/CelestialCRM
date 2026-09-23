"""Allow manually created partners without a Keitaro connection.

Revision ID: 0076_manual_partners
Revises: 0075_user_finance_tags
"""

import sqlalchemy as sa

from alembic import op

revision = "0076_manual_partners"
down_revision = "0075_user_finance_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "partners", "connection_id", existing_type=sa.Uuid(), nullable=True
    )


def downgrade() -> None:
    manual_count = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM partners WHERE connection_id IS NULL")
    )
    if manual_count:
        raise RuntimeError("Remove or link manual partners before downgrading")
    op.alter_column(
        "partners", "connection_id", existing_type=sa.Uuid(), nullable=False
    )
