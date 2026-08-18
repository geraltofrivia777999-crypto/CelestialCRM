"""Meta Ads: заливы, шаблоны, креативы и автоправила.

ТЗ 3.3–3.6 и 3.8. Всё, что здесь появляется, — записывающая часть модуля: у неё
своё право `meta.launch`, отдельное от `meta.manage`. Разделение не формальное:
`meta.manage` открывает доступ к токену кабинета, а `meta.launch` — к тратам
бюджета, и это разные люди в команде.
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0014_meta_launch"
down_revision = "0013_meta_ads"
branch_labels = None
depends_on = None

PERMISSIONS = [("meta.launch", "meta launch")]
GRANTS = {"Administrator": ["meta.launch"], "Team Lead": ["meta.launch"]}
TABLES = (
    "meta_rule_events",
    "meta_rules",
    "meta_operations",
    "meta_launch_creatives",
    "meta_launches",
    "meta_creatives",
    "meta_templates",
)


def upgrade() -> None:
    # Та же оговорка, что и в 0013: на чистой базе схему поднимает
    # metadata.create_all в 0001, поэтому таблицы к этому моменту уже есть.
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "meta_templates" in tables:
        _grant_permissions()
        return

    op.create_table(
        "meta_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("objective", sa.String(length=60), nullable=False,
                  server_default="OUTCOME_SALES"),
        sa.Column("optimization_goal", sa.String(length=60), nullable=False,
                  server_default="OFFSITE_CONVERSIONS"),
        sa.Column("billing_event", sa.String(length=40), nullable=False,
                  server_default="IMPRESSIONS"),
        sa.Column("bid_strategy", sa.String(length=60), nullable=False,
                  server_default="LOWEST_COST_WITHOUT_CAP"),
        sa.Column("geo", sa.JSON(), nullable=True),
        sa.Column("age_min", sa.Integer(), nullable=False, server_default="18"),
        sa.Column("age_max", sa.Integer(), nullable=False, server_default="65"),
        sa.Column("genders", sa.JSON(), nullable=True),
        sa.Column("languages", sa.JSON(), nullable=True),
        sa.Column("placements", sa.JSON(), nullable=True),
        sa.Column("interests", sa.JSON(), nullable=True),
        sa.Column("daily_budget", sa.Numeric(18, 2), nullable=True),
        sa.Column("lifetime_budget", sa.Numeric(18, 2), nullable=True),
        sa.Column("page_id", sa.String(length=60), nullable=True),
        sa.Column("pixel_id", sa.String(length=60), nullable=True),
        sa.Column("custom_event_type", sa.String(length=60), nullable=True),
        sa.Column("call_to_action", sa.String(length=40), nullable=False,
                  server_default="LEARN_MORE"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column(
            "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "name", name="uq_meta_template_name"),
    )
    op.create_index("ix_meta_templates_workspace_id", "meta_templates", ["workspace_id"])
    op.create_index("ix_meta_templates_status", "meta_templates", ["status"])

    op.create_table(
        "meta_creatives",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=10), nullable=False, server_default="image"),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("file_name", sa.String(length=240), nullable=True),
        sa.Column("mime_type", sa.String(length=80), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("external_hash", sa.String(length=120), nullable=True),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("permalink_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column(
            "uploaded_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("external_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_meta_creatives_workspace_id", "meta_creatives", ["workspace_id"])
    op.create_index("ix_meta_creatives_account_id", "meta_creatives", ["account_id"])
    op.create_index("ix_meta_creatives_account", "meta_creatives", ["account_id", "kind"])
    op.create_index("ix_meta_creatives_hash", "meta_creatives", ["external_hash"])
    op.create_index("ix_meta_creatives_external", "meta_creatives", ["external_id"])
    op.create_index("ix_meta_creatives_status", "meta_creatives", ["status"])

    op.create_table(
        "meta_launches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "template_id", sa.Uuid(), sa.ForeignKey("meta_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "offer_id", sa.Uuid(), sa.ForeignKey("offers.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "partner_id", sa.Uuid(), sa.ForeignKey("partners.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("geo", sa.String(length=12), nullable=True),
        sa.Column("daily_budget", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("spend_limit", sa.Numeric(18, 2), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("link_url", sa.Text(), nullable=True),
        sa.Column("primary_text", sa.Text(), nullable=True),
        sa.Column("headline", sa.String(length=240), nullable=True),
        sa.Column("description", sa.String(length=240), nullable=True),
        sa.Column("call_to_action", sa.String(length=40), nullable=False,
                  server_default="LEARN_MORE"),
        sa.Column("page_id", sa.String(length=60), nullable=True),
        sa.Column("pixel_id", sa.String(length=60), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("activate_on_publish", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("campaign_external_id", sa.String(length=100), nullable=True),
        sa.Column("adset_external_id", sa.String(length=100), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("external_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_meta_launches_workspace_id", "meta_launches", ["workspace_id"])
    op.create_index("ix_meta_launches_account_id", "meta_launches", ["account_id"])
    op.create_index("ix_meta_launches_owner_id", "meta_launches", ["owner_id"])
    op.create_index("ix_meta_launches_geo", "meta_launches", ["geo"])
    op.create_index("ix_meta_launches_campaign", "meta_launches", ["campaign_external_id"])
    op.create_index("ix_meta_launches_status", "meta_launches", ["status"])
    op.create_index(
        "ix_meta_launches_workspace_status", "meta_launches", ["workspace_id", "status"]
    )

    op.create_table(
        "meta_launch_creatives",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "launch_id",
            sa.Uuid(),
            sa.ForeignKey("meta_launches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "creative_id",
            sa.Uuid(),
            sa.ForeignKey("meta_creatives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ad_external_id", sa.String(length=100), nullable=True),
        sa.Column("creative_external_id", sa.String(length=100), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("launch_id", "creative_id", name="uq_meta_launch_creative"),
    )
    op.create_index("ix_meta_launch_creatives_launch", "meta_launch_creatives", ["launch_id"])
    op.create_index("ix_meta_launch_creatives_creative", "meta_launch_creatives", ["creative_id"])

    op.create_table(
        "meta_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "account_id", sa.Uuid(), sa.ForeignKey("meta_ad_accounts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "launch_id", sa.Uuid(), sa.ForeignKey("meta_launches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("metric", sa.String(length=20), nullable=False, server_default="roi"),
        sa.Column("operator", sa.String(length=4), nullable=False, server_default="lt"),
        sa.Column("threshold", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("min_spend", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("action", sa.String(length=30), nullable=False, server_default="notify"),
        sa.Column("action_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "name", name="uq_meta_rule_name"),
    )
    op.create_index("ix_meta_rules_workspace_id", "meta_rules", ["workspace_id"])
    op.create_index("ix_meta_rules_account_id", "meta_rules", ["account_id"])

    op.create_table(
        "meta_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "launch_id", sa.Uuid(), sa.ForeignKey("meta_launches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "rule_id", sa.Uuid(), sa.ForeignKey("meta_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("target_external_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("request", sa.JSON(), nullable=True),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_meta_operations_workspace_id", "meta_operations", ["workspace_id"])
    op.create_index("ix_meta_operations_status", "meta_operations", ["status"])
    op.create_index("ix_meta_operations_created_at", "meta_operations", ["created_at"])
    op.create_index("ix_meta_operations_launch", "meta_operations", ["launch_id", "kind"])

    op.create_table(
        "meta_rule_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rule_id", sa.Uuid(), sa.ForeignKey("meta_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id", sa.Uuid(), sa.ForeignKey("meta_ad_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("campaign_external_id", sa.String(length=100), nullable=True),
        sa.Column("campaign_name", sa.String(length=300), nullable=True),
        sa.Column("metric", sa.String(length=20), nullable=False),
        sa.Column("metric_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_meta_rule_events_workspace_id", "meta_rule_events", ["workspace_id"])
    op.create_index("ix_meta_rule_events_rule_id", "meta_rule_events", ["rule_id"])
    op.create_index("ix_meta_rule_events_created_at", "meta_rule_events", ["created_at"])
    op.create_index(
        "ix_meta_rule_events_created", "meta_rule_events", ["workspace_id", "created_at"]
    )

    _grant_permissions()


def _grant_permissions() -> None:
    bind = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("description", sa.String()),
    )
    roles = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("name", sa.String()))
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )

    permission_ids: dict[str, uuid.UUID] = {}
    for code, description in PERMISSIONS:
        existing = bind.execute(
            sa.select(permissions.c.id).where(permissions.c.code == code)
        ).scalar_one_or_none()
        if existing is None:
            existing = uuid.uuid4()
            bind.execute(
                sa.insert(permissions).values(id=existing, code=code, description=description)
            )
        permission_ids[code] = existing

    links = []
    for role_name, codes in GRANTS.items():
        role_id = bind.execute(
            sa.select(roles.c.id).where(roles.c.name == role_name)
        ).scalar_one_or_none()
        if role_id is None:
            continue
        for code in codes:
            already = bind.execute(
                sa.select(role_permissions.c.role_id).where(
                    role_permissions.c.role_id == role_id,
                    role_permissions.c.permission_id == permission_ids[code],
                )
            ).first()
            if already is None:
                links.append({"role_id": role_id, "permission_id": permission_ids[code]})
    if links:
        bind.execute(sa.insert(role_permissions), links)


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE code = 'meta.launch')"
    )
    op.execute("DELETE FROM permissions WHERE code = 'meta.launch'")
    for table in TABLES:
        op.drop_table(table)
