"""Track manually edited media fields and stamp sync runs."""

from alembic import op
import sqlalchemy as sa

revision = "0005_manual_media_fields"
down_revision = "0004_manual_catalog_status"
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "media_records" in tables and "manual_fields" not in _columns(
        inspector, "media_records"
    ):
        op.add_column(
            "media_records",
            sa.Column(
                "manual_fields",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            ),
        )

    if "sync_runs" in tables and "created_at" not in _columns(inspector, "sync_runs"):
        op.add_column(
            "sync_runs",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_sync_runs_created_at", "sync_runs", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "sync_runs" in tables and "created_at" in _columns(inspector, "sync_runs"):
        op.drop_index("ix_sync_runs_created_at", table_name="sync_runs")
        op.drop_column("sync_runs", "created_at")

    if "media_records" in tables and "manual_fields" in _columns(
        inspector, "media_records"
    ):
        op.drop_column("media_records", "manual_fields")
