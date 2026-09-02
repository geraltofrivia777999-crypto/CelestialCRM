"""Дата начала задачи.

Срок выполнения отвечает на вопрос «когда ждут результат», а планировать работу
по нему нельзя: непонятно, когда задачу вообще берут. Поэтому у карточки
появляется отдельная дата начала.
"""

import sqlalchemy as sa

from alembic import op

revision = "0056_task_start_date"
down_revision = "0055_offer_lead_cap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("start_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "start_date")
