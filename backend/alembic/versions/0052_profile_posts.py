"""Комментарии под постами профилей (залив через Dolphin Cloud).

Часть залива живёт не в рекламных кабинетах, а постами на личных профилях:
Dolphin Cloud публикует «органику» от лица персоны, и спам под такими постами
чистят так же, как под тёмными постами страниц. Пост профиля не принадлежит
никакому кабинету, поэтому у комментариев и заданий `account_id` становится
необязательным, а сами посты хранятся отдельно — в рамках подключения.

Существующие строки не трогаем: у всех комментариев `account_id` уже заполнен.
"""

import sqlalchemy as sa
from alembic import op

revision = "0052_profile_posts"
down_revision = "0051_offer_partner_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("meta_comments", "account_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("meta_comment_jobs", "account_id", existing_type=sa.Uuid(), nullable=True)
    op.create_table(
        "meta_profile_posts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "connection_id", sa.Uuid(),
            sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("post_external_id", sa.String(length=200), nullable=False),
        sa.Column("permalink", sa.Text()),
        sa.Column("author_name", sa.String(length=300)),
        sa.Column("message", sa.Text()),
        sa.Column("created_time", sa.DateTime(timezone=True)),
        sa.Column(
            "added_by_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "connection_id", "post_external_id", name="uq_meta_profile_post_external"
        ),
    )


def downgrade() -> None:
    op.drop_table("meta_profile_posts")
    op.alter_column("meta_comment_jobs", "account_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("meta_comments", "account_id", existing_type=sa.Uuid(), nullable=False)
