import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models import Permission, Role, User


async def _person(suffix: str, codes: list[str]) -> tuple[str, uuid.UUID, uuid.UUID]:
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        permissions = list(
            (await db.execute(select(Permission).where(Permission.code.in_(codes)))).scalars()
        )
        assert len(permissions) == len(codes)
        role = Role(
            workspace_id=admin.workspace_id,
            name=f"Utilities tabs {suffix}",
            description="",
            permissions=permissions,
        )
        db.add(role)
        await db.flush()
        user = User(
            workspace_id=admin.workspace_id,
            role_id=role.id,
            name=f"utiltabs{suffix}",
            login=f"utiltabs{suffix}",
            password_hash=hash_password("tabs-password"),
        )
        db.add(user)
        await db.commit()
        return user.login, user.id, role.id


def _sign_in(client: TestClient, login: str) -> None:
    signed = client.post("/api/v1/auth/login", json={"login": login, "password": "tabs-password"})
    assert signed.status_code == 200, signed.text


async def test_channels_and_events_follow_their_own_permissions(database) -> None:
    """Без прав на «Каналы» и «Журнал» вкладки закрыты и на сервере.

    Список каналов остаётся доступен: без него не заполнить форму уведомления.
    """
    made = []
    try:
        base = ["utilities.view", "utilities.manage"]
        login, user_id, role_id = await _person(uuid.uuid4().hex[:6], base)
        made.append((user_id, role_id))
        with TestClient(app) as client:
            _sign_in(client, login)
            assert client.get("/api/v1/utilities/channels").status_code == 200
            assert client.get("/api/v1/utilities/events").status_code == 403
            missing = f"/api/v1/utilities/channels/{uuid.uuid4()}"
            assert client.delete(missing).status_code == 403

        full = base + ["utilities.channels", "utilities.events"]
        login, user_id, role_id = await _person(uuid.uuid4().hex[:6], full)
        made.append((user_id, role_id))
        with TestClient(app) as client:
            _sign_in(client, login)
            assert client.get("/api/v1/utilities/events").status_code == 200
            # Право есть — дальше обычная проверка: такого канала нет.
            missing = f"/api/v1/utilities/channels/{uuid.uuid4()}"
            assert client.delete(missing).status_code == 404
    finally:
        async with SessionLocal() as db:
            for user_id, role_id in made:
                await db.execute(delete(User).where(User.id == user_id))
                await db.execute(delete(Role).where(Role.id == role_id))
            await db.commit()
