"""Alert: дерево условий И/ИЛИ и область группировки.

Правило умело ровно одно условие «показатель, знак, порог» и знало только два
среза: вся команда либо каждый баер отдельно. На практике уведомление звучит
как «ROI ниже 20 при расходе выше 500 — по каждому офферу», и собрать это из
одного условия было нельзя.

Старые правила переезжают без потерь: единственное условие становится корнем
дерева, а `per_user` превращается в область «по баерам».
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0026_alert_conditions"
down_revision = "0025_cap_multi_offer"
branch_labels = None
depends_on = None

_OLD = ("metric", "comparison", "threshold", "per_user")


def _columns(inspector: sa.Inspector) -> set[str]:
    return {column["name"] for column in inspector.get_columns("alert_rules")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(sa.inspect(bind))

    if "conditions" not in columns:
        op.add_column("alert_rules", sa.Column("conditions", sa.JSON(), nullable=True))
    if "group_by" not in columns:
        op.add_column(
            "alert_rules",
            sa.Column(
                "group_by", sa.String(length=16), nullable=False, server_default="global"
            ),
        )

    if {"metric", "comparison", "threshold"} <= columns:
        rows = bind.execute(
            sa.text(
                "SELECT id, metric, comparison, threshold, per_user FROM alert_rules"
            )
        ).fetchall()
        for row in rows:
            items = []
            if row.metric and row.threshold is not None:
                items.append(
                    {
                        "metric": row.metric,
                        "comparison": row.comparison or "gt",
                        "value": float(row.threshold),
                    }
                )
            bind.execute(
                sa.text(
                    "UPDATE alert_rules SET conditions = :tree, group_by = :group "
                    "WHERE id = :id"
                ),
                {
                    "tree": json.dumps({"op": "and", "items": items}),
                    "group": "buyer" if row.per_user else "global",
                    "id": row.id,
                },
            )

    op.execute(
        "UPDATE alert_rules SET conditions = '{\"op\": \"and\", \"items\": []}' "
        "WHERE conditions IS NULL"
    )
    op.alter_column("alert_rules", "conditions", nullable=False)

    live = _columns(sa.inspect(bind))
    for name in _OLD:
        if name in live:
            op.drop_column("alert_rules", name)


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(sa.inspect(bind))

    if "metric" not in columns:
        op.add_column(
            "alert_rules",
            sa.Column("metric", sa.String(length=40), nullable=False, server_default="spend"),
        )
        op.add_column(
            "alert_rules",
            sa.Column("comparison", sa.String(length=8), nullable=False, server_default="gt"),
        )
        op.add_column("alert_rules", sa.Column("threshold", sa.Numeric(18, 4), nullable=True))
        op.add_column(
            "alert_rules",
            sa.Column("per_user", sa.Boolean(), nullable=False, server_default="false"),
        )

    # Назад помещается только первое условие верхнего уровня — вложенные группы
    # и всё, что за ними, теряются.
    rows = bind.execute(
        sa.text("SELECT id, conditions, group_by FROM alert_rules")
    ).fetchall()
    for row in rows:
        raw = row.conditions
        tree = json.loads(raw) if isinstance(raw, str) else (raw or {})
        first = next(
            (item for item in tree.get("items") or [] if "metric" in item), None
        )
        bind.execute(
            sa.text(
                "UPDATE alert_rules SET metric = :metric, comparison = :comparison, "
                "threshold = :threshold, per_user = :per_user WHERE id = :id"
            ),
            {
                "metric": (first or {}).get("metric") or "spend",
                "comparison": (first or {}).get("comparison") or "gt",
                "threshold": (first or {}).get("value"),
                "per_user": row.group_by == "buyer",
                "id": row.id,
            },
        )

    for name in ("conditions", "group_by"):
        if name in _columns(sa.inspect(bind)):
            op.drop_column("alert_rules", name)
