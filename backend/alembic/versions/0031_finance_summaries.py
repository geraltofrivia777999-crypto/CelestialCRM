"""Тиры и затраты офферов для финансовых сводок.

Общий дневной спенд нельзя делить между тирами без выдуманной пропорции,
поэтому новые затраты живут на оффере. Старые числа переносятся только там,
где соответствие однозначно: в книге с одним оффером.
"""

import uuid
from collections import defaultdict
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "0031_finance_summaries"
down_revision = "0030_alert_deposit_conditions"
branch_labels = None
depends_on = None

ZERO = Decimal("0")


def upgrade() -> None:
    op.add_column(
        "finance_book_offers", sa.Column("tier", sa.String(length=4), nullable=True)
    )
    op.add_column("users", sa.Column("team_name", sa.String(length=120), nullable=True))
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
        sa.Column(
            "spend_buyer", sa.Numeric(18, 4), nullable=False, server_default="0"
        ),
        sa.Column("costs", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint("offer_id", "day", name="uq_finance_offer_day"),
    )
    op.create_index(
        "ix_finance_offer_days_offer_id", "finance_offer_days", ["offer_id"]
    )
    _move_unambiguous_days()


def _move_unambiguous_days() -> None:
    bind = op.get_bind()
    offers = sa.table(
        "finance_book_offers",
        sa.column("id", sa.Uuid()),
        sa.column("book_id", sa.Uuid()),
    )
    days = sa.table(
        "finance_book_days",
        sa.column("id", sa.Uuid()),
        sa.column("book_id", sa.Uuid()),
        sa.column("day", sa.Integer()),
        sa.column("spend_buyer", sa.Numeric(18, 4)),
        sa.column("costs", sa.Numeric(18, 4)),
    )
    offer_days = _offer_days_table()

    by_book: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for offer_id, book_id in bind.execute(sa.select(offers.c.id, offers.c.book_id)):
        by_book[book_id].append(offer_id)

    moved_ids = []
    rows = []
    for row in bind.execute(sa.select(days)).mappings():
        book_offers = by_book.get(row["book_id"], [])
        if len(book_offers) != 1:
            continue
        spend = row["spend_buyer"] or ZERO
        costs = row["costs"] or ZERO
        if spend or costs:
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "offer_id": book_offers[0],
                    "day": row["day"],
                    "spend_buyer": spend,
                    "costs": costs,
                }
            )
        moved_ids.append(row["id"])
    if rows:
        op.bulk_insert(offer_days, rows)
    if moved_ids:
        bind.execute(
            sa.update(days)
            .where(days.c.id.in_(moved_ids))
            .values(spend_buyer=ZERO, costs=ZERO)
        )


def downgrade() -> None:
    _merge_offer_days_back()
    op.drop_index("ix_finance_offer_days_offer_id", table_name="finance_offer_days")
    op.drop_table("finance_offer_days")
    op.drop_column("users", "team_name")
    op.drop_column("finance_book_offers", "tier")


def _merge_offer_days_back() -> None:
    bind = op.get_bind()
    offers = sa.table(
        "finance_book_offers",
        sa.column("id", sa.Uuid()),
        sa.column("book_id", sa.Uuid()),
    )
    offer_days = _offer_days_table()
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
    query = (
        sa.select(
            offers.c.book_id,
            offer_days.c.day,
            offer_days.c.spend_buyer,
            offer_days.c.costs,
        )
        .select_from(offer_days.join(offers, offers.c.id == offer_days.c.offer_id))
    )
    for row in bind.execute(query).mappings():
        key = (row["book_id"], row["day"])
        total = sums.setdefault(key, {"spend_buyer": ZERO, "costs": ZERO})
        total["spend_buyer"] += row["spend_buyer"] or ZERO
        total["costs"] += row["costs"] or ZERO

    for (book_id, day), total in sums.items():
        existing = bind.execute(
            sa.select(book_days).where(
                book_days.c.book_id == book_id, book_days.c.day == day
            )
        ).mappings().first()
        if existing:
            bind.execute(
                sa.update(book_days)
                .where(book_days.c.id == existing["id"])
                .values(
                    spend_buyer=(existing["spend_buyer"] or ZERO)
                    + total["spend_buyer"],
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


def _offer_days_table() -> sa.Table:
    return sa.table(
        "finance_offer_days",
        sa.column("id", sa.Uuid()),
        sa.column("offer_id", sa.Uuid()),
        sa.column("day", sa.Integer()),
        sa.column("spend_buyer", sa.Numeric(18, 4)),
        sa.column("costs", sa.Numeric(18, 4)),
    )
