"""Tags on users that become tag rows under their offers in Finance.

The first tag is the user's Keitaro offer group, so existing users start with
that one tag.

Revision ID: 0075_user_finance_tags
Revises: 0074_alert_delivery_outbox
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0075_user_finance_tags"
down_revision = "0074_alert_delivery_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("finance_tags", sa.JSON(), nullable=False, server_default="[]"),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, keitaro_offer_group FROM users "
            "WHERE keitaro_offer_group IS NOT NULL"
        )
    ).all()
    for user_id, group in rows:
        group = (group or "").strip()
        if not group:
            continue
        bind.execute(
            sa.text("UPDATE users SET finance_tags = CAST(:tags AS JSON) WHERE id = :id"),
            {"tags": json.dumps([group[:120]], ensure_ascii=False), "id": user_id},
        )


def downgrade() -> None:
    op.drop_column("users", "finance_tags")
