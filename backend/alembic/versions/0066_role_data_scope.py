"""Role data scope

Revision ID: 0066_role_data_scope
Revises: 0065_pipeline_removed_at
"""

import sqlalchemy as sa
from alembic import op

revision = "0066_role_data_scope"
down_revision = "0065_pipeline_removed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("data_scope", sa.String(length=10), nullable=False, server_default="team"),
    )
    # Раньше весь воркспейс видели роли с полным доступом, остальные — свою
    # ветку подчинённых. Сохраняем это же поведение для уже заведённых ролей.
    op.execute(
        """
        UPDATE roles SET data_scope = 'all'
        WHERE id IN (
            SELECT rp.role_id
            FROM role_permissions rp
            JOIN permissions p ON p.id = rp.permission_id
            WHERE p.code = '*'
        )
        """
    )


def downgrade() -> None:
    op.drop_column("roles", "data_scope")
