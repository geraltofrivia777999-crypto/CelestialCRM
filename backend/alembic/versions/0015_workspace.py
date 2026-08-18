"""Workspace: задачи и база знаний (ТЗ 8).

Пять базовых статусов канбана заводятся здесь же, а не только в seed: без них
доска пуста и первую задачу создать некуда. Помечены `is_system`, поэтому их
нельзя удалить из интерфейса — переименовать и перекрасить можно.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_workspace"
down_revision = "0014_meta_launch"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("workspace.view", "workspace view"),
    ("workspace.manage", "workspace manage"),
    ("knowledge.view", "knowledge view"),
    ("knowledge.manage", "knowledge manage"),
]
GRANTS = {
    "Administrator": [code for code, _ in PERMISSIONS],
    "Team Lead": ["workspace.view", "workspace.manage", "knowledge.view", "knowledge.manage"],
    "Buyer": ["workspace.view", "knowledge.view"],
    "Finance": ["workspace.view", "knowledge.view"],
}
STATUSES = [
    ("open", "Открыта", "#6A6161", 0, False),
    ("in_progress", "В работе", "#C9821F", 1, False),
    ("review", "На ревью", "#2C4E77", 2, False),
    ("done", "Готово", "#16B57F", 3, True),
    ("archive", "Архив", "#9B9292", 4, True),
]
TABLES = (
    "knowledge_attachments",
    "knowledge_access",
    "knowledge_articles",
    "knowledge_sections",
    "task_templates",
    "task_assignees",
    "tasks",
    "task_fields",
    "task_statuses",
)

TASK_PRIORITY_VALUES = ("low", "medium", "high", "critical")
ARTICLE_STATUS_VALUES = ("draft", "published", "archived")


def _enum_type(name: str, values: tuple[str, ...]) -> sa.types.TypeEngine:
    """Return the same type the ORM uses without recreating PostgreSQL enums.

    Existing installations are upgraded from a schema that has neither enum.
    Keeping these columns as plain VARCHAR would look harmless in the migration,
    but asyncpg renders ORM parameters as ``::taskpriority``/``::articlestatus``.
    The first task or article write would therefore fail on PostgreSQL.
    """
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name, native_enum=False)


def _create_enum_types() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    postgresql.ENUM(*TASK_PRIORITY_VALUES, name="taskpriority").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*ARTICLE_STATUS_VALUES, name="articlestatus").create(
        bind, checkfirst=True
    )


def upgrade() -> None:
    # Как и в 0013/0014: на чистой базе схему поднимает metadata.create_all в 0001.
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    workspace_tables = set(TABLES)
    present = tables & workspace_tables
    if present and present != workspace_tables:
        missing = ", ".join(sorted(workspace_tables - present))
        raise RuntimeError(
            "Workspace schema is only partially present; refusing to continue. "
            f"Missing tables: {missing}"
        )
    if not present:
        _create_tables()
    _grant_permissions()
    _seed_statuses()


def _create_tables() -> None:
    _create_enum_types()
    task_priority = _enum_type("taskpriority", TASK_PRIORITY_VALUES)
    article_status = _enum_type("articlestatus", ARTICLE_STATUS_VALUES)

    op.create_table(
        "task_statuses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="#9B9292"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_terminal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("workspace_id", "code", name="uq_task_status_code"),
    )
    op.create_index("ix_task_statuses_workspace_id", "task_statuses", ["workspace_id"])
    op.create_index("ix_task_statuses_position", "task_statuses", ["position"])

    op.create_table(
        "task_fields",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_task_field_name"),
    )
    op.create_index("ix_task_fields_workspace_id", "task_fields", ["workspace_id"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status_id",
            sa.Uuid(),
            sa.ForeignKey("task_statuses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("priority", task_priority, nullable=False, server_default="medium"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "custom_values", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_tasks_workspace_id", "tasks", ["workspace_id"])
    op.create_index("ix_tasks_status_id", "tasks", ["status_id"])
    op.create_index("ix_tasks_due_date", "tasks", ["due_date"])
    op.create_index("ix_tasks_priority", "tasks", ["priority"])
    op.create_index("ix_tasks_board", "tasks", ["workspace_id", "status_id", "position"])

    op.create_table(
        "task_assignees",
        sa.Column(
            "task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_task_assignees_user_id", "task_assignees", ["user_id"])

    op.create_table(
        "task_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", task_priority, nullable=False, server_default="medium"),
        sa.Column(
            "status_id", sa.Uuid(), sa.ForeignKey("task_statuses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("due_in_days", sa.Integer(), nullable=True),
        sa.Column(
            "assignee_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "custom_values", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_task_template_name"),
    )
    op.create_index("ix_task_templates_workspace_id", "task_templates", ["workspace_id"])

    op.create_table(
        "knowledge_sections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id", sa.Uuid(),
            sa.ForeignKey("knowledge_sections.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("icon", sa.String(length=16), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_knowledge_sections_workspace_id", "knowledge_sections", ["workspace_id"])
    op.create_index(
        "ix_knowledge_sections_tree", "knowledge_sections",
        ["workspace_id", "parent_id", "position"],
    )

    op.create_table(
        "knowledge_articles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_id", sa.Uuid(),
            sa.ForeignKey("knowledge_sections.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column(
            "parent_id", sa.Uuid(),
            sa.ForeignKey("knowledge_articles.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("blocks", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", article_status, nullable=False, server_default="draft"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_knowledge_articles_workspace_id", "knowledge_articles", ["workspace_id"])
    op.create_index("ix_knowledge_articles_section_id", "knowledge_articles", ["section_id"])
    op.create_index("ix_knowledge_articles_status", "knowledge_articles", ["status"])
    op.create_index(
        "ix_knowledge_articles_section", "knowledge_articles", ["section_id", "position"]
    )

    op.create_table(
        "knowledge_access",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_id", sa.Uuid(),
            sa.ForeignKey("knowledge_sections.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("can_view", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("can_create", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("can_edit", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("can_delete", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("can_manage", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("section_id", "role_id", name="uq_knowledge_access_role"),
    )
    op.create_index("ix_knowledge_access_workspace_id", "knowledge_access", ["workspace_id"])
    op.create_index("ix_knowledge_access_section_id", "knowledge_access", ["section_id"])
    op.create_index("ix_knowledge_access_role_id", "knowledge_access", ["role_id"])

    op.create_table(
        "knowledge_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "article_id", sa.Uuid(),
            sa.ForeignKey("knowledge_articles.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("file_name", sa.String(length=300), nullable=False),
        sa.Column(
            "mime_type", sa.String(length=120), nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(length=400), nullable=False),
        sa.Column(
            "uploaded_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_knowledge_attachments_workspace_id", "knowledge_attachments", ["workspace_id"]
    )
    op.create_index(
        "ix_knowledge_attachments_article_id", "knowledge_attachments", ["article_id"]
    )


def _seed_statuses() -> None:
    """Завести пять базовых колонок каждому воркспейсу, где их ещё нет."""
    bind = op.get_bind()
    statuses = sa.table(
        "task_statuses",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("color", sa.String()),
        sa.column("position", sa.Integer()),
        sa.column("is_system", sa.Boolean()),
        sa.column("is_terminal", sa.Boolean()),
    )
    workspaces = sa.table("workspaces", sa.column("id", sa.Uuid()))
    rows = []
    for (workspace_id,) in bind.execute(sa.select(workspaces.c.id)):
        for code, name, color, position, terminal in STATUSES:
            exists = bind.execute(
                sa.select(statuses.c.id).where(
                    statuses.c.workspace_id == workspace_id, statuses.c.code == code
                )
            ).first()
            if exists is None:
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "workspace_id": workspace_id,
                        "code": code,
                        "name": name,
                        "color": color,
                        "position": position,
                        "is_system": True,
                        "is_terminal": terminal,
                    }
                )
    if rows:
        bind.execute(sa.insert(statuses), rows)


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
    # Roles are unique only inside a workspace. A production database may contain
    # several workspaces with their own "Buyer"/"Team Lead" roles, so selecting a
    # single row by name would fail with MultipleResultsFound and abort migration.
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
    codes = ", ".join(f"'{code}'" for code, _ in PERMISSIONS)
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        f"(SELECT id FROM permissions WHERE code IN ({codes}))"
    )
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
    for table in TABLES:
        op.drop_table(table)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(*ARTICLE_STATUS_VALUES, name="articlestatus").drop(
            bind, checkfirst=True
        )
        postgresql.ENUM(*TASK_PRIORITY_VALUES, name="taskpriority").drop(
            bind, checkfirst=True
        )
