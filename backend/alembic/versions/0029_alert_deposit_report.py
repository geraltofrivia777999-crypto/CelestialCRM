"""Alert: уведомление о депозитах и отчёт по расписанию.

Дерево условий и группировка убраны: команде нужны ровно два сценария —
сообщение на каждый депозит и сводка по расписанию, — а универсальный
конструктор только множил способы ошибиться.

Заодно появляется журнал конверсий Keitaro: дневной отчёт знает «три продажи
за день», а в сообщении о депозите нужен конкретный депозит — его время клика
и его sub_id.
"""

import sqlalchemy as sa

from alembic import op

revision = "0029_alert_deposit_report"
down_revision = "0028_meta_spend_commits"
branch_labels = None
depends_on = None

_ADDED = (
    ("thread_id", sa.String(length=32), True, None),
    ("campaign_group_ids", sa.JSON(), True, None),
    ("offer_ids", sa.JSON(), True, None),
    ("schedule", sa.String(length=24), False, "daily_09"),
    ("timezone", sa.String(length=64), False, "Europe/Moscow"),
    ("cursor_at", sa.DateTime(timezone=True), True, None),
)
_DROPPED = ("group_by", "conditions", "user_id", "offer_id", "run_at", "frequency",
            "cooldown_minutes")


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not sa.inspect(bind).has_table("keitaro_conversions"):
        op.create_table(
            "keitaro_conversions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("connection_id", sa.Uuid(), nullable=False),
            sa.Column("external_id", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default=""),
            sa.Column("conversion_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("click_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("campaign_external_id", sa.String(length=100), nullable=True),
            sa.Column("campaign_name", sa.String(length=300), nullable=True),
            sa.Column("campaign_group_id", sa.String(length=100), nullable=True),
            sa.Column("offer_external_id", sa.String(length=100), nullable=True),
            sa.Column("offer_name", sa.String(length=300), nullable=True),
            sa.Column("country_code", sa.String(length=12), nullable=True),
            sa.Column("revenue", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("payout", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("sub_values", sa.JSON(), nullable=True),
            sa.Column(
                "seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["connection_id"], ["integration_connections.id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint("connection_id", "external_id", name="uq_keitaro_conversion"),
        )
        for name, columns in (
            ("ix_keitaro_conversions_workspace_id", ["workspace_id"]),
            ("ix_keitaro_conversions_connection_id", ["connection_id"]),
            ("ix_keitaro_conversions_status", ["status"]),
            ("ix_keitaro_conversions_campaign_external_id", ["campaign_external_id"]),
            ("ix_keitaro_conversions_campaign_group_id", ["campaign_group_id"]),
            ("ix_keitaro_conversions_offer_external_id", ["offer_external_id"]),
            ("ix_keitaro_conversions_seen", ["workspace_id", "seen_at"]),
        ):
            op.create_index(name, "keitaro_conversions", columns)

    live = _columns(sa.inspect(bind), "alert_rules")
    for name, kind, nullable, default in _ADDED:
        if name not in live:
            op.add_column(
                "alert_rules",
                sa.Column(name, kind, nullable=nullable, server_default=default),
            )
    op.execute("UPDATE alert_rules SET campaign_group_ids = '[]' WHERE campaign_group_ids IS NULL")
    op.execute("UPDATE alert_rules SET offer_ids = '[]' WHERE offer_ids IS NULL")

    # Прежние правила переезжают в ближайший по смыслу вид: сводка по
    # расписанию остаётся отчётом, всё остальное — уведомлением о депозитах.
    if "kind" in live:
        op.execute("UPDATE alert_rules SET kind = 'report' WHERE kind = 'schedule'")
        op.execute("UPDATE alert_rules SET kind = 'deposit' WHERE kind <> 'report'")

    live = _columns(sa.inspect(bind), "alert_rules")
    for name in _DROPPED:
        if name in live:
            op.drop_column("alert_rules", name)


def downgrade() -> None:
    bind = op.get_bind()
    live = _columns(sa.inspect(bind), "alert_rules")
    if "conditions" not in live:
        op.add_column("alert_rules", sa.Column("conditions", sa.JSON(), nullable=True))
        op.add_column(
            "alert_rules",
            sa.Column("group_by", sa.String(length=16), nullable=False, server_default="global"),
        )
        op.add_column("alert_rules", sa.Column("user_id", sa.Uuid(), nullable=True))
        op.add_column("alert_rules", sa.Column("offer_id", sa.Uuid(), nullable=True))
        op.add_column("alert_rules", sa.Column("run_at", sa.String(length=5), nullable=True))
        op.add_column(
            "alert_rules",
            sa.Column("frequency", sa.String(length=16), nullable=False, server_default="daily"),
        )
        op.add_column(
            "alert_rules",
            sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="60"),
        )
        op.execute("UPDATE alert_rules SET conditions = '{\"op\": \"and\", \"items\": []}'")
        op.execute("UPDATE alert_rules SET kind = 'schedule' WHERE kind = 'report'")
        op.execute("UPDATE alert_rules SET kind = 'trigger' WHERE kind = 'deposit'")

    live = _columns(sa.inspect(bind), "alert_rules")
    for name, _, _, _ in _ADDED:
        if name in live:
            op.drop_column("alert_rules", name)

    if sa.inspect(bind).has_table("keitaro_conversions"):
        op.drop_table("keitaro_conversions")
