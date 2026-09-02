"""Модерация комментариев: кеш комментариев и задания на чистку.

Комментарии кешируются, потому что удалённый комментарий Meta не отдаёт больше
никогда: текст нужно сохранить до удаления, иначе в CRM не останется следа, за
что человека вычистили. Задание — потому что и чтение, и удаление идут с паузой
между вызовами, и такой процесс обязан иметь прогресс и отмену.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0047_meta_comments"
down_revision = "0046_meta_rules_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meta_entities", sa.Column("post_external_id", sa.String(length=120), nullable=True)
    )
    op.create_index(
        "ix_meta_entities_post_external_id", "meta_entities", ["post_external_id"]
    )
    _backfill_post_ids()

    op.create_table(
        "meta_comment_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column(
            "account_id", sa.Uuid(),
            sa.ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column(
            "connection_id", sa.Uuid(),
            sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
        ),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column(
            "status", sa.String(length=10), nullable=False,
            server_default="queued", index=True,
        ),
        sa.Column("scope", sa.JSON(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), index=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_meta_comment_jobs_account", "meta_comment_jobs", ["account_id", "created_at"]
    )

    op.create_table(
        "meta_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column(
            "connection_id", sa.Uuid(),
            sa.ForeignKey("integration_connections.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column(
            "account_id", sa.Uuid(),
            sa.ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("post_external_id", sa.String(length=120), nullable=False),
        sa.Column("page_external_id", sa.String(length=100), nullable=True),
        sa.Column("parent_external_id", sa.String(length=120), nullable=True, index=True),
        sa.Column("author_external_id", sa.String(length=120), nullable=True, index=True),
        sa.Column("author_name", sa.String(length=300), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_link", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_phone", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "status", sa.String(length=10), nullable=False, server_default="visible"
        ),
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acted_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.UniqueConstraint("workspace_id", "external_id", name="uq_meta_comment_external"),
    )
    op.create_index(
        "ix_meta_comments_post", "meta_comments", ["account_id", "post_external_id"]
    )
    op.create_index(
        "ix_meta_comments_status", "meta_comments", ["workspace_id", "status"]
    )

    op.add_column("meta_operations", sa.Column("comment_job_id", sa.Uuid(), nullable=True))
    with op.batch_alter_table("meta_operations") as batch:
        batch.create_foreign_key(
            "fk_meta_operations_comment_job",
            "meta_comment_jobs",
            ["comment_job_id"],
            ["id"],
            ondelete="SET NULL",
        )


def _backfill_post_ids() -> None:
    """Достать id поста из уже загруженного `external_payload` объявлений.

    Без этого чистка увидела бы кабинет пустым до следующей синхронизации, а она
    может быть и через полчаса, и через сутки.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, external_payload FROM meta_entities WHERE level = 'ad'")
    ).fetchall()
    for row in rows:
        payload = row[1]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if not isinstance(payload, dict):
            continue
        creative = payload.get("creative")
        story = (
            str(creative.get("effective_object_story_id") or "")
            if isinstance(creative, dict)
            else ""
        )
        if "_" not in story:
            continue
        bind.execute(
            sa.text("UPDATE meta_entities SET post_external_id = :post WHERE id = :id"),
            {"post": story[:120], "id": row[0]},
        )


def downgrade() -> None:
    with op.batch_alter_table("meta_operations") as batch:
        batch.drop_constraint("fk_meta_operations_comment_job", type_="foreignkey")
    op.drop_column("meta_operations", "comment_job_id")
    op.drop_table("meta_comments")
    op.drop_table("meta_comment_jobs")
    op.drop_index("ix_meta_entities_post_external_id", table_name="meta_entities")
    op.drop_column("meta_entities", "post_external_id")
