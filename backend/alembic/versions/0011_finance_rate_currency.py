"""Валюта переезжает с книги на ставку оффера.

Книга всегда считается в долларах. Оффер может быть в евро — тогда его ставка
переводится по курсу, сохранённому в самой книге. Курс хранится по месяцам, а не
берётся живым: иначе профит и зарплата закрытого месяца менялись бы каждый день.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_finance_rate_currency"
down_revision = "0010_finance_currency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_books",
        sa.Column(
            "eur_usd_rate",
            sa.Numeric(18, 6),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "finance_book_offers",
        sa.Column(
            "rate_currency",
            sa.String(length=3),
            nullable=False,
            server_default="USD",
        ),
    )
    # Месяц, который вели в евро, теперь описывается офферами в евро: валюта
    # книги как таковая больше не существует.
    op.execute(
        "UPDATE finance_book_offers SET rate_currency = 'EUR' WHERE book_id IN "
        "(SELECT id FROM finance_books WHERE currency = 'EUR')"
    )
    op.drop_column("finance_books", "currency")


def downgrade() -> None:
    op.add_column(
        "finance_books",
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default="USD",
        ),
    )
    op.execute(
        "UPDATE finance_books SET currency = 'EUR' WHERE id IN "
        "(SELECT book_id FROM finance_book_offers WHERE rate_currency = 'EUR')"
    )
    op.drop_column("finance_book_offers", "rate_currency")
    op.drop_column("finance_books", "eur_usd_rate")
