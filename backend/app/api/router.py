from fastapi import APIRouter

from app.api.routers import (
    analytics,
    auth,
    catalog,
    finance,
    integrations,
    knowledge,
    meta,
    partner_integrations,
    recruitment,
    references,
    salary,
    tasks,
    users,
    utilities,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(catalog.router)
api_router.include_router(integrations.router)
api_router.include_router(meta.router)
api_router.include_router(references.router)
api_router.include_router(analytics.router)
api_router.include_router(finance.router)
api_router.include_router(tasks.router)
api_router.include_router(knowledge.router)
api_router.include_router(salary.router)
api_router.include_router(utilities.router)
api_router.include_router(recruitment.router)
api_router.include_router(partner_integrations.router)

