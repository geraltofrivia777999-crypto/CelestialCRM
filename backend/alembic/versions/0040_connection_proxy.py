"""Прокси и user-agent у подключения Meta.

Кабинеты часто живут за своим прокси: запрос из другой сети Meta к ним просто
не пускает. Адрес хранится у подключения, потому что маршрут — свойство доступа,
а не отдельного кабинета.
"""

import sqlalchemy as sa

from alembic import op

revision = "0040_connection_proxy"
down_revision = "0039_connection_auth_method"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integration_connections", sa.Column("proxy_url", sa.String(length=500))
    )
    op.add_column(
        "integration_connections", sa.Column("user_agent", sa.String(length=500))
    )


def downgrade() -> None:
    op.drop_column("integration_connections", "user_agent")
    op.drop_column("integration_connections", "proxy_url")
