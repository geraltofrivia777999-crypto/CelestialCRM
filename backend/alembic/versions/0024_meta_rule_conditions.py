"""Автоправила: уровень объекта, статусы, окно, частота и список условий.

Правило умело ровно одно условие по кампании. Теперь у него есть уровень
(кампания, адсет или объявление), фильтр по статусу объекта, пресет периода
статистики, частота проверки и список условий, соединённых И.

Одиночное `metric/operator/threshold` переезжает в `conditions` первым и
единственным элементом, `window_days` — в ближайший пресет окна. Старые колонки
удаляются: два источника одного и того же условия рано или поздно разошлись бы.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0024_meta_rule_conditions"
down_revision = "0023_meta_social_graph"
branch_labels = None
depends_on = None

# Ближайший пресет для прежнего окна в днях.
WINDOW_BY_DAYS = {1: "today", 3: "last_3d", 7: "last_7d", 30: "last_30d"}
DAYS_BY_WINDOW = {"today": 1, "yesterday": 1, "last_3d": 3, "last_7d": 7, "last_30d": 30}


def _columns(inspector: sa.Inspector) -> set[str]:
    return {column["name"] for column in inspector.get_columns("meta_rules")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(sa.inspect(bind))

    if "level" not in columns:
        op.add_column(
            "meta_rules",
            sa.Column(
                "level", sa.String(length=10), nullable=False, server_default="campaign"
            ),
        )
    if "entity_status" not in columns:
        op.add_column(
            "meta_rules",
            sa.Column(
                "entity_status", sa.String(length=10), nullable=False, server_default="active"
            ),
        )
    if "window" not in columns:
        op.add_column(
            "meta_rules",
            sa.Column("window", sa.String(length=12), nullable=False, server_default="today"),
        )
    if "conditions" not in columns:
        op.add_column("meta_rules", sa.Column("conditions", sa.JSON(), nullable=True))
    if "frequency_minutes" not in columns:
        op.add_column(
            "meta_rules",
            sa.Column("frequency_minutes", sa.Integer(), nullable=False, server_default="60"),
        )
    if "last_checked_at" not in columns:
        op.add_column(
            "meta_rules", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True)
        )

    # Переносим одиночное условие, пока старые колонки ещё на месте.
    if {"metric", "operator", "threshold"} <= columns:
        rows = bind.execute(
            sa.text(
                "SELECT id, metric, operator, threshold, window_days FROM meta_rules"
            )
        ).fetchall()
        for row in rows:
            payload = json.dumps(
                [
                    {
                        "metric": row.metric or "roi",
                        "operator": row.operator or "lt",
                        "value": str(row.threshold if row.threshold is not None else 0),
                    }
                ]
            )
            window = WINDOW_BY_DAYS.get(int(row.window_days or 1), "last_7d")
            bind.execute(
                sa.text(
                    "UPDATE meta_rules SET conditions = :payload, window = :window "
                    "WHERE id = :id"
                ),
                {"payload": payload, "window": window, "id": row.id},
            )

    op.execute("UPDATE meta_rules SET conditions = '[]' WHERE conditions IS NULL")
    op.alter_column("meta_rules", "conditions", nullable=False)

    columns = _columns(sa.inspect(bind))
    for name in ("metric", "operator", "threshold", "window_days"):
        if name in columns:
            op.drop_column("meta_rules", name)


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(sa.inspect(bind))

    if "metric" not in columns:
        op.add_column(
            "meta_rules",
            sa.Column("metric", sa.String(length=20), nullable=False, server_default="roi"),
        )
    if "operator" not in columns:
        op.add_column(
            "meta_rules",
            sa.Column("operator", sa.String(length=4), nullable=False, server_default="lt"),
        )
    if "threshold" not in columns:
        op.add_column(
            "meta_rules",
            sa.Column(
                "threshold", sa.Numeric(18, 2), nullable=False, server_default="0"
            ),
        )
    if "window_days" not in columns:
        op.add_column(
            "meta_rules",
            sa.Column("window_days", sa.Integer(), nullable=False, server_default="1"),
        )

    # Назад помещается только первое условие — остальные теряются.
    rows = bind.execute(sa.text("SELECT id, conditions, window FROM meta_rules")).fetchall()
    for row in rows:
        raw = row.conditions
        parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
        first = parsed[0] if parsed else {}
        bind.execute(
            sa.text(
                "UPDATE meta_rules SET metric = :metric, operator = :operator, "
                "threshold = :threshold, window_days = :days WHERE id = :id"
            ),
            {
                "metric": first.get("metric") or "roi",
                "operator": first.get("operator") or "lt",
                "threshold": first.get("value") or 0,
                "days": DAYS_BY_WINDOW.get(row.window or "today", 1),
                "id": row.id,
            },
        )

    for name in ("last_checked_at", "frequency_minutes", "conditions", "window",
                 "entity_status", "level"):
        if name in _columns(sa.inspect(bind)):
            op.drop_column("meta_rules", name)
