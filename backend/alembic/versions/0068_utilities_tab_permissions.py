"""Utilities channels and events permissions

Revision ID: 0068_utilities_tab_permissions
Revises: 0067_section_default_template
"""

from alembic import op

revision = "0068_utilities_tab_permissions"
down_revision = "0067_section_default_template"
branch_labels = None
depends_on = None

CODES = ("utilities.channels", "utilities.events")


def upgrade() -> None:
    # Права заводит и seed при старте, но выдать их ролям нужно здесь: иначе
    # после обновления вкладки «Каналы» и «Журнал» пропали бы у всех, кроме
    # администратора. Получают их роли, которые уже видят Утилиты.
    op.execute(
        """
        INSERT INTO permissions (id, code, description)
        SELECT gen_random_uuid(), v.code, replace(v.code, '.', ' ')
        FROM (VALUES ('utilities.channels'), ('utilities.events')) AS v(code)
        WHERE NOT EXISTS (SELECT 1 FROM permissions p WHERE p.code = v.code)
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT rp.role_id, fresh.id
        FROM role_permissions rp
        JOIN permissions viewing ON viewing.id = rp.permission_id
            AND viewing.code = 'utilities.view'
        JOIN permissions fresh ON fresh.code IN ('utilities.channels', 'utilities.events')
        WHERE NOT EXISTS (
            SELECT 1 FROM role_permissions existing
            WHERE existing.role_id = rp.role_id AND existing.permission_id = fresh.id
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions WHERE permission_id IN (
            SELECT id FROM permissions WHERE code IN ('utilities.channels', 'utilities.events')
        )
        """
    )
    op.execute(
        "DELETE FROM permissions WHERE code IN ('utilities.channels', 'utilities.events')"
    )
