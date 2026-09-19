"""Reliable and idempotent delivery queue for Telegram alerts.

Revision ID: 0074_alert_delivery_outbox
Revises: 0073_task_status_log_details
"""

import sqlalchemy as sa

from alembic import op

revision = "0074_alert_delivery_outbox"
down_revision = "0073_task_status_log_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert_events", sa.Column("event_key", sa.String(220), nullable=True))
    op.add_column(
        "alert_events",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "alert_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "alert_events", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "uq_alert_events_event_key", "alert_events", ["event_key"], unique=True
    )
    op.create_index(
        "ix_alert_events_pending",
        "alert_events",
        ["delivered", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_events_pending", table_name="alert_events")
    op.drop_index("uq_alert_events_event_key", table_name="alert_events")
    op.drop_column("alert_events", "delivered_at")
    op.drop_column("alert_events", "next_attempt_at")
    op.drop_column("alert_events", "attempts")
    op.drop_column("alert_events", "event_key")
