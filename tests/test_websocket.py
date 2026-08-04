import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from core.config import settings

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "project": settings.PROJECT_NAME}

def test_twilio_webhook_endpoint():
    response = client.post("/voice/incoming", headers={"host": "localhost:8000"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "<Stream url=" in response.text

def test_webhook_media_stream_handshake():
    with client.websocket_connect("/media-stream") as websocket:
        # 1. Simulate Twilio sending a "connected" event
        websocket.send_text(json.dumps({"event": "connected"}))
        # 2. Send 'start' event with mock StreamSid
        websocket.send_text(json.dumps({
            "event": "start",
            "streamSid": "MZ1234567890abcdef1234567890abcd",
            "start": {
                "streamSid": "MZ1234567890abcdef1234567890abcd",
                "accountSid": "AC1234567890",
                "callSid": "CA1234567890",
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1
                }
            }
        }))
        # 3. Send mock 8kHz mu-law audio chunk (base64)
        mock_mulaw_b64 = "////w=="
        websocket.send_text(json.dumps({
            "event": "media",
            "streamSid": "MZ1234567890abcdef1234567890abcd",
            "media": {
                "payload": mock_mulaw_b64,
                "timestamp": "20"
            }
        }))
        # 4. Send 'stop' event
        websocket.send_text(json.dumps({
            "event": "stop",
            "streamSid": "MZ1234567890abcdef1234567890abcd"
        }))