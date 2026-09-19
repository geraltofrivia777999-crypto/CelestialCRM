"""Separate media spend from explicit manual overrides; preserve legacy amounts."""

import sqlalchemy as sa

from alembic import op

revision = "0062_finance_spend_source"
down_revision = "0061_recruitment_card_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("finance_book_days", sa.Column("media_spend", sa.Numeric(18, 4), nullable=True))
    op.add_column("finance_book_days", sa.Column("manual_spend", sa.Numeric(18, 4), nullable=True))
    # The old schema has no provenance. Do not guess whether money was imported
    # or entered by a person; retain it as an explicit override until cleared.
    op.execute("UPDATE finance_book_days SET manual_spend = spend_buyer WHERE spend_buyer <> 0")


def downgrade() -> None:
    op.drop_column("finance_book_days", "manual_spend")
    op.drop_column("finance_book_days", "media_spend")
