"""Regression tests for CRM role delegation, session revocation and uploads."""

import uuid
from importlib.metadata import version

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import (
    AuditEvent,
    Permission,
    Role,
    RolePermission,
    Session,
    User,
    UserParent,
    Workspace,
)


@pytest.fixture
async def security_team(database):
    # Dedicated workspace; never change the seeded administrator or live data.
    async with SessionLocal() as db:
        permissions = list((await db.scalars(select(Permission))).all())
        by_code = {p.code: p for p in permissions}
        ws = Workspace(name="Security regression " + uuid.uuid4().hex)
        db.add(ws)
        await db.flush()
        roles = {
            "admin": Role(workspace_id=ws.id, name="Custom full-access role", permissions=permissions),
            "manager": Role(workspace_id=ws.id, name="Manager", permissions=[
                by_code[c] for c in ("team.view", "team.manage", "offers.view")
            ]),
            "buyer": Role(workspace_id=ws.id, name="Buyer", permissions=[by_code["offers.view"]]),
        }
        db.add_all(roles.values())
        await db.flush()
        people = {}
        for kind, role in roles.items():
            user = User(
                workspace_id=ws.id, role_id=role.id, name="Security " + kind,
                login="security-" + uuid.uuid4().hex,
                password_hash=hash_password("security-test-password"),
            )
            db.add(user)
            people[kind] = user
        await db.flush()
        # Even an accidentally subordinate admin must not be reset/demoted by
        # a less privileged manager merely because the hierarchy exposes them.
        db.add_all([
            UserParent(user_id=people[k].id, parent_id=people["manager"].id)
            for k in ("admin", "buyer")
        ])
        await db.commit()
        workspace_id = ws.id
        result = {
            k: {"id": str(u.id), "login": u.login, "role_id": str(roles[k].id)}
            for k, u in people.items()
        }
    yield result
    # SQLite test fixtures do not enable FK cascades. Clean up explicitly so
    # unrelated tests still see their own roles and workspaces only.
    async with SessionLocal() as db:
        user_ids = select(User.id).where(User.workspace_id == workspace_id)
        role_ids = select(Role.id).where(Role.workspace_id == workspace_id)
        await db.execute(delete(Session).where(Session.user_id.in_(user_ids)))
        await db.execute(delete(AuditEvent).where(AuditEvent.workspace_id == workspace_id))
        await db.execute(delete(UserParent).where(
            UserParent.user_id.in_(user_ids) | UserParent.parent_id.in_(user_ids),
        ))
        await db.execute(delete(User).where(User.workspace_id == workspace_id))
        await db.execute(delete(RolePermission).where(RolePermission.role_id.in_(role_ids)))
        await db.execute(delete(Role).where(Role.workspace_id == workspace_id))
        await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
        await db.commit()


async def login(client, person, password="security-test-password"):
    response = await client.post(
        "/api/v1/auth/login", json={"login": person["login"], "password": password},
    )
    assert response.status_code == 200, response.text
    return response


async def test_manager_cannot_rewrite_create_copy_or_delete_roles(security_team):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, security_team["manager"])
        own_role = security_team["manager"]["role_id"]
        admin_role = security_team["admin"]["role_id"]
        for role_id in (own_role, admin_role):
            response = await client.patch(f"/api/v1/roles/{role_id}", json={
                "permission_codes": ["team.manage", "team.view", "settings.manage"],
            })
            assert response.status_code == 403
        assert (await client.post("/api/v1/roles", json={
            "name": "Escalation", "permission_codes": ["settings.manage"],
        })).status_code == 403
        assert (await client.post(f"/api/v1/roles/{admin_role}/copy")).status_code == 403
        assert (await client.delete(f"/api/v1/roles/{admin_role}")).status_code == 403
        me = (await client.get("/api/v1/auth/me")).json()
        assert "settings.manage" not in {p["code"] for p in me["role"]["permissions"]}


async def test_manager_cannot_assign_admin_or_take_over_privileged_user(security_team):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, security_team["manager"])
        role_id = security_team["admin"]["role_id"]
        buyer_id = security_team["buyer"]["id"]
        admin_id = security_team["admin"]["id"]
        assert (await client.post("/api/v1/users", json={
            "name": "Escalation", "login": "escalation-" + uuid.uuid4().hex,
            "password": "security-test-password", "role_id": role_id,
        })).status_code == 403
        assert (await client.patch(f"/api/v1/users/{buyer_id}", json={
            "role_id": role_id,
        })).status_code == 403
        assert (await client.patch(f"/api/v1/users/{admin_id}", json={
            "role_id": security_team["buyer"]["role_id"],
        })).status_code == 403
        assert (await client.post(f"/api/v1/users/{admin_id}/reset-password")).status_code == 403
        assert (await client.patch(f"/api/v1/users/{admin_id}/status", params={
            "new_status": "blocked",
        })).status_code == 403
        assert (await client.delete(f"/api/v1/users/{admin_id}")).status_code == 403
        # Ordinary delegated work is still allowed.
        assert (await client.patch(f"/api/v1/users/{buyer_id}", json={
            "name": "Renamed buyer",
        })).status_code == 200


async def test_full_access_role_can_manage_roles_even_if_renamed(security_team):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, security_team["admin"])
        response = await client.post("/api/v1/roles", json={
            "name": "New role", "permission_codes": ["offers.view"],
        })
        assert response.status_code == 201
        role_id = response.json()["id"]
        assert (await client.patch(f"/api/v1/roles/{role_id}", json={
            "permission_codes": ["offers.view", "settings.view"],
        })).status_code == 200
        assert (await client.delete(f"/api/v1/roles/{role_id}")).status_code == 204


async def test_password_reset_revokes_all_target_sessions_only(security_team):
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as manager,
        AsyncClient(transport=transport, base_url="http://test") as first,
        AsyncClient(transport=transport, base_url="http://test") as second,
    ):
        await login(manager, security_team["manager"])
        await login(first, security_team["buyer"])
        await login(second, security_team["buyer"])
        response = await manager.post(f"/api/v1/users/{security_team['buyer']['id']}/reset-password")
        assert response.status_code == 200
        assert (await first.get("/api/v1/auth/me")).status_code == 401
        assert (await second.get("/api/v1/auth/me")).status_code == 401
        assert (await manager.get("/api/v1/auth/me")).status_code == 200
        assert (await first.post("/api/v1/auth/login", json={
            "login": security_team["buyer"]["login"], "password": "security-test-password",
        })).status_code == 401
        await login(first, security_team["buyer"], response.json()["temporary_password"])


@pytest.mark.parametrize("path", ["", "/status"])
async def test_unblocking_does_not_restore_old_sessions(security_team, path):
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as manager,
        AsyncClient(transport=transport, base_url="http://test") as buyer,
    ):
        await login(manager, security_team["manager"])
        await login(buyer, security_team["buyer"])
        url = f"/api/v1/users/{security_team['buyer']['id']}{path}"
        for status in ("blocked", "active"):
            kwargs = {"params": {"new_status": status}} if path else {"json": {"status": status}}
            assert (await manager.patch(url, **kwargs)).status_code == 200
        assert (await buyer.get("/api/v1/auth/me")).status_code == 401
        await login(buyer, security_team["buyer"])


async def test_cookie_security_flags(security_team, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "cookie_secure", True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        response = await login(client, security_team["buyer"])
        cookie = response.headers["set-cookie"].lower()
        assert "; secure" in cookie and "; httponly" in cookie and "samesite=lax" in cookie
        assert (await client.get("/api/v1/auth/me")).status_code == 200


def test_multipart_parser_has_header_limits():
    from python_multipart import MultipartParser
    from python_multipart.exceptions import MultipartParseError

    assert tuple(map(int, version("python-multipart").split("."))) >= (0, 0, 32)
    parser = MultipartParser(b"security-test")
    # Small, bounded local regression input, never sent to production.
    with pytest.raises(MultipartParseError):
        parser.write(b"--security-test\r\nX-Test: " + b"x" * 65536 + b"\r\n\r\n")
