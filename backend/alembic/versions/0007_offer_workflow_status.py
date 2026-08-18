"""Offer workflow status, star flag and a home for Keitaro's own state.

`offers.status` used to mirror Keitaro (active/inactive). It now carries our own
workflow — В работе / Холд / Стоп / Тест / Не занят — and Keitaro's state moves
to `offers.keitaro_state`, so a sync can no longer overwrite a team decision and
`status_overridden` has nothing left to protect.

Existing rows are split the way the workflow defines it: an offer that already
has buyers is "в работе", everything else starts as "не занят".
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_offer_workflow_status"
down_revision = "0006_board_group_indexes"
branch_labels = None
depends_on = None

OFFER_STATUS = sa.Enum(
    "working", "hold", "stop", "test", "free", name="offerstatus"
)


def _columns(inspector: sa.Inspector) -> set[str]:
    return {column["name"] for column in inspector.get_columns("offers")}


def _indexes(inspector: sa.Inspector) -> set[str]:
    return {index["name"] for index in inspector.get_indexes("offers")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = _columns(inspector)
    indexes = _indexes(inspector)

    if "keitaro_state" not in columns:
        op.alter_column("offers", "status", new_column_name="keitaro_state")
        if "ix_offers_status" in indexes:
            # The index follows the column through the rename; only its name is stale.
            if bind.dialect.name == "postgresql":
                op.execute("ALTER INDEX ix_offers_status RENAME TO ix_offers_keitaro_state")
            else:
                op.drop_index("ix_offers_status", table_name="offers")
                op.create_index("ix_offers_keitaro_state", "offers", ["keitaro_state"])

    OFFER_STATUS.create(bind, checkfirst=True)
    op.add_column(
        "offers",
        sa.Column(
            "status",
            OFFER_STATUS,
            nullable=False,
            server_default="free",
        ),
    )
    op.execute(
        """
        UPDATE offers
        SET status = 'working'
        WHERE EXISTS (
            SELECT 1 FROM offer_buyers WHERE offer_buyers.offer_id = offers.id
        )
        """
    )
    op.add_column(
        "offers",
        sa.Column(
            "is_starred",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    if "status_overridden" in columns:
        op.drop_column("offers", "status_overridden")

    indexes = _indexes(sa.inspect(bind))
    if "ix_offers_status" not in indexes:
        op.create_index("ix_offers_status", "offers", ["status"])
    if "ix_offers_group_name" not in indexes:
        op.create_index("ix_offers_group_name", "offers", ["group_name"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = _indexes(inspector)

    for name in ("ix_offers_status", "ix_offers_group_name"):
        if name in indexes:
            op.drop_index(name, table_name="offers")
    op.drop_column("offers", "status")
    op.drop_column("offers", "is_starred")
    OFFER_STATUS.drop(bind, checkfirst=True)

    op.add_column(
        "offers",
        sa.Column(
            "status_overridden",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("offers", "keitaro_state", new_column_name="status")
    if "ix_offers_keitaro_state" in _indexes(sa.inspect(bind)):
        if bind.dialect.name == "postgresql":
            op.execute("ALTER INDEX ix_offers_keitaro_state RENAME TO ix_offers_status")
        else:
            op.drop_index("ix_offers_keitaro_state", table_name="offers")
            op.create_index("ix_offers_status", "offers", ["status"])
