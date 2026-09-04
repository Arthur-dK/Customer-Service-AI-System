"""Fake Twilio stream: language selection then intent turns (allowlist vs hangup)."""

from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from app.main import app
from core.language.phrases import GET_BALANCE, UNKNOWN_CALLER
from services.ivr.audio import chunk_mulaw, generate_silence_mulaw, generate_tone_mulaw
from services.ivr.lid import FixedLanguageIdentifier
from services.ivr.phrase_cache import PhraseAudioCache
from services.ivr.selection_store import clear_last_language_selection, get_last_language_selection
from services.ivr.streaming_stt import ScriptedStreamingSpeechToText
from services.ivr.streaming_tts import build_default_streaming_tts
from services.ivr.tts import ToneTextToSpeech
from services.ivr.turn_store import clear_last_turns, get_last_turns
from tests.ivr.manual.fake_twilio_stream import (
    STREAM_SID,
    connected_message,
    dtmf_message,
    media_message,
    start_message,
    stop_message,
)
from tests.ivr.pytest.test_media_stream_language import _collect_outbound_media, _wait_for_selection

ALLOWLISTED = "+15555550100"
UNKNOWN = "+15555550999"


def _wait_for_turns(timeout_s: float = 3.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        turns = get_last_turns()
        if turns:
            return turns
        time.sleep(0.05)
    return get_last_turns()


def _send_utterance(websocket) -> None:
    tone = generate_tone_mulaw(duration_ms=20, amplitude=0.6)
    for _ in range(25):
        websocket.send_text(media_message(tone))
    for chunk in chunk_mulaw(generate_silence_mulaw(500), chunk_ms=20):
        websocket.send_text(media_message(chunk))


def _patch_stream(monkeypatch, tmp_path, *, finals: list[str]) -> None:
    tts = ToneTextToSpeech(ms_per_char=5, min_ms=40, max_ms=80)
    cache = PhraseAudioCache(tts, cache_dir=tmp_path / "phrases")
    asyncio.run(cache.warmup(languages=("en",)))
    lid = FixedLanguageIdentifier(language="en", confidence=0.99)
    stt = ScriptedStreamingSpeechToText(finals=finals)
    monkeypatch.setattr("app.api.ivr.get_tts", lambda: tts)
    monkeypatch.setattr("app.api.ivr.get_lid", lambda: lid)
    monkeypatch.setattr("app.api.ivr.get_phrase_cache", lambda: cache)
    monkeypatch.setattr("app.api.ivr.get_streaming_stt", lambda: stt)
    monkeypatch.setattr("app.api.ivr.get_streaming_tts", lambda: build_default_streaming_tts(tts))
    monkeypatch.setattr("app.api.ivr.settings.IVR_SILENCE_TIMEOUT_S", 0.3)
    monkeypatch.setattr("app.api.ivr.settings.IVR_PLAYBACK_REALTIME", False)
    monkeypatch.setattr("app.api.ivr.settings.IVR_MIN_LID_CONFIDENCE", 0.1)


def test_media_stream_allowlisted_get_balance(tmp_path, monkeypatch, isolated_caller_store):
    _patch_stream(monkeypatch, tmp_path, finals=["what is my balance"])
    assert isolated_caller_store.lookup(ALLOWLISTED) is not None
    clear_last_language_selection()
    clear_last_turns()

    client = TestClient(app)
    with client.websocket_connect("/media-stream") as websocket:
        websocket.send_text(connected_message())
        websocket.send_text(start_message(from_number=ALLOWLISTED))

        prompt = _collect_outbound_media(websocket, min_frames=1, overall_timeout_s=2.0)
        assert any(m.get("event") == "media" for m in prompt)

        websocket.send_text(dtmf_message("1"))
        selected = _wait_for_selection(timeout_s=2.0)
        assert selected is not None
        assert selected.language == "en"

        menu = _collect_outbound_media(websocket, min_frames=1, overall_timeout_s=2.0)
        assert any(m.get("event") == "media" for m in menu)
        assert all(
            m.get("streamSid") == STREAM_SID for m in menu if m.get("event") == "media"
        )

        _send_utterance(websocket)
        turns = _wait_for_turns(timeout_s=3.0)
        reply = _collect_outbound_media(websocket, min_frames=1, overall_timeout_s=2.0)
        websocket.send_text(stop_message())

    assert turns
    assert turns[0].phrase_id == GET_BALANCE
    assert getattr(turns[0], "hung_up", False) is False
    assert turns[0].language == "en"
    assert turns[0].chunks_sent >= 1
    assert any(m.get("event") == "media" for m in reply)


def test_media_stream_unknown_caller_hangs_up(tmp_path, monkeypatch, isolated_caller_store):
    _patch_stream(monkeypatch, tmp_path, finals=["what is my balance"])
    assert isolated_caller_store.lookup(UNKNOWN) is None
    clear_last_language_selection()
    clear_last_turns()

    client = TestClient(app)
    with client.websocket_connect("/media-stream") as websocket:
        websocket.send_text(connected_message())
        websocket.send_text(start_message(from_number=UNKNOWN))

        prompt = _collect_outbound_media(websocket, min_frames=1, overall_timeout_s=2.0)
        assert any(m.get("event") == "media" for m in prompt)

        websocket.send_text(dtmf_message("1"))
        selected = _wait_for_selection(timeout_s=2.0)
        assert selected is not None

        refusal = _collect_outbound_media(websocket, min_frames=1, overall_timeout_s=2.0)
        turns = _wait_for_turns(timeout_s=3.0)
        websocket.send_text(stop_message())

    assert turns
    assert len(turns) == 1
    assert turns[0].phrase_id == UNKNOWN_CALLER
    assert turns[0].hung_up is True
    assert turns[0].ended is True
    assert any(m.get("event") == "media" for m in refusal)
