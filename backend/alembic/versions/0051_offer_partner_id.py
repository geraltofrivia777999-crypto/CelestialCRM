"""ID оффера в ПП переезжает в сам оффер.

Раньше связь «оффер CRM → оффер партнёрки» жила отдельной таблицей и заводилась
в настройках. Это лишний шаг: оффер и так заводят в разделе «Оффера», и его ID
у партнёрки — такое же его свойство, как гео или ставка. Теперь он хранится в
`offers.external_id`, а таблица привязок не нужна.

Существующие привязки переносятся в офферы, чтобы синхронизация не потеряла их
на ровном месте.
"""

import sqlalchemy as sa

from alembic import op

revision = "0051_offer_partner_id"
down_revision = "0050_recruitment_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _move_mappings_into_offers()
    op.drop_table("partner_offer_mappings")


def _move_mappings_into_offers() -> None:
    """Перенести привязки в офферы.

    Пишем только ручным офферам (`connection_id IS NULL`): у синхронизированных
    из Keitaro `external_id` принадлежит трекеру, и перезаписать его значило бы
    сломать синхронизацию.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT crm_offer_id, external_offer_id FROM partner_offer_mappings"
        )
    ).fetchall()
    for crm_offer_id, external_offer_id in rows:
        if not external_offer_id:
            continue
        bind.execute(
            sa.text(
                "UPDATE offers SET external_id = :external "
                "WHERE id = :offer_id AND connection_id IS NULL"
            ),
            {"external": str(external_offer_id)[:100], "offer_id": crm_offer_id},
        )


def downgrade() -> None:
    op.create_table(
        "partner_offer_mappings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "integration_id", sa.Uuid(),
            sa.ForeignKey("partner_integrations.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column(
            "crm_offer_id", sa.Uuid(),
            sa.ForeignKey("offers.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column("external_offer_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "integration_id", "external_offer_id", name="uq_partner_mapping_external"
        ),
    )
