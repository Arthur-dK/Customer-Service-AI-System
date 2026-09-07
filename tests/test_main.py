import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


def test_rss_helper_does_not_raise():
    from app.main import _rss_mb

    assert isinstance(_rss_mb(), str)


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
