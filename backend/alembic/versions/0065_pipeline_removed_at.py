"""Убранные с доски найма помнятся, а не удаляются.

Строку кандидата удаляли целиком, а сервис рекрутинга по-прежнему считал
находку разобранной — и при следующем открытии доски она возвращалась. Отметка
`removed_at` помнит решение CRM: на доске такого кандидата нет, синхронизация
его не подтягивает.
"""

import sqlalchemy as sa

from alembic import op

revision = "0065_pipeline_removed_at"
down_revision = "0064_pipeline_tech_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recruitment_candidates",
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Убранные карточки при откате исчезают совсем: колонки для отметки больше
    # нет, а вернуть их на доску значило бы показать то, что уже убрали.
    op.execute("DELETE FROM recruitment_candidates WHERE removed_at IS NOT NULL")
    op.drop_column("recruitment_candidates", "removed_at")
