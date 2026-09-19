import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_any_permission, require_permission
from app.models import (
    FinanceServiceValue,
    FinanceSpendValue,
    MediaServiceValue,
    MediaSpendValue,
    Service,
    SpendProvider,
    Status,
    User,
)
from app.schemas import (
    Page,
    ServiceIn,
    ServiceOut,
    SpendProviderIn,
    SpendProviderOut,
)
from app.services import finance_spend
from app.services.audit import audit

MAX_PAGE_SIZE = 500

router = APIRouter(tags=["settings"])


@router.get("/services", response_model=Page)
async def list_services(
    search: str | None = None,
    status: Status | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(
        require_any_permission("settings.view", "media.view", "finance.view")
    ),
) -> Page:
    filters = [Service.workspace_id == current.workspace_id]
    if search:
        filters.append(Service.name.ilike(f"%{search}%"))
    if status:
        filters.append(Service.status == status)
    total = await db.scalar(select(func.count()).select_from(Service).where(*filters))
    items = list(
        (
            await db.execute(
                select(Service)
                .where(*filters)
                .order_by(Service.name)
                .limit(min(limit, MAX_PAGE_SIZE))
                .offset(offset)
            )
        ).scalars()
    )
    return Page(
        items=[ServiceOut.model_validate(item).model_dump(mode="json") for item in items],
        total=total or 0,
        limit=min(limit, MAX_PAGE_SIZE),
        offset=offset,
    )


@router.post("/services", response_model=ServiceOut, status_code=201)
async def create_service(
    payload: ServiceIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> Service:
    service = Service(workspace_id=current.workspace_id, **payload.model_dump())
    db.add(service)
    await audit(db, current, "service.created", f"Created service {service.name}", request=request)
    await db.commit()
    await db.refresh(service)
    return service


@router.put("/services/{service_id}", response_model=ServiceOut)
async def update_service(
    service_id: uuid.UUID,
    payload: ServiceIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> Service:
    service = await db.get(Service, service_id)
    if not service or service.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Service not found")
    for key, value in payload.model_dump().items():
        setattr(service, key, value)
    await audit(db, current, "service.updated", f"Updated service {service.name}", request=request)
    await db.commit()
    return service


@router.get("/spend-providers", response_model=Page)
async def list_spend_providers(
    search: str | None = None,
    status: Status | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(
        require_any_permission("settings.view", "media.view", "finance.view")
    ),
) -> Page:
    filters = [SpendProvider.workspace_id == current.workspace_id]
    if search:
        filters.append(SpendProvider.name.ilike(f"%{search}%"))
    if status:
        filters.append(SpendProvider.status == status)
    total = await db.scalar(select(func.count()).select_from(SpendProvider).where(*filters))
    items = list(
        (
            await db.execute(
                select(SpendProvider)
                .where(*filters)
                .order_by(SpendProvider.name)
                .limit(min(limit, MAX_PAGE_SIZE))
                .offset(offset)
            )
        ).scalars()
    )
    return Page(
        items=[SpendProviderOut.model_validate(item).model_dump(mode="json") for item in items],
        total=total or 0,
        limit=min(limit, MAX_PAGE_SIZE),
        offset=offset,
    )


@router.post("/spend-providers", response_model=SpendProviderOut, status_code=201)
async def create_spend_provider(
    payload: SpendProviderIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> SpendProvider:
    provider = SpendProvider(workspace_id=current.workspace_id, **payload.model_dump())
    db.add(provider)
    await audit(
        db, current, "spend_provider.created", f"Created provider {provider.name}", request=request
    )
    await db.commit()
    await db.refresh(provider)
    return provider


@router.put("/spend-providers/{provider_id}", response_model=SpendProviderOut)
async def update_spend_provider(
    provider_id: uuid.UUID,
    payload: SpendProviderIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> SpendProvider:
    await finance_spend.lock_workspace(db, current.workspace_id)
    provider = await db.get(SpendProvider, provider_id)
    if not provider or provider.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Spend provider not found")
    for key, value in payload.model_dump().items():
        setattr(provider, key, value)
    await audit(
        db, current, "spend_provider.updated", f"Updated provider {provider.name}", request=request
    )
    await finance_spend.refresh_workspace(db, current.workspace_id)
    await db.commit()
    return provider


@router.delete("/services/{service_id}", status_code=204, response_class=Response)
async def delete_service(
    service_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> None:
    service = await db.get(Service, service_id)
    if not service or service.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Service not found")
    used = await db.scalar(
        select(func.count()).select_from(MediaServiceValue).where(
            MediaServiceValue.service_id == service.id
        )
    ) or await db.scalar(
        select(func.count()).select_from(FinanceServiceValue).where(
            FinanceServiceValue.service_id == service.id
        )
    )
    if used:
        raise HTTPException(
            status_code=409,
            detail="Сервис используется в записях. Деактивируйте его вместо удаления.",
        )
    await audit(
        db,
        current,
        "service.deleted",
        f"Deleted service {service.name}",
        request=request,
        data={"service_name": service.name},
    )
    await db.delete(service)
    await db.commit()


@router.delete("/spend-providers/{provider_id}", status_code=204, response_class=Response)
async def delete_spend_provider(
    provider_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(require_permission("settings.manage")),
) -> None:
    provider = await db.get(SpendProvider, provider_id)
    if not provider or provider.workspace_id != current.workspace_id:
        raise HTTPException(status_code=404, detail="Spend provider not found")
    used = await db.scalar(
        select(func.count()).select_from(MediaSpendValue).where(
            MediaSpendValue.provider_id == provider.id
        )
    ) or await db.scalar(
        select(func.count()).select_from(FinanceSpendValue).where(
            FinanceSpendValue.provider_id == provider.id
        )
    )
    if used:
        raise HTTPException(
            status_code=409,
            detail="Агент/платёжка используется в записях. Деактивируйте вместо удаления.",
        )
    await audit(
        db,
        current,
        "spend_provider.deleted",
        f"Deleted provider {provider.name}",
        request=request,
        data={"provider_name": provider.name},
    )
    await db.delete(provider)
    await db.commit()
