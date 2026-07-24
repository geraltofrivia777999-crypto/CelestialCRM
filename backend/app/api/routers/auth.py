from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import create_session_token, hash_session_token, verify_password
from app.models import Role, Session, Status, User
from app.schemas import LoginRequest, UserOut
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    stmt = (
        select(User)
        .where(User.login == payload.login.strip().lower())
        .options(selectinload(User.role).selectinload(Role.permissions))
    )
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid login or password")
    if user.status != Status.active:
        raise HTTPException(status_code=403, detail="User is blocked")
    token, token_hash, expires_at = create_session_token()
    db.add(Session(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
    await audit(db, user, "auth.login", "User signed in", request=request)
    await db.commit()
    response.set_cookie(
        "crm_session",
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_days * 86400,
        path="/",
    )
    return user


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    crm_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    if crm_session:
        await db.execute(delete(Session).where(Session.token_hash == hash_session_token(crm_session)))
    else:
        await db.execute(
            delete(Session).where(
                Session.user_id == user.id, Session.expires_at <= datetime.now(UTC)
            )
        )
    await db.commit()
    response.delete_cookie("crm_session", path="/")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
