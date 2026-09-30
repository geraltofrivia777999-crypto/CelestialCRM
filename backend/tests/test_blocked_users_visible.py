import uuid

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import Role, Status, User, UserParent
from tests.test_meta_entity_actions import _admin


async def test_blocked_user_keeps_numbers_but_not_forms(database) -> None:
    """Блокировка закрывает вход, а не прячет человека из Медиаборда и Финансов."""
    suffix = uuid.uuid4().hex[:6]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        role = await db.scalar(select(Role).where(Role.id == admin.role_id))
        blocked = User(workspace_id=admin.workspace_id, role_id=role.id, name=f"Gone {suffix}",
                       login=f"gone{suffix}", password_hash=hash_password("gone-password"),
                       status=Status.blocked)
        db.add(blocked)
        await db.flush()
        db.add(UserParent(user_id=blocked.id, parent_id=admin.id))
        await db.commit()
        blocked_id = str(blocked.id)
    client = _admin()
    try:
        default = client.get("/api/v1/users/options").json()
        assert blocked_id not in {row["id"] for row in default}
        wide = {row["id"]: row for row in
                client.get("/api/v1/users/options?include_blocked=true").json()}
        assert wide[blocked_id]["blocked"] is True
        scopes = client.get("/api/v1/finance/scopes?year=2026&month=9").json()
        buyers = {row["id"]: row for row in scopes["buyers"]}
        assert buyers[blocked_id]["blocked"] is True
        assert not any(row["blocked"] for key, row in buyers.items() if key != blocked_id
                       and row["name"] == "Administrator")
    finally:
        client.__exit__(None, None, None)
        async with SessionLocal() as db:
            await db.execute(delete(UserParent).where(UserParent.user_id == uuid.UUID(blocked_id)))
            await db.execute(delete(User).where(User.id == uuid.UUID(blocked_id)))
            await db.commit()
