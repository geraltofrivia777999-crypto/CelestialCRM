"""Валюта книги: доллар или евро.

Хранится по месяцам. Смена валюты в текущем месяце не переписывает закрытые —
новый месяц просто наследует валюту предыдущего.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_finance_currency"
down_revision = "0009_finance_offer_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_books",
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default="USD",
        ),
    )


def downgrade() -> None:
    op.drop_column("finance_books", "currency")
