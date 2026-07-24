"""Persist manual offer and partner status overrides."""

from alembic import op
import sqlalchemy as sa

revision = "0004_manual_catalog_status"
down_revision = "0003_finance_detail"
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table in ("partners", "offers"):
        if table in tables and "status_overridden" not in _columns(inspector, table):
            op.add_column(
                table,
                sa.Column(
                    "status_overridden",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table in ("offers", "partners"):
        if table in tables and "status_overridden" in _columns(inspector, table):
            op.drop_column(table, "status_overridden")
