import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import Role, User


async def test_any_role_can_be_deleted_with_people_moved(database) -> None:
    suffix = uuid.uuid4().hex[:6]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        doomed = Role(workspace_id=admin.workspace_id, name=f"Doomed {suffix}",
                      description="", is_system=True)
        target = Role(workspace_id=admin.workspace_id, name=f"Target {suffix}", description="")
        db.add_all([doomed, target])
        await db.flush()
        person = User(workspace_id=admin.workspace_id, role_id=doomed.id, name=f"Moved {suffix}",
                      login=f"moved{suffix}", password_hash=hash_password("moved-password"))
        db.add(person)
        await db.commit()
        ids = {"doomed": doomed.id, "target": target.id, "person": person.id,
               "admin_role": admin.role_id}
    try:
        with TestClient(app) as client:
            assert client.post(
                "/api/v1/auth/login", json={"login": "admin", "password": "test-password"}
            ).status_code == 200
            path = f"/api/v1/roles/{ids['doomed']}"
            # С пользователями без роли для переноса — нельзя: человек остался бы без роли.
            assert client.delete(path).status_code == 409
            assert client.delete(
                path, params={"replacement_role_id": str(ids["doomed"])}
            ).status_code == 422
            # Стандартная роль (is_system) удаляется так же, люди переезжают.
            assert client.delete(
                path, params={"replacement_role_id": str(ids["target"])}
            ).status_code == 204
            # Свою роль удалить нельзя — иначе администратор отрежет себе доступ.
            assert client.delete(f"/api/v1/roles/{ids['admin_role']}").status_code == 422
        async with SessionLocal() as db:
            assert await db.get(Role, ids["doomed"]) is None
            assert (await db.get(User, ids["person"])).role_id == ids["target"]
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == ids["person"]))
            await db.execute(delete(Role).where(Role.id.in_([ids["doomed"], ids["target"]])))
            await db.commit()
