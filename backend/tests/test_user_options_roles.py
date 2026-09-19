import uuid

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import Role, User
from tests.test_media_finance import _admin_client


async def test_user_options_can_be_filtered_to_team_leads(database) -> None:
    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        roles = {
            name: await db.scalar(
                select(Role).where(
                    Role.workspace_id == admin.workspace_id,
                    Role.name == name,
                )
            )
            for name in ("Team Lead", "Buyer")
        }
        people = [
            User(
                workspace_id=admin.workspace_id,
                role_id=roles["Team Lead"].id,
                name=f"Team Lead {suffix}",
                login=f"team-lead-{suffix}",
                password_hash=hash_password("test-password"),
            ),
            User(
                workspace_id=admin.workspace_id,
                role_id=roles["Buyer"].id,
                name=f"Buyer {suffix}",
                login=f"buyer-{suffix}",
                password_hash=hash_password("test-password"),
            ),
        ]
        db.add_all(people)
        await db.commit()
        ids = [person.id for person in people]

    try:
        with _admin_client() as client:
            response = client.get("/api/v1/users/options?role_name=Team%20Lead")
        assert response.status_code == 200
        returned = {row["id"] for row in response.json()}
        assert str(ids[0]) in returned
        assert str(ids[1]) not in returned
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.commit()
