import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import Permission, Role, RolePermission, Session, User


@pytest.fixture
async def partner_permission_users(database):
    suffix = uuid.uuid4().hex[:8]
    password = "test-password"
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        media_permission = await db.scalar(
            select(Permission).where(Permission.code == "media.view")
        )
        dashboard_permission = await db.scalar(
            select(Permission).where(Permission.code == "dashboard.view")
        )
        assert media_permission is not None and dashboard_permission is not None

        media_role = Role(
            workspace_id=admin.workspace_id,
            name=f"Media reference {suffix}",
            permissions=[media_permission],
        )
        blocked_role = Role(
            workspace_id=admin.workspace_id,
            name=f"No media reference {suffix}",
            permissions=[dashboard_permission],
        )
        db.add_all([media_role, blocked_role])
        await db.flush()

        media_user = User(
            workspace_id=admin.workspace_id,
            role_id=media_role.id,
            name="Media reference user",
            login=f"media-reference-{suffix}",
            password_hash=hash_password(password),
        )
        blocked_user = User(
            workspace_id=admin.workspace_id,
            role_id=blocked_role.id,
            name="No media reference user",
            login=f"no-media-reference-{suffix}",
            password_hash=hash_password(password),
        )
        db.add_all([media_user, blocked_user])
        await db.commit()
        result = {
            "password": password,
            "media_login": media_user.login,
            "blocked_login": blocked_user.login,
            "user_ids": [media_user.id, blocked_user.id],
            "role_ids": [media_role.id, blocked_role.id],
        }

    yield result

    async with SessionLocal() as db:
        await db.execute(delete(Session).where(Session.user_id.in_(result["user_ids"])))
        await db.execute(delete(User).where(User.id.in_(result["user_ids"])))
        await db.execute(
            delete(RolePermission).where(RolePermission.role_id.in_(result["role_ids"]))
        )
        await db.execute(delete(Role).where(Role.id.in_(result["role_ids"])))
        await db.commit()


async def test_media_view_can_read_partner_filter_without_partners_permission(
    partner_permission_users,
) -> None:
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "login": partner_permission_users["media_login"],
                "password": partner_permission_users["password"],
            },
        )
        assert login.status_code == 200
        codes = {item["code"] for item in login.json()["role"]["permissions"]}
        assert "media.view" in codes
        assert "partners.view" not in codes

        response = client.get("/api/v1/partners", params={"limit": 1})
        assert response.status_code == 200
        assert set(response.json()) == {"items", "total", "limit", "offset"}


async def test_partner_filter_still_requires_media_view(partner_permission_users) -> None:
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "login": partner_permission_users["blocked_login"],
                "password": partner_permission_users["password"],
            },
        )
        assert login.status_code == 200
        response = client.get("/api/v1/partners")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "http_403"


async def test_obsolete_partners_permission_is_not_listed(database) -> None:
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        )
        assert login.status_code == 200
        response = client.get("/api/v1/permissions")
        assert response.status_code == 200
        assert "partners.view" not in response.json()
