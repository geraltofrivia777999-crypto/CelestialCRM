"""Владелец подключения интеграции.

Meta Ads подключается отдельно каждым пользователем. Старое подключение можно
однозначно привязать к человеку, если все его кабинеты уже закреплены за одним
ответственным; остальные legacy-строки остаются административными.
"""

import sqlalchemy as sa

from alembic import op

revision = "0041_connection_owner"
down_revision = "0040_connection_proxy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integration_connections",
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_integration_connections_owner_id",
        "integration_connections",
        ["owner_id"],
    )
    # У части старых подключений ответственный уже проставлен на всех кабинетах.
    # Только этот однозначный случай безопасно переносить на подключение.
    op.execute(
        """
        UPDATE integration_connections AS connection
        SET owner_id = owners.owner_id
        FROM (
            SELECT connection_id, MIN(owner_id::text)::uuid AS owner_id
            FROM meta_ad_accounts
            WHERE owner_id IS NOT NULL
            GROUP BY connection_id
            HAVING COUNT(DISTINCT owner_id) = 1
        ) AS owners
        WHERE connection.id = owners.connection_id
          AND connection.kind = 'meta'
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_connections_owner_id",
        table_name="integration_connections",
    )
    op.drop_column("integration_connections", "owner_id")
