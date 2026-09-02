"""Кастомный нейминг и DSA-бенефициар залива.

Мастер залива раздаёт кабинетам индивидуальные значения: шаблон имени
кампании и бенефициара/платильщика DSA-прозрачности (Meta требует его для
рекламы на ЕС). `url_tags` и `display_link` уже существуют — их пер-кабинетные
значения пишутся в те же колонки.
"""

import sqlalchemy as sa

from alembic import op

revision = "0042_launch_dsa"
down_revision = "0041_connection_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meta_launches",
        sa.Column("campaign_name", sa.String(length=240), nullable=True),
    )
    op.add_column(
        "meta_launches",
        sa.Column("beneficiary", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meta_launches", "beneficiary")
    op.drop_column("meta_launches", "campaign_name")
