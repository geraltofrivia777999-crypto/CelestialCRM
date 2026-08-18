"""CPA у оффера и ссылка строки книги на справочник.

Назначение баера тянет оффер в его Финансы. Ставку берём из справочника, а по
`source_offer_id` повторное назначение узнаёт свою строку и обновляет её вместо
того, чтобы заводить дубль с тем же названием.
"""

import sqlalchemy as sa

from alembic import op

revision = "0035_offer_cpa"
down_revision = "0034_finance_book_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column("cpa", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "offers",
        sa.Column(
            "cpa_currency", sa.String(length=3), nullable=False, server_default="USD"
        ),
    )
    op.add_column(
        "finance_book_offers", sa.Column("source_offer_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_finance_book_offer_source",
        "finance_book_offers",
        "offers",
        ["source_offer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_finance_book_offers_source_offer_id",
        "finance_book_offers",
        ["source_offer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_finance_book_offers_source_offer_id", table_name="finance_book_offers"
    )
    op.drop_constraint(
        "fk_finance_book_offer_source", "finance_book_offers", type_="foreignkey"
    )
    op.drop_column("finance_book_offers", "source_offer_id")
    op.drop_column("offers", "cpa_currency")
    op.drop_column("offers", "cpa")
