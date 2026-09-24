"""Several Telegram channels per CAP rule.

`cap_rules.channel_ids` keeps the whole list (the existing `channel_id` stays
as the first one), and `alert_events.channel_id` says where a queued message
goes, because one CAP now produces one event per channel.

Revision ID: 0076_cap_rule_channels
Revises: 0075_user_finance_tags
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0077_cap_rule_channels"
down_revision = "0076_manual_partners"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cap_rules",
        sa.Column("channel_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "alert_events",
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_alert_events_channel_id",
        "alert_events",
        "alert_channels",
        ["channel_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_alert_events_channel_id", "alert_events", ["channel_id"])
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, channel_id FROM cap_rules")).all()
    for rule_id, channel_id in rows:
        bind.execute(
            sa.text(
                "UPDATE cap_rules SET channel_ids = CAST(:ids AS JSON) WHERE id = :id"
            ),
            {"ids": f'["{channel_id}"]', "id": rule_id},
        )


def downgrade() -> None:
    op.drop_index("ix_alert_events_channel_id", table_name="alert_events")
    op.drop_constraint("fk_alert_events_channel_id", "alert_events", type_="foreignkey")
    op.drop_column("alert_events", "channel_id")
    op.drop_column("cap_rules", "channel_ids")
