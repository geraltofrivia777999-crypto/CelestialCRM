"""Оффер знает свою интеграцию с ПП.

Номер оффера у партнёрки принадлежит одной программе. Пока интеграция была
одна, это не мешало; со второй тот же номер означает уже другой оффер, и без
явной ссылки CRM отдавала бы его обеим — депозиты могли приехать не туда.
"""

import sqlalchemy as sa

from alembic import op

revision = "0058_offer_partner_integration"
down_revision = "0057_drop_partner_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column("partner_integration_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_offers_partner_integration_id", "offers", ["partner_integration_id"]
    )
    op.create_foreign_key(
        "fk_offers_partner_integration",
        "offers",
        "partner_integrations",
        ["partner_integration_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # У кого интеграция ровно одна — проставляем её сразу: до этой миграции
    # офферы и так уходили ей одной, и просить заполнить поле руками было бы
    # требованием подтвердить то, что уже верно.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT workspace_id, MIN(id::text) AS only_id, COUNT(*) AS total "
            "FROM partner_integrations GROUP BY workspace_id HAVING COUNT(*) = 1"
        )
    ).fetchall()
    for workspace_id, only_id, _total in rows:
        bind.execute(
            sa.text(
                "UPDATE offers SET partner_integration_id = CAST(:pid AS uuid) "
                "WHERE workspace_id = :ws AND connection_id IS NULL "
                "AND external_id IS NOT NULL AND external_id <> ''"
            ),
            {"pid": only_id, "ws": workspace_id},
        )


def downgrade() -> None:
    op.drop_constraint("fk_offers_partner_integration", "offers", type_="foreignkey")
    op.drop_index("ix_offers_partner_integration_id", table_name="offers")
    op.drop_column("offers", "partner_integration_id")
