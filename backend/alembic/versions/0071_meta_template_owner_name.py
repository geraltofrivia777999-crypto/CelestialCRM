"""Bundle names are unique per owner, not per workspace.

Revision ID: 0071_meta_template_owner_name
Revises: 0070_task_fields_finance_locks

Связки видны только своему владельцу и его руководителям. Уникальность по всему
воркспейсу выдавала чужие названия («такая связка уже есть») и мешала двум
баерам назвать свои связки одинаково.
"""

from alembic import op

revision = "0071_meta_template_owner_name"
down_revision = "0070_task_fields_finance_locks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_meta_template_name", "meta_templates", type_="unique")
    op.create_unique_constraint(
        "uq_meta_template_owner_name",
        "meta_templates",
        ["workspace_id", "created_by_id", "name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_meta_template_owner_name", "meta_templates", type_="unique")
    op.create_unique_constraint(
        "uq_meta_template_name", "meta_templates", ["workspace_id", "name"]
    )
