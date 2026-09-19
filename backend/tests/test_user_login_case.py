import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.main import app
from app.models import User


def _sign_in(client: TestClient, login: str, password: str):
    return client.post("/api/v1/auth/login", json={"login": login, "password": password})


async def test_login_keeps_its_case_and_still_signs_in(database) -> None:
    """Логин сохраняется как его написали, а войти можно в любом регистре.

    Логин теперь и есть имя пользователя в CRM, поэтому «CG_Dmitry» должен
    остаться «CG_Dmitry». Заводить рядом такой же логин другим регистром
    нельзя: войти под ним всё равно не получится.
    """
    login = f"CG_Dmitry{uuid.uuid4().hex[:6]}"
    password = "case-password"
    created_id = None
    try:
        with TestClient(app) as client:
            assert _sign_in(client, "admin", "test-password").status_code == 200
            roles = client.get("/api/v1/roles").json()
            role = next(row for row in roles if row["name"] == "Buyer")
            created = client.post(
                "/api/v1/users",
                json={
                    "name": login,
                    "login": login,
                    "password": password,
                    "role_id": role["id"],
                },
            )
            assert created.status_code == 201, created.text
            created_id = created.json()["id"]
            assert created.json()["login"] == login
            assert created.json()["name"] == login

            duplicate = client.post(
                "/api/v1/users",
                json={
                    "name": login.lower(),
                    "login": login.lower(),
                    "password": password,
                    "role_id": role["id"],
                },
            )
            assert duplicate.status_code == 409

        with TestClient(app) as client:
            lower = _sign_in(client, login.lower(), password)
            assert lower.status_code == 200, lower.text
            assert lower.json()["login"] == login

        with TestClient(app) as client:
            assert _sign_in(client, login, password).status_code == 200
    finally:
        if created_id:
            async with SessionLocal() as db:
                user = await db.scalar(select(User).where(User.id == uuid.UUID(created_id)))
                if user:
                    await db.execute(delete(User).where(User.id == user.id))
                    await db.commit()
