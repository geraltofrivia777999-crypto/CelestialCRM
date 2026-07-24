from fastapi import APIRouter

from app.api.routers import analytics, auth, catalog, integrations, references, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(catalog.router)
api_router.include_router(integrations.router)
api_router.include_router(references.router)
api_router.include_router(analytics.router)

