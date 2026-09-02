"""Расширенный режим v2: цель (4 селекта), лимиты адсета, периоды бюджета.

По обратной связи на «Расширенный режим»: цель кампании получает событие
пикселя, окно конверсии и вовлечённые просмотры; лимит адсета становится
парой минимум/максимум; «Запланировать увеличение бюджета» хранит периоды
(начало, завершение, тип и сумма) и применяется планировщиком; имя группы
автоправил сохраняется строкой (логика групп — позже).
"""

import sqlalchemy as sa

from alembic import op

revision = "0044_launch_advanced_v2"
down_revision = "0043_launch_advanced"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meta_launches",
        sa.Column("custom_event_type", sa.String(length=60), nullable=True),
    )
    op.add_column(
        "meta_launches", sa.Column("attribution", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "meta_launches", sa.Column("engaged_view", sa.String(length=5), nullable=True)
    )
    op.add_column(
        "meta_launches", sa.Column("budget_limit_min", sa.Numeric(18, 2), nullable=True)
    )
    op.add_column(
        "meta_launches", sa.Column("budget_limit_max", sa.Numeric(18, 2), nullable=True)
    )
    op.add_column("meta_launches", sa.Column("budget_increases", sa.JSON(), nullable=True))
    op.add_column(
        "meta_launches", sa.Column("rule_group", sa.String(length=160), nullable=True)
    )


def downgrade() -> None:
    for column in ("rule_group", "budget_increases", "budget_limit_max", "budget_limit_min",
                   "engaged_view", "attribution", "custom_event_type"):
        op.drop_column("meta_launches", column)
