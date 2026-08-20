"""Fake Twilio media-stream harness: real FastAPI WebSocket + language selection."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app
from services.ivr.audio import chunk_mulaw, generate_silence_mulaw, generate_tone_mulaw
from services.ivr.lid import FixedLanguageIdentifier
from services.ivr.selection_store import clear_last_language_selection, get_last_language_selection
from services.ivr.tts import ToneTextToSpeech
from tests.ivr.manual.fake_twilio_stream import (
    STREAM_SID,
    connected_message,
    dtmf_message,
    media_message,
    parse_outbound,
    start_message,
    stop_message,
)


def _patch_fast_backends(monkeypatch) -> None:
    """Use tone TTS + fixed LID so the harness does not call SAPI/SpeechBrain."""
    tts = ToneTextToSpeech(ms_per_char=5, min_ms=40, max_ms=80)
    lid = FixedLanguageIdentifier(language="en", confidence=0.99)

    monkeypatch.setattr("app.api.ivr.get_tts", lambda: tts)
    monkeypatch.setattr("app.api.ivr.get_lid", lambda: lid)
    monkeypatch.setattr("app.api.ivr.settings.IVR_SILENCE_TIMEOUT_S", 0.3)
    monkeypatch.setattr("app.api.ivr.settings.IVR_PLAYBACK_REALTIME", False)
    monkeypatch.setattr("app.api.ivr.settings.IVR_MIN_LID_CONFIDENCE", 0.1)


def _collect_outbound_media(websocket, min_frames: int = 1, overall_timeout_s: float = 2.0) -> list[dict]:
    """
    Pull outbound WS messages until we have min_frames media events or timeout.

    Starlette TestClient receive_text blocks when empty, so we bound total wait
    and stop once enough media arrives (prompt playback).
    """
    messages: list[dict] = []
    media_count = 0
    deadline = time.time() + overall_timeout_s

    while time.time() < deadline and media_count < min_frames:
        # Small sleeps let the ASGI app flush outbound frames between receives.
        time.sleep(0.01)
        try:
            # Non-blocking-ish: rely on prompt having already queued frames.
            raw = websocket.receive_text()
        except Exception:
            break
        parsed = parse_outbound(raw)
        messages.append(parsed)
        if parsed.get("event") == "media":
            media_count += 1
            # Keep reading while frames are still flowing.
            deadline = max(deadline, time.time() + 0.2)
    return messages


def _wait_for_selection(timeout_s: float = 3.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = get_last_language_selection()
        if result is not None:
            return result
        time.sleep(0.05)
    return get_last_language_selection()


def test_media_stream_language_selection_via_dtmf(monkeypatch):
    """
    Phase 7 DoD: fake Twilio start(+44...) → outbound prompt audio → DTMF → selected.
    """
    _patch_fast_backends(monkeypatch)
    clear_last_language_selection()

    client = TestClient(app)
    with client.websocket_connect("/media-stream") as websocket:
        websocket.send_text(connected_message())
        websocket.send_text(start_message(from_number="+442071838750"))

        outbound = _collect_outbound_media(websocket, min_frames=1, overall_timeout_s=2.0)
        media_frames = [m for m in outbound if m.get("event") == "media"]
        assert media_frames, "expected TTS prompt media frames on the stream"
        assert all(m.get("streamSid") == STREAM_SID for m in media_frames)

        websocket.send_text(dtmf_message("1"))
        result = _wait_for_selection(timeout_s=2.0)
        websocket.send_text(stop_message())

    assert result is not None
    assert result.metrics.outcome == "selected"
    assert result.method == "dtmf"
    assert result.language == "en"  # GB menu: en first
    assert result.metrics.country_code == "GB"
    assert result.metrics.country_known is True
    assert result.metrics.dtmf_digit == "1"
    assert result.metrics.tts_calls >= 1


def test_media_stream_language_selection_via_speech(monkeypatch):
    _patch_fast_backends(monkeypatch)
    clear_last_language_selection()

    monkeypatch.setattr(
        "app.api.ivr.get_lid",
        lambda: FixedLanguageIdentifier(language="pl", confidence=0.99),
    )

    client = TestClient(app)
    with client.websocket_connect("/media-stream") as websocket:
        websocket.send_text(connected_message())
        websocket.send_text(start_message(from_number="+442071838750"))

        outbound = _collect_outbound_media(websocket, min_frames=1, overall_timeout_s=2.0)
        assert any(m.get("event") == "media" for m in outbound)

        tone = generate_tone_mulaw(duration_ms=20, amplitude=0.6)
        for _ in range(25):
            websocket.send_text(media_message(tone))
        for chunk in chunk_mulaw(generate_silence_mulaw(500), chunk_ms=20):
            websocket.send_text(media_message(chunk))

        result = _wait_for_selection(timeout_s=3.0)
        websocket.send_text(stop_message())

    assert result is not None
    assert result.metrics.outcome == "selected"
    assert result.method == "speech"
    assert result.language == "pl"
    assert result.metrics.country_code == "GB"
    assert result.metrics.tts_calls >= 1
