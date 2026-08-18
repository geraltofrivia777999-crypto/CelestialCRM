"""Способ выпуска токена Meta.

Graph API любой токен принимает одинаково, поэтому на саму интеграцию способ не
влияет. Но живут токены по-разному: системный — бессрочно, из панели приложения —
часы, из сессии аккаунта — до первого выхода из устройств. Когда синхронизация
встанет, объяснение «почему» зависит от способа, и хранить его дешевле, чем
угадывать по коду ошибки.
"""

import sqlalchemy as sa

from alembic import op

revision = "0039_connection_auth_method"
down_revision = "0038_launch_ads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integration_connections",
        sa.Column(
            "auth_method",
            sa.String(length=20),
            nullable=False,
            server_default="system_user",
        ),
    )


def downgrade() -> None:
    op.drop_column("integration_connections", "auth_method")
