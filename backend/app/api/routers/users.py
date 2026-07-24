import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import accessible_user_ids, get_current_user, require_permission
from app.core.security import hash_password
from app.models import Permission, Role, Status, User, UserParent, UserPreference
from app.schemas import (
    Page,
    PreferenceIn,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services.audit import audit

MAX_PAGE_SIZE = 500

router = APIRouter(tags=["team"])


@router.get("/users/options")
async def user_options(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
) -> list[dict]:
    visible_users = await accessible_user_ids(db, current)
    rows = (
        await db.execute(
            select(User.id, User.name, User.login)
            .where(
                User.workspace_id == current.workspace_id,
                User.id.in_(visible_users),
                User.status == Status.active,
            )
            .order_by(User.name)
        )
    ).all()
    return [{"id": str(user_id), "name": name, "login": login} for user_id, name, login in rows]


@router.get("/users", response_model=Page)
async def list_users(
    search: str | None = None,
    role_id: uuid.UUID | None = None,
    status: Status | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.view")),
) -> Page:
    visible_users = await accessible_user_ids(db, current)
    filters = [
        User.workspace_id == current.workspace_id,
        User.id.in_(visible_users),
    ]
    if search:
        filters.append((User.name.ilike(f"%{search}%")) | (User.login.ilike(f"%{search}%")))
    if role_id:
        filters.append(User.role_id == role_id)
    if status:
        filters.append(User.status == status)
    total = await db.scalar(select(func.count()).select_from(User).where(*filters))
    stmt = (
        select(User)
        .where(*filters)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .order_by(User.name)
        .limit(min(limit, MAX_PAGE_SIZE))
        .offset(offset)
    )
    users = list((await db.execute(stmt)).scalars().all())
    parents = await _parents_by_user(db, [user.id for user in users])
    return Page(
        items=[_user_payload(user, parents.get(user.id, [])) for user in users],
        total=total or 0,
        limit=min(limit, MAX_PAGE_SIZE),
        offset=offset,
    )


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.view")),
) -> dict:
    user = await _managed_user(db, current, user_id)
    parents = await _parents_by_user(db, [user.id])
    return _user_payload(user, parents.get(user.id, []))


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> dict:
    role = await _workspace_role(db, current.workspace_id, payload.role_id)
    login = _normalized_login(payload.login)
    await _ensure_login_available(db, current.workspace_id, login)
    user = User(
        workspace_id=current.workspace_id,
        role_id=role.id,
        name=payload.name.strip(),
        login=login,
        password_hash=hash_password(payload.password),
        status=payload.status,
        keitaro_company_group=_optional_text(payload.keitaro_company_group),
        keitaro_offer_group=_optional_text(payload.keitaro_offer_group),
    )
    db.add(user)
    await db.flush()
    parent_ids = set(payload.parent_ids)
    await _validate_parents(db, current, user.id, parent_ids)
    db.add_all([UserParent(user_id=user.id, parent_id=parent_id) for parent_id in parent_ids])
    await audit(
        db,
        current,
        "user.created",
        f"Created user {user.login}",
        request=request,
        entity_type="user",
        entity_id=str(user.id),
    )
    await db.commit()
    user = await _load_user(db, user.id)
    parents = await _parents_by_user(db, [user.id])
    return _user_payload(user, parents.get(user.id, []))


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> dict:
    user = await _managed_user(db, current, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if "role_id" in changes:
        role = await _workspace_role(db, current.workspace_id, changes["role_id"])
        if user.id == current.id and role.id != user.role_id:
            raise HTTPException(status_code=422, detail="You cannot change your own role")
        user.role_id = role.id
    if "login" in changes:
        login = _normalized_login(changes["login"])
        await _ensure_login_available(db, current.workspace_id, login, exclude_id=user.id)
        user.login = login
    if "name" in changes:
        user.name = changes["name"].strip()
    if "status" in changes:
        if user.id == current.id and changes["status"] != Status.active:
            raise HTTPException(status_code=422, detail="You cannot block your own account")
        user.status = changes["status"]
    if "keitaro_company_group" in changes:
        user.keitaro_company_group = _optional_text(changes["keitaro_company_group"])
    if "keitaro_offer_group" in changes:
        user.keitaro_offer_group = _optional_text(changes["keitaro_offer_group"])
    if "parent_ids" in changes:
        parent_ids = set(changes["parent_ids"] or [])
        await _validate_parents(db, current, user.id, parent_ids)
        await db.execute(delete(UserParent).where(UserParent.user_id == user.id))
        db.add_all(
            [UserParent(user_id=user.id, parent_id=parent_id) for parent_id in parent_ids]
        )
    await audit(
        db,
        current,
        "user.updated",
        f"Updated user {user.login}",
        request=request,
        entity_type="user",
        entity_id=str(user.id),
    )
    await db.commit()
    user = await _load_user(db, user.id)
    parents = await _parents_by_user(db, [user.id])
    return _user_payload(user, parents.get(user.id, []))


@router.patch("/users/{user_id}/status", response_model=UserOut)
async def set_user_status(
    user_id: uuid.UUID,
    new_status: Status,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> dict:
    user = await _managed_user(db, current, user_id)
    if user.id == current.id and new_status != Status.active:
        raise HTTPException(status_code=422, detail="You cannot block your own account")
    user.status = new_status
    await audit(
        db,
        current,
        "user.status_changed",
        f"Changed {user.login} status to {new_status.value}",
        request=request,
        entity_type="user",
        entity_id=str(user.id),
    )
    await db.commit()
    user = await _load_user(db, user.id)
    parents = await _parents_by_user(db, [user.id])
    return _user_payload(user, parents.get(user.id, []))


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> dict:
    user = await _managed_user(db, current, user_id)
    temporary = secrets.token_urlsafe(12)
    user.password_hash = hash_password(temporary)
    await audit(
        db,
        current,
        "user.password_reset",
        f"Reset password for {user.login}",
        request=request,
        entity_type="user",
        entity_id=str(user.id),
    )
    await db.commit()
    return {"temporary_password": temporary}


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.view")),
) -> list[Role]:
    stmt = (
        select(Role)
        .where(Role.workspace_id == current.workspace_id)
        .options(selectinload(Role.permissions))
        .order_by(Role.name)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(
    payload: RoleCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> Role:
    name = payload.name.strip()
    await _ensure_role_name_available(db, current.workspace_id, name)
    permissions = await _permissions(db, payload.permission_codes)
    role = Role(
        workspace_id=current.workspace_id,
        name=name,
        description=payload.description.strip(),
        permissions=permissions,
    )
    db.add(role)
    await audit(db, current, "role.created", f"Created role {role.name}", request=request)
    await db.commit()
    await db.refresh(role, ["permissions"])
    return role


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> Role:
    role = await db.get(Role, role_id, options=[selectinload(Role.permissions)])
    if not role or role.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Role not found")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        name = changes["name"].strip()
        if role.is_system and name != role.name:
            raise HTTPException(status_code=422, detail="System role cannot be renamed")
        await _ensure_role_name_available(
            db, current.workspace_id, name, exclude_id=role.id
        )
        role.name = name
    if "description" in changes:
        role.description = changes["description"].strip()
    if "permission_codes" in changes:
        role.permissions = await _permissions(db, changes["permission_codes"] or [])
    await audit(
        db,
        current,
        "role.updated",
        f"Updated role {role.name}",
        request=request,
        entity_type="role",
        entity_id=str(role.id),
    )
    await db.commit()
    await db.refresh(role, ["permissions"])
    return role


@router.delete("/roles/{role_id}", status_code=204, response_class=Response)
async def delete_role(
    role_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> None:
    role = await db.get(Role, role_id)
    if not role or role.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=422, detail="System role cannot be deleted")
    assigned = await db.scalar(
        select(func.count()).select_from(User).where(User.role_id == role.id)
    )
    if assigned:
        raise HTTPException(
            status_code=409,
            detail="Роль назначена пользователям. Сначала переназначьте их.",
        )
    await audit(
        db,
        current,
        "role.deleted",
        f"Deleted role {role.name}",
        request=request,
        entity_type="role",
        entity_id=str(role.id),
    )
    await db.delete(role)
    await db.commit()


@router.post("/roles/{role_id}/copy", response_model=RoleOut, status_code=201)
async def copy_role(
    role_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> Role:
    source = await db.get(Role, role_id, options=[selectinload(Role.permissions)])
    if not source or source.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Role not found")
    base_name = f"{source.name} (копия)"
    name = base_name
    suffix = 2
    while await db.scalar(
        select(Role.id).where(
            Role.workspace_id == current.workspace_id,
            func.lower(Role.name) == name.lower(),
        )
    ):
        name = f"{base_name} {suffix}"
        suffix += 1
    role = Role(
        workspace_id=current.workspace_id,
        name=name,
        description=source.description,
        permissions=list(source.permissions),
    )
    db.add(role)
    await audit(
        db,
        current,
        "role.copied",
        f"Copied role {source.name} to {name}",
        request=request,
        entity_type="role",
    )
    await db.commit()
    await db.refresh(role, ["permissions"])
    return role


@router.get("/me/preferences/{key}")
async def get_preference(
    key: str,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict:
    preference = await db.scalar(
        select(UserPreference).where(
            UserPreference.user_id == current.id, UserPreference.key == key
        )
    )
    return {"key": key, "value": preference.value if preference else {}}


@router.put("/me/preferences/{key}")
async def set_preference(
    key: str,
    payload: PreferenceIn,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict:
    if len(key) > 100:
        raise HTTPException(status_code=422, detail="Preference key is too long")
    preference = await db.scalar(
        select(UserPreference).where(
            UserPreference.user_id == current.id, UserPreference.key == key
        )
    )
    if preference:
        preference.value = payload.value
    else:
        db.add(UserPreference(user_id=current.id, key=key, value=payload.value))
    await db.commit()
    return {"key": key, "value": payload.value}


@router.get("/permissions", response_model=list[str])
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("team.manage")),
) -> list[str]:
    return list((await db.execute(select(Permission.code).order_by(Permission.code))).scalars())


async def _load_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(
        User,
        user_id,
        options=[selectinload(User.role).selectinload(Role.permissions)],
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _managed_user(db: AsyncSession, current: User, user_id: uuid.UUID) -> User:
    visible = await accessible_user_ids(db, current)
    if user_id not in visible:
        raise HTTPException(status_code=404, detail="User not found")
    user = await _load_user(db, user_id)
    if user.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _workspace_role(
    db: AsyncSession, workspace_id: uuid.UUID, role_id: uuid.UUID
) -> Role:
    role = await db.get(Role, role_id)
    if not role or role.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


async def _ensure_login_available(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    login: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    filters = [User.workspace_id == workspace_id, User.login == login]
    if exclude_id:
        filters.append(User.id != exclude_id)
    if await db.scalar(select(User.id).where(*filters)):
        raise HTTPException(status_code=409, detail="Login already exists")


async def _ensure_role_name_available(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    name: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    filters = [Role.workspace_id == workspace_id, func.lower(Role.name) == name.lower()]
    if exclude_id:
        filters.append(Role.id != exclude_id)
    if await db.scalar(select(Role.id).where(*filters)):
        raise HTTPException(status_code=409, detail="Role name already exists")


async def _permissions(db: AsyncSession, codes: list[str]) -> list[Permission]:
    unique_codes = set(codes)
    rows = list(
        (
            await db.execute(select(Permission).where(Permission.code.in_(unique_codes)))
        ).scalars()
    )
    if len(rows) != len(unique_codes):
        raise HTTPException(status_code=422, detail="Unknown permission code")
    return rows


async def _validate_parents(
    db: AsyncSession,
    current: User,
    user_id: uuid.UUID,
    parent_ids: set[uuid.UUID],
) -> None:
    if user_id in parent_ids:
        raise HTTPException(status_code=422, detail="User cannot be their own parent")
    visible = await accessible_user_ids(db, current)
    if not parent_ids.issubset(visible):
        raise HTTPException(status_code=422, detail="Parent user is outside your hierarchy")
    workspace_user_ids = set(
        await db.scalars(select(User.id).where(User.workspace_id == current.workspace_id))
    )
    if not parent_ids.issubset(workspace_user_ids):
        raise HTTPException(status_code=422, detail="Parent user is invalid")
    edges: dict[uuid.UUID, set[uuid.UUID]] = {}
    rows = (
        await db.execute(
            select(UserParent.user_id, UserParent.parent_id).where(
                UserParent.user_id.in_(workspace_user_ids)
            )
        )
    ).all()
    for child_id, parent_id in rows:
        if child_id != user_id:
            edges.setdefault(child_id, set()).add(parent_id)
    edges[user_id] = parent_ids
    for parent_id in parent_ids:
        stack = [parent_id]
        visited: set[uuid.UUID] = set()
        while stack:
            node = stack.pop()
            if node == user_id:
                raise HTTPException(
                    status_code=422, detail="Parent hierarchy cannot contain a cycle"
                )
            if node in visited:
                continue
            visited.add(node)
            stack.extend(edges.get(node, set()))


async def _parents_by_user(
    db: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict]]:
    if not user_ids:
        return {}
    rows = (
        await db.execute(
            select(UserParent.user_id, User.id, User.name, User.login)
            .join(User, User.id == UserParent.parent_id)
            .where(UserParent.user_id.in_(user_ids))
            .order_by(User.name)
        )
    ).all()
    result: dict[uuid.UUID, list[dict]] = {}
    for child_id, parent_id, name, login in rows:
        result.setdefault(child_id, []).append(
            {"id": parent_id, "name": name, "login": login}
        )
    return result


def _user_payload(user: User, parents: list[dict]) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "login": user.login,
        "status": user.status,
        "role": RoleOut.model_validate(user.role).model_dump(mode="json"),
        "parents": parents,
        "keitaro_company_group": user.keitaro_company_group,
        "keitaro_offer_group": user.keitaro_offer_group,
    }


def _normalized_login(value: str) -> str:
    return value.strip().removeprefix("@").lower()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
