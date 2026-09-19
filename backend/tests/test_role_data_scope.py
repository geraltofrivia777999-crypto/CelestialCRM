import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.core.deps import accessible_user_ids
from app.core.security import hash_password
from app.main import app
from app.models import Permission, Role, User, UserParent


async def _team(suffix: str) -> dict:
    """Тимлид и его баер в отдельных ролях — на них и проверяем область."""
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        view = await db.scalar(select(Permission).where(Permission.code == "media.view"))
        roles = {}
        for scope in ("all", "team", "own"):
            role = Role(
                workspace_id=admin.workspace_id,
                name=f"Scope {scope} {suffix}",
                description="",
                data_scope=scope,
                permissions=[view],
            )
            db.add(role)
            roles[scope] = role
        await db.flush()
        lead = User(
            workspace_id=admin.workspace_id,
            role_id=roles["team"].id,
            name=f"Lead {suffix}",
            login=f"scopelead{suffix}",
            password_hash=hash_password("scope-password"),
        )
        buyer = User(
            workspace_id=admin.workspace_id,
            role_id=roles["own"].id,
            name=f"Buyer {suffix}",
            login=f"scopebuyer{suffix}",
            password_hash=hash_password("scope-password"),
        )
        db.add_all([lead, buyer])
        await db.flush()
        db.add(UserParent(user_id=buyer.id, parent_id=lead.id))
        await db.commit()
        return {
            "roles": {scope: role.id for scope, role in roles.items()},
            "lead": lead.id,
            "buyer": buyer.id,
        }


async def _visible(user_id: uuid.UUID) -> set[uuid.UUID]:
    async with SessionLocal() as db:
        user = await db.scalar(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.role).selectinload(Role.permissions))
        )
        return await accessible_user_ids(db, user)


async def test_role_data_scope_decides_whose_rows_are_visible(database) -> None:
    """«Все данные», «Данные своей команды» и «Только свои данные» — три разных списка."""
    suffix = uuid.uuid4().hex[:6]
    made = await _team(suffix)
    try:
        # Своя команда: тимлид видит себя и подчинённого.
        team_view = await _visible(made["lead"])
        assert made["lead"] in team_view
        assert made["buyer"] in team_view

        # Только свои: баер видит лишь себя, хотя тимлид над ним.
        own_view = await _visible(made["buyer"])
        assert own_view == {made["buyer"]}

        # Все данные: в списке и администратор, которого нет в ветке тимлида.
        async with SessionLocal() as db:
            lead = await db.get(User, made["lead"])
            lead.role_id = made["roles"]["all"]
            await db.commit()
        all_view = await _visible(made["lead"])
        async with SessionLocal() as db:
            admin = await db.scalar(select(User).where(User.login == "admin"))
            assert admin.id in all_view
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(UserParent).where(UserParent.user_id.in_([made["buyer"], made["lead"]]))
            )
            await db.execute(delete(User).where(User.id.in_([made["buyer"], made["lead"]])))
            await db.execute(delete(Role).where(Role.id.in_(list(made["roles"].values()))))
            await db.commit()


async def test_team_scope_still_limits_a_role_with_every_permission(database) -> None:
    """Область строк важнее набора прав: CMO с полными правами видит свою ветку."""
    suffix = uuid.uuid4().hex[:6]
    made = await _team(suffix)
    try:
        async with SessionLocal() as db:
            role = await db.get(Role, made["roles"]["team"])
            role.permissions = list((await db.scalars(select(Permission))).all())
            admin = await db.scalar(select(User).where(User.login == "admin"))
            admin_id = admin.id
            await db.commit()

        visible = await _visible(made["lead"])
        assert made["lead"] in visible
        assert made["buyer"] in visible
        assert admin_id not in visible
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(UserParent).where(UserParent.user_id.in_([made["buyer"], made["lead"]]))
            )
            await db.execute(delete(User).where(User.id.in_([made["buyer"], made["lead"]])))
            await db.execute(delete(Role).where(Role.id.in_(list(made["roles"].values()))))
            await db.commit()


async def test_role_api_keeps_the_chosen_scope(database) -> None:
    """Область доступа задаётся при создании роли и меняется при правке."""
    suffix = uuid.uuid4().hex[:6]
    role_id = None
    try:
        with TestClient(app) as client:
            assert client.post(
                "/api/v1/auth/login", json={"login": "admin", "password": "test-password"}
            ).status_code == 200
            created = client.post(
                "/api/v1/roles",
                json={
                    "name": f"Scope API {suffix}",
                    "description": "",
                    "data_scope": "own",
                    "permission_codes": ["media.view"],
                },
            )
            assert created.status_code == 201, created.text
            role_id = created.json()["id"]
            assert created.json()["data_scope"] == "own"

            updated = client.patch(f"/api/v1/roles/{role_id}", json={"data_scope": "all"})
            assert updated.status_code == 200
            assert updated.json()["data_scope"] == "all"

            listed = client.get("/api/v1/roles").json()
            mine = next(row for row in listed if row["id"] == role_id)
            assert mine["data_scope"] == "all"

            wrong = client.patch(f"/api/v1/roles/{role_id}", json={"data_scope": "everything"})
            assert wrong.status_code == 422
    finally:
        if role_id:
            async with SessionLocal() as db:
                await db.execute(delete(Role).where(Role.id == uuid.UUID(role_id)))
                await db.commit()
