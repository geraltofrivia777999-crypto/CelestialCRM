"""KPI и комментарий у оффера, шаблон задачи по умолчанию.

KPI и комментарий — длинный текст, который читают не в таблице, а открыв
конкретный оффер, поэтому это `Text`, а не короткая строка.

`is_default` у шаблона один на воркспейс: команда работает по одному брифу, и
выбирать его вручную в самом частом действии раздела — лишний шаг.
"""

import sqlalchemy as sa

from alembic import op

revision = "0027_offer_kpi_template_default"
down_revision = "0026_alert_conditions"
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    offers = _columns(inspector, "offers")
    if "kpi" not in offers:
        op.add_column("offers", sa.Column("kpi", sa.Text(), nullable=True))
    if "comment" not in offers:
        op.add_column("offers", sa.Column("comment", sa.Text(), nullable=True))

    templates = _columns(inspector, "task_templates")
    if "is_default" not in templates:
        op.add_column(
            "task_templates",
            sa.Column(
                "is_default", sa.Boolean(), nullable=False, server_default="false"
            ),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "is_default" in _columns(inspector, "task_templates"):
        op.drop_column("task_templates", "is_default")
    offers = _columns(inspector, "offers")
    for name in ("comment", "kpi"):
        if name in offers:
            op.drop_column("offers", name)
