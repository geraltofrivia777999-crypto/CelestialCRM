"""Разделы доски задач с доступами.

Доска делится по отделам: у дизайнеров свой раздел, у баеров свой. Права
устроены как в базе знаний — правило на роль или на человека, именное сильнее
ролевого, раздел без правил открыт всем.

Существующие задачи переносятся в раздел «Общие задачи» своего воркспейса:
карточка без раздела не попала бы ни на одну доску, поэтому ссылка обязательная.
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0036_task_sections"
down_revision = "0035_offer_cpa"
branch_labels = None
depends_on = None

DEFAULT_TITLE = "Общие задачи"


def upgrade() -> None:
    op.create_table(
        "task_sections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_task_sections_workspace_id", "task_sections", ["workspace_id"])
    op.create_index(
        "ix_task_sections_order", "task_sections", ["workspace_id", "position"]
    )

    op.create_table(
        "task_section_access",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.Uuid(),
            sa.ForeignKey("task_sections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE")),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("can_view", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("can_create", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_edit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_delete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_manage", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("section_id", "role_id", name="uq_task_section_access_role"),
        sa.UniqueConstraint("section_id", "user_id", name="uq_task_section_access_user"),
    )
    op.create_index(
        "ix_task_section_access_workspace_id", "task_section_access", ["workspace_id"]
    )
    op.create_index(
        "ix_task_section_access_section_id", "task_section_access", ["section_id"]
    )
    op.create_index("ix_task_section_access_role_id", "task_section_access", ["role_id"])
    op.create_index("ix_task_section_access_user_id", "task_section_access", ["user_id"])

    _seed_sections()

    op.add_column("tasks", sa.Column("section_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE tasks
           SET section_id = (
               SELECT s.id FROM task_sections s
                WHERE s.workspace_id = tasks.workspace_id
                ORDER BY s.position
                LIMIT 1
           )
        """
    )
    op.alter_column("tasks", "section_id", nullable=False)
    op.create_foreign_key(
        "fk_tasks_section", "tasks", "task_sections", ["section_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_tasks_section_id", "tasks", ["section_id"])
    op.create_index("ix_tasks_section", "tasks", ["workspace_id", "section_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_section", table_name="tasks")
    op.drop_index("ix_tasks_section_id", table_name="tasks")
    op.drop_constraint("fk_tasks_section", "tasks", type_="foreignkey")
    op.drop_column("tasks", "section_id")

    op.drop_index("ix_task_section_access_user_id", table_name="task_section_access")
    op.drop_index("ix_task_section_access_role_id", table_name="task_section_access")
    op.drop_index("ix_task_section_access_section_id", table_name="task_section_access")
    op.drop_index("ix_task_section_access_workspace_id", table_name="task_section_access")
    op.drop_table("task_section_access")

    op.drop_index("ix_task_sections_order", table_name="task_sections")
    op.drop_index("ix_task_sections_workspace_id", table_name="task_sections")
    op.drop_table("task_sections")


def _seed_sections() -> None:
    """Раздел по умолчанию каждому воркспейсу.

    Всем, а не только тем, где есть задачи: доска без единого раздела не приняла
    бы ни одной новой карточки.
    """
    bind = op.get_bind()
    workspaces = sa.table("workspaces", sa.column("id", sa.Uuid()))
    sections = sa.table(
        "task_sections",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("title", sa.String()),
        sa.column("position", sa.Integer()),
    )
    rows = [
        {
            "id": uuid.uuid4(),
            "workspace_id": workspace_id,
            "title": DEFAULT_TITLE,
            "position": 0,
        }
        for (workspace_id,) in bind.execute(sa.select(workspaces.c.id))
    ]
    if rows:
        op.bulk_insert(sections, rows)
