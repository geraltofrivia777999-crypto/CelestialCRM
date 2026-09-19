"""Поля карточки кандидата: позиция, телеграм, запись интервью.

`position_title` остаётся снимком из резюме, а решение команды «на какую
позицию рассматриваем» живёт отдельным полем: они расходятся почти всегда.
"""

import sqlalchemy as sa

from alembic import op

revision = "0061_recruitment_card_fields"
down_revision = "0060_partner_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recruitment_candidates",
        sa.Column("target_position", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "recruitment_candidates",
        sa.Column("telegram_contact", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "recruitment_candidates",
        sa.Column("interview_record", sa.String(length=500), nullable=True),
    )


    # Запись интервью может быть загруженным файлом: привязка не даёт уборщику
    # незакреплённых вложений удалить его через сутки.
    op.add_column(
        "knowledge_attachments",
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_knowledge_attachments_candidate_id", "knowledge_attachments", ["candidate_id"]
    )
    op.create_foreign_key(
        "fk_knowledge_attachments_candidate",
        "knowledge_attachments",
        "recruitment_candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_knowledge_attachments_candidate", "knowledge_attachments", type_="foreignkey"
    )
    op.drop_index("ix_knowledge_attachments_candidate_id", "knowledge_attachments")
    op.drop_column("knowledge_attachments", "candidate_id")
    op.drop_column("recruitment_candidates", "interview_record")
    op.drop_column("recruitment_candidates", "telegram_contact")
    op.drop_column("recruitment_candidates", "target_position")
