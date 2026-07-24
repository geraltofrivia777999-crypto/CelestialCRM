from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, User


async def audit(
    db: AsyncSession,
    user: User,
    event_type: str,
    description: str,
    *,
    request: Request | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    data: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            workspace_id=user.workspace_id,
            user_id=user.id,
            event_type=event_type,
            description=description,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=request.client.host if request and request.client else None,
            data=data or {},
        )
    )

