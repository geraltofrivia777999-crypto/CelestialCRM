"""Remove the obsolete standalone partners.view permission.

Partner names are only a Media Board dimension/filter now. Reading the partner
catalog follows media.view; changing a partner status remains protected by
offers.manage.
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0012_remove_partners_view"
down_revision = "0011_finance_rate_currency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Delete links explicitly as well: production PostgreSQL has ON DELETE
    # CASCADE, while SQLite environments may run with foreign keys disabled.
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE code = 'partners.view')"
    )
    op.execute("DELETE FROM permissions WHERE code = 'partners.view'")


def downgrade() -> None:
    bind = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("description", sa.String()),
    )
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )

    permission_id = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == "partners.view")
    ).scalar_one_or_none()
    if permission_id is None:
        permission_id = uuid.uuid4()
        bind.execute(
            sa.insert(permissions).values(
                id=permission_id,
                code="partners.view",
                description="partners view",
            )
        )

    role_ids = list(
        bind.execute(
            sa.select(roles.c.id).where(
                roles.c.name.in_(["Administrator", "Team Lead", "Buyer"])
            )
        ).scalars()
    )
    links = [
        {"role_id": role_id, "permission_id": permission_id}
        for role_id in role_ids
    ]
    if links:
        bind.execute(sa.insert(role_permissions), links)
