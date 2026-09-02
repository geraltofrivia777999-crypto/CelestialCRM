"""Капа тимлида по офферу.

Партнёрка выдаёт общий лимит на оффер, а тимлиды делят его между собой: у
каждого своя цифра. Держать её на самом оффере негде — она принадлежит паре
«оффер + тимлид», поэтому колонка встаёт на связку.
"""

import sqlalchemy as sa

from alembic import op

revision = "0055_offer_lead_cap"
down_revision = "0054_offer_partner_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offer_leads", sa.Column("cap", sa.String(length=160), nullable=True))


def downgrade() -> None:
    op.drop_column("offer_leads", "cap")
