from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_twilio_webhook_endpoint_includes_stream_and_caller_params():
    response = client.post(
        "/voice/incoming",
        headers={"host": "localhost:8000"},
        data={"From": "+972501234567"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "<Stream url=" in response.text
    assert 'name="from" value="+972501234567"' in response.text
    assert 'name="country_code" value="IL"' in response.text


def test_twilio_webhook_gb_caller_gets_gb_country_param():
    response = client.post(
        "/voice/incoming",
        headers={"host": "localhost:8000"},
        data={"From": "+442071838750"},
    )
    assert response.status_code == 200
    assert 'name="country_code" value="GB"' in response.text
    assert 'name="from" value="+442071838750"' in response.text


def test_twilio_webhook_unknown_caller_omits_country_when_unresolved():
    response = client.post(
        "/voice/incoming",
        headers={"host": "localhost:8000"},
        data={},
    )
    assert response.status_code == 200
    assert "<Stream url=" in response.text
    assert "country_code" not in response.text
