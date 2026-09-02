import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import settings
from app.services.meta_session import MetaSessionError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # Браузерные сессии Meta Ads: при остановке закрываем все открытые браузеры,
    # чтобы не оставлять процессы Playwright висеть.
    from app.services.meta_session import get_session_manager

    await get_session_manager().shutdown()


app = FastAPI(
    title="Celestial CRM API",
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/docs",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api/v1")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "environment": settings.environment}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": f"http_{exc.status_code}",
                "message": str(exc.detail),
                "details": {},
            }
        },
    )


@app.exception_handler(MetaSessionError)
async def meta_session_exception_handler(
    _: Request, exc: MetaSessionError
) -> JSONResponse:
    """Проблемы браузерной сессии Meta — понятным текстом, а не 500.

    Сюда попадают «сессия не найдена на диске», «требуется ручной вход» и т.п.
    из любого эндпоинта, который работает с токеном сессии.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "meta_session_error",
                "message": str(exc),
                "details": {},
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": {
                    "fields": [
                        {
                            "field": ".".join(str(part) for part in error["loc"]),
                            "message": error["msg"],
                            "type": error["type"],
                        }
                        for error in exc.errors()
                    ]
                },
            }
        },
    )
