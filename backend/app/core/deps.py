import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import hash_session_token
from app.models import Role, Session, Status, User, UserParent


async def get_current_user(
    crm_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not crm_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    stmt = (
        select(User)
        .join(Session)
        .where(
            Session.token_hash == hash_session_token(crm_session),
            Session.expires_at > datetime.now(UTC),
        )
        .options(selectinload(User.role).selectinload(Role.permissions))
    )
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or user.status != Status.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid")
    return user


def require_permission(permission_code: str) -> Callable:
    async def dependency(user: User = Depends(get_current_user)) -> User:
        codes = {permission.code for permission in user.role.permissions}
        if "*" not in codes and permission_code not in codes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
        return user

    return dependency


async def accessible_user_ids(db: AsyncSession, user: User) -> set[uuid.UUID]:
    """Return the user and every descendant visible through the parent hierarchy."""
    if user.role.name == "Administrator":
        rows = await db.scalars(
            select(User.id).where(User.workspace_id == user.workspace_id)
        )
        return set(rows)

    visible = {user.id}
    frontier = {user.id}
    while frontier:
        children = set(
            await db.scalars(
                select(UserParent.user_id).where(UserParent.parent_id.in_(frontier))
            )
        )
        frontier = children - visible
        visible.update(frontier)
    return visible


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid identifier") from exc
