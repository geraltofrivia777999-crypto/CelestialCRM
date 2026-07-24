import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    Permission,
    ProviderType,
    Role,
    Service,
    SpendProvider,
    User,
    Workspace,
)

PERMISSIONS = {
    "dashboard.view",
    "media.view",
    "media.manage",
    "finance.view",
    "finance.manage",
    "finance.export",
    "partners.view",
    "offers.view",
    "offers.manage",
    "team.view",
    "team.manage",
    "settings.view",
    "settings.manage",
}


async def seed() -> None:
    async with SessionLocal() as db:
        workspace = await db.scalar(select(Workspace).limit(1))
        if not workspace:
            workspace = Workspace(
                name="Celestial",
                timezone=settings.default_timezone,
                currency=settings.default_currency,
            )
            db.add(workspace)
            await db.flush()

        permission_rows = {}
        for code in sorted(PERMISSIONS):
            permission = await db.scalar(select(Permission).where(Permission.code == code))
            if not permission:
                permission = Permission(code=code, description=code.replace(".", " "))
                db.add(permission)
                await db.flush()
            permission_rows[code] = permission

        admin_role = await db.scalar(
            select(Role).where(Role.workspace_id == workspace.id, Role.name == "Administrator")
        )
        if not admin_role:
            admin_role = Role(
                workspace_id=workspace.id,
                name="Administrator",
                description="Full access",
                is_system=True,
                permissions=list(permission_rows.values()),
            )
            db.add(admin_role)
            await db.flush()

        role_defaults = {
            "Team Lead": {
                "dashboard.view",
                "media.view",
                "media.manage",
                "finance.view",
                "partners.view",
                "offers.view",
                "offers.manage",
                "team.view",
                "settings.view",
            },
            "Buyer": {
                "dashboard.view",
                "media.view",
                "media.manage",
                "partners.view",
                "offers.view",
            },
            "Finance": {
                "dashboard.view",
                "finance.view",
                "finance.manage",
                "finance.export",
                "offers.view",
            },
        }
        for name, codes in role_defaults.items():
            role = await db.scalar(
                select(Role).where(Role.workspace_id == workspace.id, Role.name == name)
            )
            if not role:
                db.add(
                    Role(
                        workspace_id=workspace.id,
                        name=name,
                        is_system=True,
                        permissions=[permission_rows[code] for code in codes],
                    )
                )

        admin = await db.scalar(
            select(User).where(
                User.workspace_id == workspace.id,
                User.login == settings.admin_login.lower(),
            )
        )
        if not admin:
            db.add(
                User(
                    workspace_id=workspace.id,
                    role_id=admin_role.id,
                    name="Administrator",
                    login=settings.admin_login.lower(),
                    password_hash=hash_password(settings.admin_password),
                )
            )

        for name, cost, commission in [
            ("PWA", "0.0300", "0"),
            ("SKAK", "0.0250", "2"),
            ("ZM", "0.0180", "1.5"),
            ("HEROES", "0.0400", "3"),
        ]:
            existing = await db.scalar(
                select(Service).where(Service.workspace_id == workspace.id, Service.name == name)
            )
            if not existing:
                db.add(
                    Service(
                        workspace_id=workspace.id,
                        name=name,
                        install_cost=Decimal(cost),
                        commission_pct=Decimal(commission),
                    )
                )
        for name, provider_type, commission in [
            ("BRO", ProviderType.agent, "8.5"),
            ("MT", ProviderType.agent, "7"),
            ("SPX", ProviderType.payment, "4.5"),
        ]:
            existing = await db.scalar(
                select(SpendProvider).where(
                    SpendProvider.workspace_id == workspace.id,
                    SpendProvider.name == name,
                )
            )
            if not existing:
                db.add(
                    SpendProvider(
                        workspace_id=workspace.id,
                        name=name,
                        provider_type=provider_type,
                        commission_pct=Decimal(commission),
                    )
                )
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
