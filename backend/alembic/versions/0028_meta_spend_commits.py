"""Фиксация расхода Meta за отрезок дня на оффер.

Дневная статистика Meta не знает, что баер лил один оффер с 12:00 до 16:00, а
потом другой. Окно берётся из почасовой разбивки, а сюда пишется, какой кусок
расхода какой кампании на какой оффер отнесли.

Таблица нужна не для отчёта: без неё второй клик по кнопке молча удваивал бы
расход в Медиаборде, а ошибочную привязку нельзя было бы снять.
"""

import sqlalchemy as sa

from alembic import op

revision = "0028_meta_spend_commits"
down_revision = "0027_offer_kpi_template_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("meta_spend_commits"):
        return
    op.create_table(
        "meta_spend_commits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("hour_from", sa.Integer(), nullable=False),
        sa.Column("hour_to", sa.Integer(), nullable=False),
        sa.Column("campaign_external_id", sa.String(length=100), nullable=False),
        sa.Column("campaign_name", sa.String(length=300), nullable=True),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("media_record_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("base_amount", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["meta_ad_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["media_record_id"], ["media_records.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["provider_id"], ["spend_providers.id"]),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_meta_spend_commits_workspace_id", "meta_spend_commits", ["workspace_id"]
    )
    op.create_index(
        "ix_meta_spend_commits_media_record_id", "meta_spend_commits", ["media_record_id"]
    )
    op.create_index(
        "ix_meta_commit_window",
        "meta_spend_commits",
        ["workspace_id", "record_date", "campaign_external_id"],
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("meta_spend_commits"):
        op.drop_table("meta_spend_commits")
