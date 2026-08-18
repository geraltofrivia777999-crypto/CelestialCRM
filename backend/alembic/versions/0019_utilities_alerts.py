"""Модуль «Утилиты»: Alert и CAP — ТЗ 9.

Уведомления уходят в Telegram, поэтому здесь же появляется бот воркспейса.
Токен хранится зашифрованным: по нему можно писать от имени команды в любой
чат, куда бота добавили.

Журнал событий заводится сразу, а не «когда понадобится»: когда алерт не
пришёл, отличить «правило не сработало» от «Telegram не принял» больше нечем.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019_utilities_alerts"
down_revision = "0018_salary_rules"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("utilities.view", "utilities view"),
    ("utilities.manage", "utilities manage"),
]
GRANTS = {
    "Administrator": ["utilities.view", "utilities.manage"],
    "Team Lead": ["utilities.view", "utilities.manage"],
    "Buyer": ["utilities.view"],
    "Finance": ["utilities.view"],
}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _status_type():
    """Существующий тип `status`, а не попытка создать его заново."""
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM("active", "inactive", name="status", create_type=False)
    return sa.Enum("active", "inactive", name="status", native_enum=False)


def upgrade() -> None:
    tables = _tables()

    if "telegram_bots" not in tables:
        op.create_table(
            "telegram_bots",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.Uuid(),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("username", sa.String(length=120)),
            sa.Column("token_encrypted", sa.String(length=500), nullable=False),
            sa.Column("status", _status_type(), nullable=False, server_default="active"),
            sa.Column("checked_at", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.Text()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("workspace_id", name="uq_telegram_bot_workspace"),
        )
        op.create_index("ix_telegram_bots_workspace_id", "telegram_bots", ["workspace_id"])

    if "alert_channels" not in tables:
        op.create_table(
            "alert_channels",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.Uuid(),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("chat_id", sa.String(length=64), nullable=False),
            sa.Column("thread_id", sa.String(length=32)),
            sa.Column("status", _status_type(), nullable=False, server_default="active"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_alert_channels_workspace_id", "alert_channels", ["workspace_id"]
        )

    if "alert_rules" not in tables:
        op.create_table(
            "alert_rules",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.Uuid(),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("status", _status_type(), nullable=False, server_default="active"),
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="trigger"),
            sa.Column(
                "channel_id",
                sa.Uuid(),
                sa.ForeignKey("alert_channels.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("metric", sa.String(length=40), nullable=False, server_default="spend"),
            sa.Column("window", sa.String(length=16), nullable=False, server_default="today"),
            sa.Column("comparison", sa.String(length=8), nullable=False, server_default="gt"),
            sa.Column("threshold", sa.Numeric(18, 4)),
            sa.Column(
                "per_user", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
            sa.Column("offer_id", sa.Uuid(), sa.ForeignKey("offers.id", ondelete="CASCADE")),
            sa.Column("run_at", sa.String(length=5)),
            sa.Column(
                "frequency", sa.String(length=16), nullable=False, server_default="daily"
            ),
            sa.Column("message_template", sa.Text()),
            sa.Column(
                "cooldown_minutes", sa.Integer(), nullable=False, server_default="60"
            ),
            sa.Column("last_fired_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index("ix_alert_rules_workspace_id", "alert_rules", ["workspace_id"])
        op.create_index("ix_alert_rules_channel_id", "alert_rules", ["channel_id"])
        op.create_index(
            "ix_alert_rules_workspace_status", "alert_rules", ["workspace_id", "status"]
        )

    if "cap_rules" not in tables:
        op.create_table(
            "cap_rules",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.Uuid(),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("status", _status_type(), nullable=False, server_default="active"),
            sa.Column(
                "channel_id",
                sa.Uuid(),
                sa.ForeignKey("alert_channels.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("offer_id", sa.Uuid(), sa.ForeignKey("offers.id", ondelete="CASCADE")),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
            sa.Column("metric", sa.String(length=40), nullable=False, server_default="ftd"),
            sa.Column(
                "limit_value", sa.Numeric(18, 4), nullable=False, server_default="0"
            ),
            sa.Column("period", sa.String(length=16), nullable=False, server_default="day"),
            sa.Column("notify_at", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column(
                "notified_percent", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("notified_period", sa.String(length=24)),
            sa.Column("last_fired_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index("ix_cap_rules_workspace_id", "cap_rules", ["workspace_id"])
        op.create_index("ix_cap_rules_channel_id", "cap_rules", ["channel_id"])
        op.create_index("ix_cap_rules_offer_id", "cap_rules", ["offer_id"])
        op.create_index(
            "ix_cap_rules_workspace_status", "cap_rules", ["workspace_id", "status"]
        )

    if "alert_events" not in tables:
        op.create_table(
            "alert_events",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.Uuid(),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "alert_rule_id",
                sa.Uuid(),
                sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
            ),
            sa.Column(
                "cap_rule_id", sa.Uuid(), sa.ForeignKey("cap_rules.id", ondelete="CASCADE")
            ),
            sa.Column("rule_name", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="trigger"),
            sa.Column("value", sa.Numeric(18, 4)),
            sa.Column("message", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "delivered", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("error", sa.Text()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index("ix_alert_events_workspace_id", "alert_events", ["workspace_id"])
        op.create_index("ix_alert_events_alert_rule_id", "alert_events", ["alert_rule_id"])
        op.create_index("ix_alert_events_cap_rule_id", "alert_events", ["cap_rule_id"])
        op.create_index(
            "ix_alert_events_workspace_time", "alert_events", ["workspace_id", "created_at"]
        )

    _grant_permissions()


def _grant_permissions() -> None:
    bind = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("description", sa.String()),
    )
    roles = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("name", sa.String()))
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )

    permission_ids: dict[str, uuid.UUID] = {}
    for code, description in PERMISSIONS:
        existing = bind.execute(
            sa.select(permissions.c.id).where(permissions.c.code == code)
        ).scalar_one_or_none()
        if existing is None:
            existing = uuid.uuid4()
            bind.execute(
                sa.insert(permissions).values(id=existing, code=code, description=description)
            )
        permission_ids[code] = existing

    links = []
    # Роли уникальны только внутри воркспейса: в базе может быть несколько
    # «Buyer», и выбирать одну строку по имени нельзя.
    for role_name, codes in GRANTS.items():
        role_ids = bind.execute(
            sa.select(roles.c.id).where(roles.c.name == role_name)
        ).scalars()
        for role_id in role_ids:
            for code in codes:
                already = bind.execute(
                    sa.select(role_permissions.c.role_id).where(
                        role_permissions.c.role_id == role_id,
                        role_permissions.c.permission_id == permission_ids[code],
                    )
                ).first()
                if already is None:
                    links.append({"role_id": role_id, "permission_id": permission_ids[code]})
    if links:
        bind.execute(sa.insert(role_permissions), links)


def downgrade() -> None:
    tables = _tables()
    for table in ("alert_events", "cap_rules", "alert_rules", "alert_channels", "telegram_bots"):
        if table in tables:
            op.drop_table(table)
    codes = ", ".join(f"'{code}'" for code, _ in PERMISSIONS)
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        f"(SELECT id FROM permissions WHERE code IN ({codes}))"
    )
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
