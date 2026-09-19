"""Task section default template

Revision ID: 0067_section_default_template
Revises: 0066_role_data_scope
"""

import sqlalchemy as sa
from alembic import op

revision = "0067_section_default_template"
down_revision = "0066_role_data_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_sections",
        sa.Column("default_template_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_task_sections_default_template",
        "task_sections",
        "task_templates",
        ["default_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # До этого шаблон по умолчанию был один на воркспейс. Раздаём его всем
    # разделам, чтобы после обновления новая задача открывалась как раньше.
    op.execute(
        """
        UPDATE task_sections SET default_template_id = (
            SELECT t.id FROM task_templates t
            WHERE t.workspace_id = task_sections.workspace_id AND t.is_default
            LIMIT 1
        )
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_task_sections_default_template", "task_sections", type_="foreignkey")
    op.drop_column("task_sections", "default_template_id")
