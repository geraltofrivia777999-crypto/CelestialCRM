"""Ручное заведение офферов: капа, тимлиды и статус «Активен».

Раздел «Оффера» перестал быть витриной группы Keitaro. Оффер теперь заводят
руками, поэтому `connection_id` и `external_id` становятся необязательными:
именно пустой `connection_id` отличает ручную строку от синхронизированной.

Статусов тоже стало иначе: появился «Активен» (оффер у тимлида), а «Тест»
ушёл — строки с ним переводятся в «Не занят», первичный статус. В PostgreSQL
значение из enum не удаляется на месте, поэтому тип пересоздаётся целиком.
"""

import sqlalchemy as sa

from alembic import op

revision = "0022_manual_offers"
down_revision = "0021_salary_rule_trim"
branch_labels = None
depends_on = None

NEW_STATUSES = ("active", "working", "hold", "stop", "free")
OLD_STATUSES = ("working", "hold", "stop", "test", "free")


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _swap_status_enum(bind, values: tuple[str, ...]) -> None:
    """Пересоздать тип offerstatus с новым набором значений."""
    if bind.dialect.name != "postgresql":
        # SQLite хранит enum как VARCHAR — менять нечего.
        return
    listed = ", ".join(f"'{value}'" for value in values)
    op.execute("ALTER TYPE offerstatus RENAME TO offerstatus_old")
    op.execute(f"CREATE TYPE offerstatus AS ENUM ({listed})")
    op.execute("ALTER TABLE offers ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE offers ALTER COLUMN status TYPE offerstatus "
        "USING status::text::offerstatus"
    )
    op.execute("ALTER TABLE offers ALTER COLUMN status SET DEFAULT 'free'")
    op.execute("DROP TYPE offerstatus_old")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    columns = _columns(inspector, "offers")

    # Сначала данные, потом тип: 'test' в старом наборе ещё существует.
    op.execute("UPDATE offers SET status = 'free' WHERE status = 'test'")
    _swap_status_enum(bind, NEW_STATUSES)

    if "cap" not in columns:
        op.add_column("offers", sa.Column("cap", sa.String(length=160), nullable=True))
    if bind.dialect.name == "postgresql":
        op.alter_column("offers", "connection_id", nullable=True)
        op.alter_column("offers", "external_id", nullable=True)
    if "ix_offers_connection_id" not in {
        index["name"] for index in inspector.get_indexes("offers")
    }:
        op.create_index("ix_offers_connection_id", "offers", ["connection_id"])

    if "offer_leads" not in tables:
        op.create_table(
            "offer_leads",
            sa.Column("offer_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("offer_id", "user_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "offer_leads" in set(inspector.get_table_names()):
        op.drop_table("offer_leads")
    if "ix_offers_connection_id" in {
        index["name"] for index in inspector.get_indexes("offers")
    }:
        op.drop_index("ix_offers_connection_id", table_name="offers")
    if "cap" in _columns(inspector, "offers"):
        op.drop_column("offers", "cap")

    # Ручные офферы без трекера в старую схему не помещаются.
    op.execute("DELETE FROM offers WHERE connection_id IS NULL")
    op.execute("UPDATE offers SET status = 'free' WHERE status = 'active'")
    _swap_status_enum(bind, OLD_STATUSES)
    if bind.dialect.name == "postgresql":
        op.alter_column("offers", "connection_id", nullable=False)
        op.alter_column("offers", "external_id", nullable=False)
