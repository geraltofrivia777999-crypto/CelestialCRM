"""Снимок профиля кандидата одним полем.

Имя, дата рождения, город, ник в телеграме, текст отклика и файл приходят из
Recruitment Service и только показываются в карточке. Колонка на каждое поле
означала бы миграцию под каждую правку сервиса, а состав ответа ещё меняется.
"""

import sqlalchemy as sa

from alembic import op

# Идентификатор короче 32 символов: столько отведено под него в alembic_version.
revision = "0063_recruitment_profile"
down_revision = "0062_finance_spend_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recruitment_candidates",
        sa.Column("profile", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("recruitment_candidates", "profile")
