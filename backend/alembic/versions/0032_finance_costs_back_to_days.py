"""Затраты возвращаются на день книги.

Построчный ввод расхода внутри каждого оффера команда не приняла: спенд
приходит из кабинета одной суммой за день, и разносить его по офферам руками
дороже, чем получаемая точность. В разрезе тиров расход теперь распределяется
по доле дохода дня — это делает `finance_books.totals`.

Тир у оффера остаётся: доход по тирам считается точно, без допущений.
"""

import uuid
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "0032_finance_costs_back_to_days"
down_revision = "0031_finance_summaries"
branch_labels = None
depends_on = None

ZERO = Decimal("0")


def upgrade() -> None:
    _merge_offer_days_into_book_days()
    op.drop_index("ix_finance_offer_days_offer_id", table_name="finance_offer_days")
    op.drop_table("finance_offer_days")


def downgrade() -> None:
    """Таблица возвращается пустой.

    Затраты уже слиты в дни книги, и разложить их обратно по офферам нечем:
    исходное распределение не сохранилось. Скопировать их во второе место
    значило бы удвоить расход в итогах.
    """
    op.create_table(
        "finance_offer_days",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "offer_id",
            sa.Uuid(),
            sa.ForeignKey("finance_book_offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("spend_buyer", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("costs", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint("offer_id", "day", name="uq_finance_offer_day"),
    )
    op.create_index(
        "ix_finance_offer_days_offer_id", "finance_offer_days", ["offer_id"]
    )


def _merge_offer_days_into_book_days() -> None:
    """Сложить затраты офферов в строку дня книги, ничего не потеряв."""
    bind = op.get_bind()
    offers = sa.table(
        "finance_book_offers",
        sa.column("id", sa.Uuid()),
        sa.column("book_id", sa.Uuid()),
    )
    offer_days = sa.table(
        "finance_offer_days",
        sa.column("offer_id", sa.Uuid()),
        sa.column("day", sa.Integer()),
        sa.column("spend_buyer", sa.Numeric(18, 4)),
        sa.column("costs", sa.Numeric(18, 4)),
    )
    book_days = sa.table(
        "finance_book_days",
        sa.column("id", sa.Uuid()),
        sa.column("book_id", sa.Uuid()),
        sa.column("day", sa.Integer()),
        sa.column("spend_buyer", sa.Numeric(18, 4)),
        sa.column("spend_agent", sa.Numeric(18, 4)),
        sa.column("costs", sa.Numeric(18, 4)),
    )

    sums: dict[tuple[uuid.UUID, int], dict[str, Decimal]] = {}
    query = sa.select(
        offers.c.book_id,
        offer_days.c.day,
        offer_days.c.spend_buyer,
        offer_days.c.costs,
    ).select_from(offer_days.join(offers, offers.c.id == offer_days.c.offer_id))
    for row in bind.execute(query).mappings():
        total = sums.setdefault(
            (row["book_id"], row["day"]), {"spend_buyer": ZERO, "costs": ZERO}
        )
        total["spend_buyer"] += row["spend_buyer"] or ZERO
        total["costs"] += row["costs"] or ZERO

    for (book_id, day), total in sums.items():
        existing = (
            bind.execute(
                sa.select(book_days).where(
                    book_days.c.book_id == book_id, book_days.c.day == day
                )
            )
            .mappings()
            .first()
        )
        if existing:
            bind.execute(
                sa.update(book_days)
                .where(book_days.c.id == existing["id"])
                .values(
                    spend_buyer=(existing["spend_buyer"] or ZERO) + total["spend_buyer"],
                    costs=(existing["costs"] or ZERO) + total["costs"],
                )
            )
        else:
            bind.execute(
                sa.insert(book_days).values(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    day=day,
                    spend_buyer=total["spend_buyer"],
                    spend_agent=ZERO,
                    costs=total["costs"],
                )
            )
