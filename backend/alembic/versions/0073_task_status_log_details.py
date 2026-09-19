"""Task status log, automatic task dates and the task details permission.

Revision ID: 0073_task_status_log_details
Revises: 0072_launch_budget_random_pct

* task_status_events — кто и когда перевёл задачу в каждый статус.
* workspace.details — видеть и менять приоритет, даты и исполнителей задачи.
  Выдаётся ролям, у которых есть «Задачи · просмотр», кроме роли Buyer:
  баерам эти поля не нужны, дизайнерам и руководителям — нужны.
* Даты задачи теперь ставятся сами: начало — день создания, выполнение — день
  перехода в «Готово». Старые задачи приводим к этому же правилу.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0073_task_status_log_details"
down_revision = "0072_launch_budget_random_pct"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_status_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "status_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("task_statuses.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("status_name", sa.String(80), nullable=False, server_default=""),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_task_status_events_task", "task_status_events", ["task_id", "created_at"])

    op.execute(
        """
        INSERT INTO permissions (id, code, description)
        SELECT gen_random_uuid(), 'workspace.details', 'workspace details'
        WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'workspace.details')
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT rp.role_id, fresh.id
        FROM role_permissions rp
        JOIN permissions viewing ON viewing.id = rp.permission_id
            AND viewing.code = 'workspace.view'
        JOIN roles r ON r.id = rp.role_id AND lower(r.name) <> 'buyer'
        JOIN permissions fresh ON fresh.code = 'workspace.details'
        WHERE NOT EXISTS (
            SELECT 1 FROM role_permissions existing
            WHERE existing.role_id = rp.role_id AND existing.permission_id = fresh.id
        )
        """
    )
    # Роли с полным доступом («*») право и так покрывают.
    op.execute(
        """
        UPDATE tasks
        SET start_date = (created_at AT TIME ZONE 'Europe/Moscow')::date
        WHERE start_date IS NULL
        """
    )
    op.execute(
        """
        UPDATE tasks
        SET due_date = CASE
            WHEN completed_at IS NOT NULL
                THEN (completed_at AT TIME ZONE 'Europe/Moscow')::date
            ELSE NULL
        END
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions WHERE permission_id IN (
            SELECT id FROM permissions WHERE code = 'workspace.details'
        )
        """
    )
    op.execute("DELETE FROM permissions WHERE code = 'workspace.details'")
    op.drop_index("ix_task_status_events_task", table_name="task_status_events")
    op.drop_table("task_status_events")
