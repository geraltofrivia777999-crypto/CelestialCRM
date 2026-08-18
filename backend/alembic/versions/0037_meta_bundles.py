"""Связка целиком и время залива.

Связка перестала быть набором полей адсета: теперь она описывает кампанию,
адсет и объявление разом — как их собирают в мастере. Новые параметры лежат
одним JSON тремя блоками: это сквозные поля Graph API, по которым мы не ищем и
не считаем, и раскладывать их по колонкам смысла нет.

У залива появилось время: когда создавать объекты в кабинете (`publish_at`),
когда им начать крутиться (`start_at`) и что оставить на паузе.
"""

import sqlalchemy as sa

from alembic import op

revision = "0037_meta_bundles"
down_revision = "0036_task_sections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meta_templates",
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column("meta_launches", sa.Column("publish_at", sa.DateTime(timezone=True)))
    op.add_column("meta_launches", sa.Column("start_at", sa.DateTime(timezone=True)))
    op.create_index("ix_meta_launches_publish_at", "meta_launches", ["publish_at"])
    for column in ("pause_campaigns", "pause_adsets", "pause_ads"):
        op.add_column(
            "meta_launches",
            sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    for column in ("pause_ads", "pause_adsets", "pause_campaigns"):
        op.drop_column("meta_launches", column)
    op.drop_index("ix_meta_launches_publish_at", table_name="meta_launches")
    op.drop_column("meta_launches", "start_at")
    op.drop_column("meta_launches", "publish_at")
    op.drop_column("meta_templates", "settings")
