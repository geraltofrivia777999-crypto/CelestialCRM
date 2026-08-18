"""Типы пользовательских полей задачи и шаблоны с набором полей (ТЗ 8.1).

До этой миграции каждое пользовательское поле висело на всех карточках сразу.
Так нельзя описать бриф на креатив: «Исходник» и «Референс» нужны только в нём,
а в задаче бухгалтерии это шум. Поэтому у поля появляется `show_always`, у
шаблона — список полей, а у задачи — ссылка на шаблон, по которому она заведена.

Существующие поля получают `show_always = true`: до сих пор они показывались
везде, и миграция не должна их прятать.
"""

import sqlalchemy as sa

from alembic import op

revision = "0016_task_field_types"
down_revision = "0015_workspace"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    field_columns = _columns("task_fields")
    if field_columns and "config" not in field_columns:
        op.add_column(
            "task_fields",
            sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        )
    if field_columns and "show_always" not in field_columns:
        op.add_column(
            "task_fields",
            sa.Column(
                "show_always",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    template_columns = _columns("task_templates")
    if template_columns and "field_ids" not in template_columns:
        op.add_column(
            "task_templates",
            sa.Column("field_ids", sa.JSON(), nullable=False, server_default="[]"),
        )

    task_columns = _columns("tasks")
    if task_columns and "template_id" not in task_columns:
        op.add_column("tasks", sa.Column("template_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_tasks_template_id",
            "tasks",
            "task_templates",
            ["template_id"],
            ["id"],
            ondelete="SET NULL",
        )

    attachment_columns = _columns("knowledge_attachments")
    if attachment_columns and "task_id" not in attachment_columns:
        op.add_column(
            "knowledge_attachments", sa.Column("task_id", sa.Uuid(), nullable=True)
        )
        op.create_index(
            "ix_knowledge_attachments_task_id",
            "knowledge_attachments",
            ["task_id"],
        )
        op.create_foreign_key(
            "fk_knowledge_attachments_task_id",
            "knowledge_attachments",
            "tasks",
            ["task_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    attachment_columns = _columns("knowledge_attachments")
    if "task_id" in attachment_columns:
        op.drop_constraint(
            "fk_knowledge_attachments_task_id",
            "knowledge_attachments",
            type_="foreignkey",
        )
        op.drop_index(
            "ix_knowledge_attachments_task_id", table_name="knowledge_attachments"
        )
        op.drop_column("knowledge_attachments", "task_id")

    if "template_id" in _columns("tasks"):
        op.drop_constraint("fk_tasks_template_id", "tasks", type_="foreignkey")
        op.drop_column("tasks", "template_id")

    if "field_ids" in _columns("task_templates"):
        op.drop_column("task_templates", "field_ids")

    field_columns = _columns("task_fields")
    if "show_always" in field_columns:
        op.drop_column("task_fields", "show_always")
    if "config" in field_columns:
        op.drop_column("task_fields", "config")
