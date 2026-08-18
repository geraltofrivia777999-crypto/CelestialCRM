"""Правила расчёта зарплаты — «Настройки → Расчет ЗП».

До сих пор ставка была зашита в код одной лестницей на всю команду. Реальные
договорённости отличаются: у баера процент от профита ПП, у тимлида — сетка по
профиту команды, у медиабайера сверху фиксированный оклад. Поэтому правило
собирается из компонентов и привязывается к роли, человеку или всем сразу.

Права отдельные (`salary.view`/`salary.manage`), а не `settings.manage`: доступ
к комиссиям агентов и доступ к чужим зарплатам — это разные вещи. Финансисту
дополнительно открывается сам экран настроек, иначе до раздела не дойти.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018_salary_rules"
down_revision = "0017_knowledge_user_access"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("salary.view", "salary view"),
    ("salary.manage", "salary manage"),
]
GRANTS = {
    "Administrator": ["salary.view", "salary.manage"],
    "Team Lead": ["salary.view"],
    "Finance": ["salary.view", "salary.manage", "settings.view"],
}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _status_type():
    """Ссылка на уже существующий тип `status`, а не попытка создать его заново.

    Тип завела первая миграция, и `CREATE TYPE` во второй раз падает с
    DuplicateObject — на этом спотыкается вся миграция целиком.
    """
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM("active", "inactive", name="status", create_type=False)
    return sa.Enum("active", "inactive", name="status", native_enum=False)


def upgrade() -> None:
    tables = _tables()
    if "salary_rules" not in tables:
        op.create_table(
            "salary_rules",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.Uuid(),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column(
                "status",
                _status_type(),
                nullable=False,
                server_default="active",
            ),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="replace"),
            sa.Column("scope", sa.String(length=16), nullable=False, server_default="role"),
            sa.Column(
                "role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE")
            ),
            sa.Column(
                "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")
            ),
            sa.Column("valid_from", sa.Date()),
            sa.Column("valid_to", sa.Date()),
            sa.Column(
                "subtract_penalties",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "add_bonuses", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")
            ),
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
        op.create_index("ix_salary_rules_workspace_id", "salary_rules", ["workspace_id"])
        op.create_index("ix_salary_rules_role_id", "salary_rules", ["role_id"])
        op.create_index("ix_salary_rules_user_id", "salary_rules", ["user_id"])
        op.create_index(
            "ix_salary_rules_workspace_status", "salary_rules", ["workspace_id", "status"]
        )

    if "salary_components" not in tables:
        op.create_table(
            "salary_components",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "rule_id",
                sa.Uuid(),
                sa.ForeignKey("salary_rules.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("base", sa.String(length=40)),
            sa.Column("percent", sa.Numeric(9, 4)),
            sa.Column("amount", sa.Numeric(18, 4)),
            sa.Column("tiers", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column(
                "periodicity", sa.String(length=16), nullable=False, server_default="period"
            ),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index("ix_salary_components_rule_id", "salary_components", ["rule_id"])

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

    wanted = {code for codes in GRANTS.values() for code in codes}
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
    # settings.view заведён прошлыми миграциями — здесь он только выдаётся.
    for code in wanted - set(permission_ids):
        found = bind.execute(
            sa.select(permissions.c.id).where(permissions.c.code == code)
        ).scalar_one_or_none()
        if found is not None:
            permission_ids[code] = found

    links = []
    # Роли уникальны только внутри воркспейса: в базе может быть несколько
    # «Finance», и выбирать одну строку по имени нельзя.
    for role_name, codes in GRANTS.items():
        role_ids = bind.execute(
            sa.select(roles.c.id).where(roles.c.name == role_name)
        ).scalars()
        for role_id in role_ids:
            for code in codes:
                if code not in permission_ids:
                    continue
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
    if "salary_components" in tables:
        op.drop_table("salary_components")
    if "salary_rules" in tables:
        op.drop_table("salary_rules")
    codes = ", ".join(f"'{code}'" for code, _ in PERMISSIONS)
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        f"(SELECT id FROM permissions WHERE code IN ({codes}))"
    )
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
