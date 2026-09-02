"""Числовой ключ оффера для Partner Integration Service.

Сервис принимает `crm_offer_id` только целым числом: на наш UUID он отвечает
422 «unable to parse string as an integer», и привязка не заводится — а без неё
оффер не попадает в `/stats`. Поэтому у оффера появляется небольшой стабильный
номер, который мы и отдаём сервису; им же он помечает возвращаемые факты.
"""

import sqlalchemy as sa

from alembic import op

revision = "0054_offer_partner_ref"
down_revision = "0053_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("partner_ref", sa.Integer(), nullable=True))
    op.create_index("ix_offers_partner_ref", "offers", ["partner_ref"])
    # Номера раздаём сразу тем офферам, у которых уже проставлен ID партнёрки:
    # иначе первый же синк после обновления снова упрётся в 422.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, workspace_id FROM offers "
            "WHERE connection_id IS NULL AND external_id IS NOT NULL "
            "AND external_id <> '' ORDER BY created_at, id"
        )
    ).fetchall()
    counters: dict[str, int] = {}
    for offer_id, workspace_id in rows:
        key = str(workspace_id)
        counters[key] = counters.get(key, 0) + 1
        bind.execute(
            sa.text("UPDATE offers SET partner_ref = :ref WHERE id = :id"),
            {"ref": counters[key], "id": offer_id},
        )


def downgrade() -> None:
    op.drop_index("ix_offers_partner_ref", table_name="offers")
    op.drop_column("offers", "partner_ref")
