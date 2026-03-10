"""Lightweight health check server for Kubernetes probes.

Runs alongside the worker process on a separate port.
Provides /health, /ready, and /metrics endpoints.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from worker_template.core.config import settings
from worker_template.core.metrics import metrics_app

LOGGER = logging.getLogger(__name__)

health_app = FastAPI(
    title=f"{settings.app_name} Health",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@health_app.get("/health")
async def health_check() -> JSONResponse:
    """Liveness probe -- checks the process is alive."""
    return JSONResponse(
        content={"status": "healthy", "service": settings.app_name},
    )


@health_app.get("/ready")
async def readiness_check() -> JSONResponse:
    """Readiness probe -- checks the worker can accept tasks.

    In production, this could verify broker and DB connectivity.
    For now, if the health server is running, the worker is ready.
    """
    return JSONResponse(
        content={"status": "ready", "service": settings.app_name},
    )


if settings.enable_metrics:
    health_app.mount("/metrics", metrics_app)
