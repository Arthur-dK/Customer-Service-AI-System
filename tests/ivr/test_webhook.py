from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_twilio_webhook_endpoint():
    response = client.post("/voice/incoming", headers={"host": "localhost:8000"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "<Stream url=" in response.text
