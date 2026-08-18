"""CapAlert: несколько офферов, своя таймзона и тема супергруппы.

Капа умела ровно один оффер, но партнёрка обычно даёт общий лимит на связку —
поэтому `offer_id` превращается в список `offer_ids`, показатели по которым
складываются. Заодно у капы появляется таймзона (без неё «сброс в 00:00» — это
полночь сервера, а не команды) и собственный `thread_id`: капы одного канала
часто разводят по разным темам.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0025_cap_multi_offer"
down_revision = "0024_meta_rule_conditions"
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector) -> set[str]:
    return {column["name"] for column in inspector.get_columns("cap_rules")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(sa.inspect(bind))

    if "offer_ids" not in columns:
        op.add_column("cap_rules", sa.Column("offer_ids", sa.JSON(), nullable=True))
    if "timezone" not in columns:
        op.add_column(
            "cap_rules",
            sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        )
    if "thread_id" not in columns:
        op.add_column("cap_rules", sa.Column("thread_id", sa.String(length=32), nullable=True))

    if "offer_id" in columns:
        rows = bind.execute(sa.text("SELECT id, offer_id FROM cap_rules")).fetchall()
        for row in rows:
            payload = json.dumps([str(row.offer_id)] if row.offer_id else [])
            bind.execute(
                sa.text("UPDATE cap_rules SET offer_ids = :payload WHERE id = :id"),
                {"payload": payload, "id": row.id},
            )

    op.execute("UPDATE cap_rules SET offer_ids = '[]' WHERE offer_ids IS NULL")
    op.alter_column("cap_rules", "offer_ids", nullable=False)

    if "offer_id" in _columns(sa.inspect(bind)):
        op.drop_column("cap_rules", "offer_id")


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(sa.inspect(bind))

    if "offer_id" not in columns:
        op.add_column("cap_rules", sa.Column("offer_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "cap_rules_offer_id_fkey",
            "cap_rules",
            "offers",
            ["offer_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_cap_rules_offer_id", "cap_rules", ["offer_id"])

    # Назад помещается только первый оффер — остальные теряются.
    rows = bind.execute(sa.text("SELECT id, offer_ids FROM cap_rules")).fetchall()
    for row in rows:
        raw = row.offer_ids
        parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
        bind.execute(
            sa.text("UPDATE cap_rules SET offer_id = :offer WHERE id = :id"),
            {"offer": parsed[0] if parsed else None, "id": row.id},
        )

    for name in ("thread_id", "timezone", "offer_ids"):
        if name in _columns(sa.inspect(bind)):
            op.drop_column("cap_rules", name)
