"""Справочник тиров стран, гео у оффера книги.

Тир перестал быть свойством оффера и стал свойством страны: в книге выбирается
гео, а тир к нему подставляет справочник «Настройки → Тиры стран». Иначе два
оффера на одну страну могли разойтись по тирам, и сводка переставала сходиться
сама с собой.

Ранее проставленные вручную тиры перенести не во что: из «T1» страна не
восстанавливается. Колонка удаляется, гео проставляется заново — до этого такие
офферы идут в сводке строкой «Без тира».
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0033_country_tiers"
down_revision = "0032_finance_costs_back_to_days"
branch_labels = None
depends_on = None

# Стартовый список Tier-1 — тот же, что в `app.services.country_tiers`.
# Дублируется намеренно: миграция не должна меняться вслед за кодом.
DEFAULT_TIER_1 = (
    "US", "CA", "GB", "DE", "FR", "AT", "CH", "NO", "SE", "DK",
    "FI", "IS", "NL", "BE", "LU", "IE", "IT", "ES", "CZ", "PL",
    "PT", "SI", "AU", "NZ", "JP", "SG", "AE", "KR", "IL", "SA",
)


def upgrade() -> None:
    op.create_table(
        "country_tiers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("tier", sa.String(length=4), nullable=False),
        sa.UniqueConstraint("workspace_id", "code", name="uq_country_tier_code"),
    )
    op.create_index("ix_country_tiers_workspace_id", "country_tiers", ["workspace_id"])
    op.add_column(
        "finance_book_offers", sa.Column("geo", sa.String(length=12), nullable=True)
    )
    op.drop_column("finance_book_offers", "tier")
    _seed_tier_one()


def downgrade() -> None:
    op.add_column(
        "finance_book_offers", sa.Column("tier", sa.String(length=4), nullable=True)
    )
    op.drop_column("finance_book_offers", "geo")
    op.drop_index("ix_country_tiers_workspace_id", table_name="country_tiers")
    op.drop_table("country_tiers")


def _seed_tier_one() -> None:
    """Разложить базовый список по всем воркспейсам, где справочника ещё нет."""
    bind = op.get_bind()
    workspaces = sa.table("workspaces", sa.column("id", sa.Uuid()))
    tiers = sa.table(
        "country_tiers",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("tier", sa.String()),
    )
    rows = [
        {
            "id": uuid.uuid4(),
            "workspace_id": workspace_id,
            "code": code,
            "tier": "T1",
        }
        for (workspace_id,) in bind.execute(sa.select(workspaces.c.id))
        for code in DEFAULT_TIER_1
    ]
    if rows:
        op.bulk_insert(tiers, rows)
