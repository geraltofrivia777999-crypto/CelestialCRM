"""Маршрутизация депозитов ПП по тегу баера.

Сервис партнёрок отдаёт тег сырой строкой из sub1/sub2 и не знает про наших
людей. Без реестра «тег → баер» раскладка определяла получателя по офферу, а у
оффера бывает несколько баеров: один и тот же депозит попадал в книгу каждому и
задваивал доход. Реестр закрывает это, а неразобранные теги копятся отдельно —
чтобы деньги не пропадали молча, пока тег никому не привязан.
"""

import sqlalchemy as sa

from alembic import op

revision = "0049_partner_buyer_tags"
down_revision = "0048_partner_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "partner_integrations", sa.Column("external_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "partner_sync_runs",
        sa.Column("records_pending", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "partner_sync_runs",
        sa.Column("records_skipped", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("partner_sync_runs", sa.Column("details", sa.JSON(), nullable=True))

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

    op.create_table(
        "partner_pending_tags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column("tag_key", sa.String(length=120), nullable=False),
        sa.Column("tag", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.String(length=12), nullable=False, server_default="no_buyer"),
        sa.Column("facts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deposits_total", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("sample_offer_id", sa.Uuid(), sa.ForeignKey("offers.id", ondelete="SET NULL")),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("workspace_id", "tag_key", name="uq_partner_pending_tag"),
    )


def downgrade() -> None:
    op.drop_table("partner_pending_tags")
    op.drop_table("partner_buyer_tags")
    op.drop_column("partner_sync_runs", "details")
    op.drop_column("partner_sync_runs", "records_skipped")
    op.drop_column("partner_sync_runs", "records_pending")
    op.drop_column("partner_integrations", "external_id")
