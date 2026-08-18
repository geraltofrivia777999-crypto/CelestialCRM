"""Именной доступ к разделу базы знаний (ТЗ 8.2).

Раздел «только для Ирины» через роли не описать: пришлось бы заводить роль под
одного человека и следить, чтобы в неё больше никто не попал. Поэтому правило
доступа теперь адресуется либо роли, либо пользователю — заполнена ровно одна
ссылка, `role_id` для этого становится необязательным.
"""

import sqlalchemy as sa

from alembic import op

revision = "0017_knowledge_user_access"
down_revision = "0016_task_field_types"
branch_labels = None
depends_on = None

TABLE = "knowledge_access"


def _columns() -> dict:
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return {}
    return {column["name"]: column for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    columns = _columns()
    if not columns:
        return
    if not columns["role_id"]["nullable"]:
        op.alter_column(TABLE, "role_id", existing_type=sa.Uuid(), nullable=True)
    if "user_id" not in columns:
        op.add_column(TABLE, sa.Column("user_id", sa.Uuid(), nullable=True))
        op.create_index(f"ix_{TABLE}_user_id", TABLE, ["user_id"])
        op.create_foreign_key(
            f"fk_{TABLE}_user_id", TABLE, "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        op.create_unique_constraint(
            "uq_knowledge_access_user", TABLE, ["section_id", "user_id"]
        )


def downgrade() -> None:
    columns = _columns()
    if not columns:
        return
    if "user_id" in columns:
        # Именные правила пропадают вместе с колонкой — снимаем их явно, иначе
        # раздел остался бы закрытым правилом, которого больше не видно.
        op.execute(sa.text(f"DELETE FROM {TABLE} WHERE user_id IS NOT NULL"))
        op.drop_constraint("uq_knowledge_access_user", TABLE, type_="unique")
        op.drop_constraint(f"fk_{TABLE}_user_id", TABLE, type_="foreignkey")
        op.drop_index(f"ix_{TABLE}_user_id", table_name=TABLE)
        op.drop_column(TABLE, "user_id")
    op.alter_column(TABLE, "role_id", existing_type=sa.Uuid(), nullable=False)
