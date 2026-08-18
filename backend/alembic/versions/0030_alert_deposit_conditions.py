"""Условия уведомления о депозитах вместо двух списков отметок.

Отмеченные галочками группы и офферы означали ровно одно: «входит в список».
«Всё, кроме этой группы» или «оффер содержит Vulkan» так не выразить, а именно
это и нужно, когда список офферов растёт каждую неделю.

Прежние отметки переезжают в условия «в списке» — правило продолжает работать
ровно так же, как работало.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0030_alert_deposit_conditions"
down_revision = "0029_alert_deposit_report"
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _listed(raw: object) -> list[str]:
    parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
    return [str(item) for item in parsed if str(item).strip()]


def upgrade() -> None:
    bind = op.get_bind()

    if "campaign_group_name" not in _columns(sa.inspect(bind), "keitaro_conversions"):
        op.add_column(
            "keitaro_conversions",
            sa.Column("campaign_group_name", sa.String(length=300), nullable=True),
        )

    live = _columns(sa.inspect(bind), "alert_rules")
    if "conditions" not in live:
        op.add_column("alert_rules", sa.Column("conditions", sa.JSON(), nullable=True))

    if {"campaign_group_ids", "offer_ids"} <= live:
        rows = bind.execute(
            sa.text("SELECT id, campaign_group_ids, offer_ids FROM alert_rules")
        ).fetchall()
        for row in rows:
            items = []
            groups = _listed(row.campaign_group_ids)
            offers = _listed(row.offer_ids)
            if groups:
                items.append(
                    {"field": "campaign_group", "operator": "in", "values": groups}
                )
            if offers:
                items.append({"field": "offer", "operator": "in", "values": offers})
            bind.execute(
                sa.text("UPDATE alert_rules SET conditions = :tree WHERE id = :id"),
                {"tree": json.dumps({"op": "and", "items": items}), "id": row.id},
            )

    op.execute(
        "UPDATE alert_rules SET conditions = '{\"op\": \"and\", \"items\": []}' "
        "WHERE conditions IS NULL"
    )

    live = _columns(sa.inspect(bind), "alert_rules")
    for name in ("campaign_group_ids", "offer_ids"):
        if name in live:
            op.drop_column("alert_rules", name)


def downgrade() -> None:
    bind = op.get_bind()
    live = _columns(sa.inspect(bind), "alert_rules")
    if "campaign_group_ids" not in live:
        op.add_column("alert_rules", sa.Column("campaign_group_ids", sa.JSON(), nullable=True))
        op.add_column("alert_rules", sa.Column("offer_ids", sa.JSON(), nullable=True))

    # Назад помещается только «в списке» — отрицания и «содержит» теряются.
    rows = bind.execute(sa.text("SELECT id, conditions FROM alert_rules")).fetchall()
    for row in rows:
        raw = row.conditions
        tree = json.loads(raw) if isinstance(raw, str) else (raw or {})
        groups, offers = [], []
        for item in tree.get("items") or []:
            if item.get("operator") != "in":
                continue
            if item.get("field") == "campaign_group":
                groups = [str(value) for value in item.get("values") or []]
            elif item.get("field") == "offer":
                offers = [str(value) for value in item.get("values") or []]
        bind.execute(
            sa.text(
                "UPDATE alert_rules SET campaign_group_ids = :groups, offer_ids = :offers "
                "WHERE id = :id"
            ),
            {"groups": json.dumps(groups), "offers": json.dumps(offers), "id": row.id},
        )

    if "conditions" in _columns(sa.inspect(bind), "alert_rules"):
        op.drop_column("alert_rules", "conditions")
    if "campaign_group_name" in _columns(sa.inspect(bind), "keitaro_conversions"):
        op.drop_column("keitaro_conversions", "campaign_group_name")
