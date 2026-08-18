"""Финансы: месячная книга баера вместо построчных записей.

Модуль переписан под то, как команда действительно ведёт финансы — таблица
«показатели × дни месяца» на каждого баера. Всё заполняется руками: спенд,
costs, SOK по офферам. Доход, профит, ROI и зарплата считаются из них.

Старые `finance_records` не трогаются: они больше не участвуют в экране
«Финансы», но выгрузки и история по ним остаются рабочими.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_finance_books"
down_revision = "0007_offer_workflow_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_books",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("buyer_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column(
            "prev_minus", sa.Numeric(18, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "workspace_id", "buyer_id", "year", "month", name="uq_finance_book_period"
        ),
    )
    op.create_index("ix_finance_books_workspace_id", "finance_books", ["workspace_id"])
    op.create_index("ix_finance_books_buyer_id", "finance_books", ["buyer_id"])
    op.create_index(
        "ix_finance_books_period", "finance_books", ["workspace_id", "year", "month"]
    )

    op.create_table(
        "finance_book_offers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "book_id",
            sa.Uuid(),
            sa.ForeignKey("finance_books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("partner", sa.String(160)),
        sa.Column("rate", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.create_index("ix_finance_book_offers_book_id", "finance_book_offers", ["book_id"])

    op.create_table(
        "finance_book_days",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "book_id",
            sa.Uuid(),
            sa.ForeignKey("finance_books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("spend_buyer", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("spend_agent", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("costs", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint("book_id", "day", name="uq_finance_book_day"),
    )
    op.create_index("ix_finance_book_days_book_id", "finance_book_days", ["book_id"])

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
        sa.Column("sok", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint("offer_id", "day", name="uq_finance_offer_day"),
    )
    op.create_index("ix_finance_offer_days_offer_id", "finance_offer_days", ["offer_id"])


def downgrade() -> None:
    op.drop_table("finance_offer_days")
    op.drop_table("finance_book_days")
    op.drop_table("finance_book_offers")
    op.drop_index("ix_finance_books_period", table_name="finance_books")
    op.drop_index("ix_finance_books_buyer_id", table_name="finance_books")
    op.drop_index("ix_finance_books_workspace_id", table_name="finance_books")
    op.drop_table("finance_books")
