"""Finance detail values, QUAL and user preferences.

Follows the defensive pattern of 0002: inspects the live schema first so the
migration is safe on both fresh and existing databases.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_finance_detail"
down_revision = "0002_keitaro_core"
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "finance_records" in tables:
        columns = _columns(inspector, "finance_records")
        if "spend_override" not in columns:
            op.add_column(
                "finance_records", sa.Column("spend_override", sa.Numeric(18, 4))
            )
        if "qual" not in columns:
            op.add_column(
                "finance_records",
                sa.Column("qual", sa.Numeric(18, 4), nullable=False, server_default="0"),
            )

    if "finance_service_values" not in tables:
        op.create_table(
            "finance_service_values",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("finance_record_id", sa.Uuid(), nullable=False),
            sa.Column("service_id", sa.Uuid(), nullable=False),
            sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("manual_cost_override", sa.Numeric(18, 4)),
            sa.ForeignKeyConstraint(
                ["finance_record_id"], ["finance_records.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "finance_record_id", "service_id", name="uq_finance_service"
            ),
        )

    if "finance_spend_values" not in tables:
        op.create_table(
            "finance_spend_values",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("finance_record_id", sa.Uuid(), nullable=False),
            sa.Column("provider_id", sa.Uuid(), nullable=False),
            sa.Column(
                "base_amount", sa.Numeric(18, 4), nullable=False, server_default="0"
            ),
            sa.Column("manual_amount_override", sa.Numeric(18, 4)),
            sa.ForeignKeyConstraint(
                ["finance_record_id"], ["finance_records.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["provider_id"], ["spend_providers.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "finance_record_id", "provider_id", name="uq_finance_provider"
            ),
        )

    if "user_preferences" not in tables:
        op.create_table(
            "user_preferences",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("key", sa.String(100), nullable=False),
            sa.Column("value", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "key", name="uq_user_preference_key"),
        )
        op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "user_preferences" in tables:
        op.drop_table("user_preferences")
    if "finance_spend_values" in tables:
        op.drop_table("finance_spend_values")
    if "finance_service_values" in tables:
        op.drop_table("finance_service_values")
    if "finance_records" in tables:
        columns = _columns(inspector, "finance_records")
        if "qual" in columns:
            op.drop_column("finance_records", "qual")
        if "spend_override" in columns:
            op.drop_column("finance_records", "spend_override")
