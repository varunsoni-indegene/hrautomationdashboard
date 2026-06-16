"""
app/main.py
-----------
FastAPI application entry point.

Development:   uvicorn app.main:app --reload --port 8000
Production:    gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import check_db_connection

# ── Import all routers ────────────────────────────────────────────────────────
from backend.app.routers.team import router as team_router
from backend.app.routers.employee import (
    auth_router,
    employee_router,
    predictions_router,
)

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.is_dev else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    logger.info("Starting HR Analytics API (%s)...", settings.APP_ENV)
    try:
        check_db_connection()
        logger.info("Database connection OK.")
    except Exception as e:
        logger.critical("Database unreachable at startup: %s", e)
        # Don't crash the process — the app can still serve /health
        # but data endpoints will fail until DB is back.
    yield
    # SHUTDOWN
    logger.info("Shutting down HR Analytics API.")


# ── App instance ──────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.API_VERSION,
    description=(
        "HR Analytics backend API.\n\n"
        "Authentication: Azure Entra ID Bearer token required on all data endpoints.\n"
        "Access control: Managers see only their direct reports."
    ),
    lifespan=lifespan,
    # In production, hide the docs unless you explicitly want them public.
    docs_url="/docs" if settings.is_dev else None,
    redoc_url="/redoc" if settings.is_dev else None,
)


# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


# ── Routers ───────────────────────────────────────────────────────────────────
# All routes prefixed with /api/v1
API_PREFIX = f"/api/{settings.API_VERSION}"

app.include_router(auth_router,        prefix=API_PREFIX)
app.include_router(team_router,        prefix=API_PREFIX)
app.include_router(employee_router,    prefix=API_PREFIX)
app.include_router(predictions_router, prefix=API_PREFIX)


# ── Health check (no auth) ────────────────────────────────────────────────────
@app.get("/health", tags=["Health"], include_in_schema=False)
def health_check():
    """
    Used by Azure App Service health probes and your CI/CD pipeline.
    Returns 200 if the app is running. Separately checks DB.
    """
    try:
        check_db_connection()
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)[:100]}"

    return JSONResponse({
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.API_VERSION,
        "environment": settings.APP_ENV,
        "database": db_status,
    })


@app.get("/", include_in_schema=False)
def root():
    return {"message": f"{settings.APP_NAME} is running. See /docs for API reference."}
