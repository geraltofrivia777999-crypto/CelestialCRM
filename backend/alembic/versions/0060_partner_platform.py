"""Платформа ПП в карточке интеграции.

Интеграцию на сервисе больше не выбирают из готового списка, а заводят по
шаблону платформы: Affise, Alanbase или AffTech. Отсюда новая колонка.

Значения `base_url` и `api_key_encrypted` у уже заведённых интеграций после
этой смены означают другое: раньше там был адрес и ключ самого сервиса
партнёрок, теперь — доступы к конкретной ПП. Данные не трогаем: угадать за
человека, какая ПП стоит за строкой, нельзя, а перезаписать чужой ключ пустым
значило бы молча сломать синк. Существующие карточки нужно открыть и сохранить
заново, указав платформу и доступы к самой партнёрке.
"""

import sqlalchemy as sa

from alembic import op

revision = "0060_partner_platform"
down_revision = "0059_moscow_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "partner_integrations",
        sa.Column(
            "platform",
            sa.String(length=40),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("partner_integrations", "platform")
