"""Свести две ветки миграций в одну голову.

`0052_drop_buyer_tag_registry` (реестр тегов больше не нужен) и
`0052_profile_posts` (комментарии под постами профилей) ответвились от одной
точки независимо друг от друга. Ничего своего эта ревизия не делает — она
только возвращает линейность, чтобы `alembic upgrade head` снова знал, куда
идти: с двумя головами он отказывается стартовать, и контейнер api не
поднимается.
"""

revision = "0053_merge_heads"
down_revision = ("0052_drop_buyer_tag_registry", "0052_profile_posts")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
