"""Task-local fields and finance offer locks.

Revision ID: 0070_task_fields_finance_locks
Revises: 0069_role_finance_summaries
"""

import sqlalchemy as sa
from alembic import op

revision = "0070_task_fields_finance_locks"
down_revision = "0069_role_finance_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("field_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "finance_book_offers",
        sa.Column("locked_fields", sa.JSON(), nullable=False, server_default="[]"),
    )
    # Существующие связанные строки могли быть уточнены вручную. На первом
    # деплое защищаем их консервативно; новые строки приезжают без замка.
    op.execute(
        "UPDATE finance_book_offers "
        "SET locked_fields = '[\"name\",\"partner\",\"geo\",\"rate\",\"rate_currency\"]' "
        "WHERE source_offer_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("finance_book_offers", "locked_fields")
    op.drop_column("tasks", "field_ids")
