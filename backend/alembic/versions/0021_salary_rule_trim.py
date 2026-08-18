"""Правило зарплаты без периодичности, штрафов и бонусов.

Периодичность компонента («каждый период» / «один раз») и галочки «вычитать
штрафы» / «плюс бонусы» убраны из формы: штрафы и бонусы в CRM не заводятся,
а разовая выплата — это не свойство формулы, а отдельное начисление. Поля,
которые ничего не меняют, хуже отсутствующих: их заполняют и ждут эффекта.

Привязка «все сотрудники» тоже убрана — правило всегда чьё-то. Роли у таких
строк нет, поэтому они выключаются, а не переносятся: показать выключенное
правило честнее, чем молча применить его к каждому сотруднику или удалить.
"""

import sqlalchemy as sa

from alembic import op

revision = "0021_salary_rule_trim"
down_revision = "0020_template_fields_only"
branch_labels = None
depends_on = None

RULES = "salary_rules"
COMPONENTS = "salary_components"


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    rules = _columns(RULES)
    if rules:
        op.execute(
            sa.text(
                f"UPDATE {RULES} SET status = 'inactive', scope = 'role' "
                "WHERE scope = 'all'"
            )
        )
        for column in ("subtract_penalties", "add_bonuses"):
            if column in rules:
                op.drop_column(RULES, column)
    if "periodicity" in _columns(COMPONENTS):
        op.drop_column(COMPONENTS, "periodicity")


def downgrade() -> None:
    rules = _columns(RULES)
    if rules:
        for column in ("subtract_penalties", "add_bonuses"):
            if column not in rules:
                op.add_column(
                    RULES,
                    sa.Column(
                        column, sa.Boolean(), nullable=False, server_default="false"
                    ),
                )
    if _columns(COMPONENTS) and "periodicity" not in _columns(COMPONENTS):
        op.add_column(
            COMPONENTS,
            sa.Column(
                "periodicity", sa.String(length=16), nullable=False, server_default="period"
            ),
        )
