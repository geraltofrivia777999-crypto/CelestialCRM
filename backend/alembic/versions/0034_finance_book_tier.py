"""Две книги на месяц: Tier-1 и Tier-2/3.

Спенд по Tier-1 и Tier-2/3 приходит из разных кабинетов и разными суммами.
Пока книга была одна, разделить общий дневной расход между тирами было нечем,
кроме пропорции по доходу, — теперь баер ведёт две таблицы и вводит фактические
числа в каждую.

Существующие книги расщепляются по гео офферов: офферы Tier-2/3 переезжают в
новую книгу своего тира. Затраты остаются в книге Tier-1 — они вводились одной
суммой на день, и разложить их задним числом не по чему. Долг прошлых месяцев
тоже остаётся на Tier-1: он копился по человеку целиком.
"""

import uuid
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "0034_finance_book_tier"
down_revision = "0033_country_tiers"
branch_labels = None
depends_on = None

TIER_1 = "T1"
TIER_23 = "T23"


def upgrade() -> None:
    op.add_column(
        "finance_books",
        sa.Column("tier", sa.String(length=4), nullable=False, server_default=TIER_1),
    )
    op.drop_constraint("uq_finance_book_period", "finance_books", type_="unique")
    op.create_unique_constraint(
        "uq_finance_book_period",
        "finance_books",
        ["workspace_id", "buyer_id", "year", "month", "tier"],
    )
    _split_books_by_offer_tier()


def downgrade() -> None:
    """Книги Tier-2/3 сливаются обратно в книгу того же месяца.

    Офферы переезжают целиком, затраты складываются: обратно они всё равно
    вводились одной строкой на день.
    """
    _merge_tier_books_back()
    op.drop_constraint("uq_finance_book_period", "finance_books", type_="unique")
    op.create_unique_constraint(
        "uq_finance_book_period",
        "finance_books",
        ["workspace_id", "buyer_id", "year", "month"],
    )
    op.drop_column("finance_books", "tier")


def _books_table() -> sa.Table:
    return sa.table(
        "finance_books",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("buyer_id", sa.Uuid()),
        sa.column("year", sa.Integer()),
        sa.column("month", sa.Integer()),
        sa.column("tier", sa.String()),
        sa.column("prev_minus", sa.Numeric(18, 4)),
        sa.column("eur_usd_rate", sa.Numeric(18, 6)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _offers_table() -> sa.Table:
    return sa.table(
        "finance_book_offers",
        sa.column("id", sa.Uuid()),
        sa.column("book_id", sa.Uuid()),
        sa.column("geo", sa.String()),
    )


def _split_books_by_offer_tier() -> None:
    bind = op.get_bind()
    books = _books_table()
    offers = _offers_table()
    tiers = sa.table(
        "country_tiers",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("tier", sa.String()),
    )

    tier_one: dict[uuid.UUID, set[str]] = {}
    for row in bind.execute(sa.select(tiers)).mappings():
        if row["tier"] == TIER_1:
            tier_one.setdefault(row["workspace_id"], set()).add(row["code"])

    for book in bind.execute(sa.select(books)).mappings():
        codes = tier_one.get(book["workspace_id"], set())
        moving = [
            row["id"]
            for row in bind.execute(
                sa.select(offers).where(offers.c.book_id == book["id"])
            ).mappings()
            # Без гео тир неизвестен — такой оффер остаётся там, где лежал.
            if row["geo"] and row["geo"] not in codes
        ]
        if not moving:
            continue
        target = uuid.uuid4()
        bind.execute(
            sa.insert(books).values(
                id=target,
                workspace_id=book["workspace_id"],
                buyer_id=book["buyer_id"],
                year=book["year"],
                month=book["month"],
                tier=TIER_23,
                prev_minus=Decimal("0"),
                eur_usd_rate=book["eur_usd_rate"],
                created_at=book["created_at"],
                updated_at=book["updated_at"],
            )
        )
        bind.execute(
            sa.update(offers).where(offers.c.id.in_(moving)).values(book_id=target)
        )


def _merge_tier_books_back() -> None:
    bind = op.get_bind()
    books = _books_table()
    offers = _offers_table()
    days = sa.table(
        "finance_book_days",
        sa.column("id", sa.Uuid()),
        sa.column("book_id", sa.Uuid()),
        sa.column("day", sa.Integer()),
        sa.column("spend_buyer", sa.Numeric(18, 4)),
        sa.column("spend_agent", sa.Numeric(18, 4)),
        sa.column("costs", sa.Numeric(18, 4)),
    )
    zero = Decimal("0")

    rows = list(bind.execute(sa.select(books)).mappings())
    primary = {
        (row["workspace_id"], row["buyer_id"], row["year"], row["month"]): row["id"]
        for row in rows
        if row["tier"] == TIER_1
    }
    for row in rows:
        if row["tier"] == TIER_1:
            continue
        key = (row["workspace_id"], row["buyer_id"], row["year"], row["month"])
        target = primary.get(key)
        if target is None:
            # Второй книги месяца нет — эта и станет единственной.
            bind.execute(
                sa.update(books).where(books.c.id == row["id"]).values(tier=TIER_1)
            )
            primary[key] = row["id"]
            continue
        bind.execute(
            sa.update(offers).where(offers.c.book_id == row["id"]).values(book_id=target)
        )
        for day_row in bind.execute(
            sa.select(days).where(days.c.book_id == row["id"])
        ).mappings():
            existing = (
                bind.execute(
                    sa.select(days).where(
                        days.c.book_id == target, days.c.day == day_row["day"]
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                bind.execute(
                    sa.update(days)
                    .where(days.c.id == existing["id"])
                    .values(
                        spend_buyer=(existing["spend_buyer"] or zero)
                        + (day_row["spend_buyer"] or zero),
                        spend_agent=(existing["spend_agent"] or zero)
                        + (day_row["spend_agent"] or zero),
                        costs=(existing["costs"] or zero) + (day_row["costs"] or zero),
                    )
                )
            else:
                bind.execute(
                    sa.update(days)
                    .where(days.c.id == day_row["id"])
                    .values(book_id=target)
                )
        bind.execute(sa.delete(days).where(days.c.book_id == row["id"]))
        bind.execute(sa.delete(books).where(books.c.id == row["id"]))
