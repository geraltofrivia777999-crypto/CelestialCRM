"""Этап «Тех. интервью» в воронке найма.

Ключ этапа `tech_interview` длиннее прежних двенадцати символов, отведённых под
колонку, — поэтому вместе с этапом расширяется и она. Обратная миграция
возвращает таких кандидатов на «Интервью»: в узкую колонку ключ не помещается.
"""

import sqlalchemy as sa

from alembic import op

revision = "0064_pipeline_tech_stage"
down_revision = "0063_recruitment_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "recruitment_candidates",
        "stage",
        existing_type=sa.String(length=12),
        type_=sa.String(length=24),
        existing_nullable=False,
        existing_server_default="screening",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE recruitment_candidates SET stage = 'interview' "
        "WHERE stage = 'tech_interview'"
    )
    op.alter_column(
        "recruitment_candidates",
        "stage",
        existing_type=sa.String(length=24),
        type_=sa.String(length=12),
        existing_nullable=False,
        existing_server_default="screening",
    )
