"""Integration tests for health server."""

import pytest
from httpx import ASGITransport, AsyncClient

from worker_template.health_server import health_app


@pytest.mark.integration
async def test_health_endpoint():
    """Verify the health check endpoint returns healthy status."""
    transport = ASGITransport(app=health_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


@pytest.mark.integration
async def test_ready_endpoint():
    """Verify the readiness check endpoint returns ready status."""
    transport = ASGITransport(app=health_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
