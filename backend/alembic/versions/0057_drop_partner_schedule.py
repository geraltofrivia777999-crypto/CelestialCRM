"""Расписание синков с ПП убрано.

Планировщик по cron снят: синк запускают руками — с карточки интеграции или
кнопкой на странице «Финансы». Колонка вместе с ним теряет смысл: пустая она
ничего не значит, а заполненная обещала бы автозапуск, которого больше нет.
"""

import sqlalchemy as sa

from alembic import op

revision = "0057_drop_partner_schedule"
down_revision = "0056_task_start_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("partner_integrations", "schedule_cron")


def downgrade() -> None:
    op.add_column(
        "partner_integrations",
        sa.Column("schedule_cron", sa.String(length=60), nullable=True),
    )
