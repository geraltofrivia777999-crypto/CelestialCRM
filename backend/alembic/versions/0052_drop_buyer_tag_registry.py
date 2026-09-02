"""Реестр «тег → баер» больше не нужен.

Куда положить депозит, определяет сама финансовая таблица: строка тега под
конкретным оффером в книге конкретного баера — это и есть привязка. Заводить то
же самое вторым списком в настройках означало держать два источника правды,
которые рано или поздно разойдутся.
"""

import sqlalchemy as sa

from alembic import op

revision = "0052_drop_buyer_tag_registry"
down_revision = "0051_offer_partner_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("partner_buyer_tags")


def downgrade() -> None:
    op.create_table(
        "partner_buyer_tags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column("tag_key", sa.String(length=120), nullable=False),
        sa.Column("tag", sa.String(length=120), nullable=False),
        sa.Column(
            "buyer_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column("is_auto", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "tag_key", name="uq_partner_buyer_tag"),
    )
