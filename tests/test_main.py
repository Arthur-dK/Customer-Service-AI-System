import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

@pytest.mark.asyncio
async def test_websocket_connection():
    # Verify WebSocket handshake succeeds
    from fastapi.testclient import TestClient
    client = TestClient(app)
    with client.websocket_connect("/media-stream") as websocket:
        assert websocket is not None