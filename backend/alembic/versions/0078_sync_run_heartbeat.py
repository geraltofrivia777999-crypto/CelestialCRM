"""Heartbeat on sync runs so a killed run does not hold the schedule.

A run whose worker died (container rebuilt, process killed) stays "running"
forever and blocks the next sync of that connection. Runs now stamp
`heartbeat_at` on every step, and the scheduler releases the silent ones.

Revision ID: 0078_sync_run_heartbeat
Revises: 0077_cap_rule_channels
"""

import sqlalchemy as sa

from alembic import op

revision = "0078_sync_run_heartbeat"
down_revision = "0077_cap_rule_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sync_runs", "heartbeat_at")
