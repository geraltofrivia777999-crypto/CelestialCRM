"""Объявления залива, копии адсета и параметры ссылки.

У залива появился собственный список объявлений: у каждого свои тексты, свои
языки и свои креативы. Список, а не таблица, потому что живёт он ровно один
залив и наружу ничем, кроме публикации, не используется.

Пустой список означает старое поведение — по объявлению на креатив.
"""

import sqlalchemy as sa

from alembic import op

revision = "0038_launch_ads"
down_revision = "0037_meta_bundles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meta_launches",
        sa.Column("ads", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "meta_launches",
        sa.Column("adset_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("meta_launches", sa.Column("url_tags", sa.Text()))
    op.add_column("meta_launches", sa.Column("display_link", sa.String(length=240)))


def downgrade() -> None:
    op.drop_column("meta_launches", "display_link")
    op.drop_column("meta_launches", "url_tags")
    op.drop_column("meta_launches", "adset_count")
    op.drop_column("meta_launches", "ads")
