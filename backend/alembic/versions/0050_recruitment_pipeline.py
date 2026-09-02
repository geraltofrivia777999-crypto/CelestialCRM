"""Воронка найма на стороне CRM.

Recruitment Service намеренно не хранит этапы найма — он владеет находками и их
триажем. Скрининг, интервью, оффер и отказ живут здесь, вместе со снимком
профиля: доска найма должна открываться, даже когда сервис недоступен.
"""

import sqlalchemy as sa

from alembic import op

revision = "0050_recruitment_pipeline"
down_revision = "0049_partner_buyer_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recruitment_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), index=True,
        ),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column(
            "stage", sa.String(length=12), nullable=False, server_default="screening"
        ),
        sa.Column("position_title", sa.String(length=300), nullable=True),
        sa.Column("geo", sa.String(length=160), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=True),
        sa.Column("tier", sa.String(length=10), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("salary_expectation", sa.Numeric(18, 2), nullable=True),
        sa.Column("experience_months", sa.Integer(), nullable=True),
        sa.Column("external_url", sa.String(length=500), nullable=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "stage_changed_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("added_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "workspace_id", "external_id", name="uq_recruitment_candidate_external"
        ),
    )
    op.create_index(
        "ix_recruitment_candidates_stage", "recruitment_candidates", ["workspace_id", "stage"]
    )


def downgrade() -> None:
    op.drop_table("recruitment_candidates")
