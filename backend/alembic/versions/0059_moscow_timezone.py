"""Единая пользовательская таймзона CRM — Москва.

UTC остаётся форматом хранения timestamp. Меняются календарные границы,
расписания и отображение. Ранее журнал Keitaro отдавал наивное время в зоне
подключения, а импорт считал его UTC; уже загруженные строки из Qyzylorda
корректируем на пять часов до смены зоны подключения.
"""

import sqlalchemy as sa

from alembic import op

revision = "0059_moscow_timezone"
down_revision = "0058_offer_partner_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "UPDATE keitaro_conversions AS conversion "
                "SET conversion_at = conversion.conversion_at - INTERVAL '5 hours', "
                "click_at = conversion.click_at - INTERVAL '5 hours' "
                "FROM integration_connections AS connection "
                "WHERE conversion.connection_id = connection.id "
                "AND connection.timezone = 'Asia/Qyzylorda'"
            )
        )

    for table in ("workspaces", "integration_connections", "alert_rules", "cap_rules"):
        bind.execute(sa.text(f"UPDATE {table} SET timezone = 'Europe/Moscow'"))

    for table in ("workspaces", "integration_connections", "alert_rules", "cap_rules"):
        op.alter_column(table, "timezone", server_default="Europe/Moscow")


def downgrade() -> None:
    op.alter_column("workspaces", "timezone", server_default="Asia/Qyzylorda")
    op.alter_column("integration_connections", "timezone", server_default="UTC")
    op.alter_column("alert_rules", "timezone", server_default="Europe/Moscow")
    op.alter_column("cap_rules", "timezone", server_default="UTC")
