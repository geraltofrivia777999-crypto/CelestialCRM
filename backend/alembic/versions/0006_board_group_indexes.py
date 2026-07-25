"""Indexes for the grouped board queries.

Both boards join every record to its offer and then filter by GEO or partner, and
finance additionally scans by workspace + date. Without these the grouped queries
fall back to sequential scans once a workspace accumulates real volume.
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_board_group_indexes"
down_revision = "0005_manual_media_fields"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_offers_geo", "offers", ["geo"]),
    ("ix_offers_partner_id", "offers", ["partner_id"]),
    ("ix_finance_records_date", "finance_records", ["workspace_id", "record_date"]),
    ("ix_media_records_date", "media_records", ["workspace_id", "record_date"]),
]


def _existing(inspector: sa.Inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for name, table, columns in INDEXES:
        if table in tables and name not in _existing(inspector, table):
            op.create_index(name, table, columns)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for name, table, _columns in INDEXES:
        if table in tables and name in _existing(inspector, table):
            op.drop_index(name, table_name=table)
