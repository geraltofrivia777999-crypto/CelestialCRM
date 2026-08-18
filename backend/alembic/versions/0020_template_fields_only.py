"""Шаблон задачи хранит только набор полей — ТЗ 8.1.

Название, приоритет, колонка, срок, описание и исполнители по умолчанию из
шаблона убраны: их всё равно заполняют под конкретную задачу, а подставленное
«Крео для » приходилось стирать перед каждым вводом. Шаблон остаётся тем, чем
он полезен, — набором полей карточки и значениями по умолчанию для них.
"""

import sqlalchemy as sa

from alembic import op

revision = "0020_template_fields_only"
down_revision = "0019_utilities_alerts"
branch_labels = None
depends_on = None

TABLE = "task_templates"
DROPPED = ("title", "description", "priority", "status_id", "due_in_days", "assignee_ids")


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    columns = _columns()
    if not columns:
        return
    if "status_id" in columns:
        # Внешний ключ мешает удалить колонку; имя у него по умолчанию, поэтому
        # ищем его в схеме, а не задаём строкой.
        inspector = sa.inspect(op.get_bind())
        for constraint in inspector.get_foreign_keys(TABLE):
            if constraint["constrained_columns"] == ["status_id"] and constraint["name"]:
                op.drop_constraint(constraint["name"], TABLE, type_="foreignkey")
    for column in DROPPED:
        if column in columns:
            op.drop_column(TABLE, column)


def downgrade() -> None:
    columns = _columns()
    if not columns:
        return
    if "title" not in columns:
        op.add_column(TABLE, sa.Column("title", sa.String(length=300)))
    if "description" not in columns:
        op.add_column(TABLE, sa.Column("description", sa.Text()))
    if "priority" not in columns:
        op.add_column(
            TABLE,
            sa.Column(
                "priority",
                sa.Enum("low", "medium", "high", "critical", name="taskpriority"),
                nullable=False,
                server_default="medium",
            ),
        )
    if "status_id" not in columns:
        op.add_column(TABLE, sa.Column("status_id", sa.Uuid()))
        op.create_foreign_key(
            "fk_task_templates_status_id",
            TABLE,
            "task_statuses",
            ["status_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "due_in_days" not in columns:
        op.add_column(TABLE, sa.Column("due_in_days", sa.Integer()))
    if "assignee_ids" not in columns:
        op.add_column(
            TABLE, sa.Column("assignee_ids", sa.JSON(), nullable=False, server_default="[]")
        )
