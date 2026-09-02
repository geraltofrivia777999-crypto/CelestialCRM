"""Интеграция с Partner Integration Service — депозиты в финансы баеров.

Сервис партнёрок хранит факты «дата — оффер — тег — депозиты». CRM подключается
к нему X-API-Key, привязывает свои офферы к офферам ПП и раскладывает депозиты
в книги баеров: по тегу из ПП находится строка тега в книге, и значения пишутся
в `FinanceTagDay` — те же ячейки, которые раньше заполнялись руками.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0048_partner_integrations"
down_revision = "0047_meta_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partner_integrations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("partner_name", sa.String(length=160), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("schedule_cron", sa.String(length=60)),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_status", sa.String(length=12)),
        sa.Column("last_sync_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_partner_integration_name"),
    )
    op.create_table(
        "partner_offer_mappings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "integration_id",
            sa.Uuid(),
            sa.ForeignKey("partner_integrations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "crm_offer_id",
            sa.Uuid(),
            sa.ForeignKey("offers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("external_offer_id", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "integration_id", "external_offer_id", name="uq_partner_mapping_external"
        ),
    )
    op.create_table(
        "partner_sync_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "integration_id",
            sa.Uuid(),
            sa.ForeignKey("partner_integrations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("records_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("trigger", sa.String(length=10), nullable=False, server_default="manual"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("partner_sync_runs")
    op.drop_table("partner_offer_mappings")
    op.drop_table("partner_integrations")
