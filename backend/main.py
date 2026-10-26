"""LeadForge FastAPI application composition entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import (
    discovery,
    exports,
    health,
    leads,
    scans,
    stats,
    validation,
)
from core.config import settings


def create_application() -> FastAPI:
    """Create the API with the stable middleware and router configuration."""
    application = FastAPI(
        title="LeadForge API",
        version="1.0.0",
        description="Bounded public-website lead discovery API.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in settings.frontend_origin.split(",")
            if origin.strip()
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    application.include_router(health.router)
    application.include_router(discovery.router)
    application.include_router(scans.router)
    application.include_router(leads.router)
    application.include_router(validation.router)
    application.include_router(stats.router)
    application.include_router(exports.router)
    return application


# Kept as a module-level object for uvicorn ``main:app`` and Vercel's adapter.
app = create_application()
