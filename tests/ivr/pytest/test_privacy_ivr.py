"""Privacy: no call recording files; logs omit transcripts, PINs, and full E.164."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from core.cards import last4_phone, log_contains_secret
from services.ivr.audio import generate_silence_mulaw, generate_tone_mulaw
from services.ivr.phrase_cache import PhraseAudioCache
from services.ivr.streaming_stt import ScriptedStreamingSpeechToText
from services.ivr.tts import ToneTextToSpeech
from services.ivr.ttfb import TtfbHarness
from services.ivr.turn_engine import PlaceholderTurnEngine

FULL_E164 = "+15555550100"
PLANTED_PIN = "9876"
FORBIDDEN_AUDIO = (".wav", ".mp3")


def _utterance_mulaw() -> bytes:
    return generate_tone_mulaw(400, amplitude=0.45) + generate_silence_mulaw(400)


def _audio_leaks(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in FORBIDDEN_AUDIO
    ]


def test_webhook_logs_last4_not_full_number(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    client = TestClient(app)
    response = client.post(
        "/voice/incoming",
        headers={"host": "localhost:8000"},
        data={"From": FULL_E164},
    )
    assert response.status_code == 200
    assert "<Record" not in response.text
    text = caplog.text
    assert last4_phone(FULL_E164) in text
    assert not log_contains_secret(text, full_e164=FULL_E164)


@pytest.mark.asyncio
async def test_simulated_turn_does_not_write_wav_mp3_or_log_secrets(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.INFO)
    tts = ToneTextToSpeech(ms_per_char=5, min_ms=40, max_ms=80)
    cache = PhraseAudioCache(tts, cache_dir=tmp_path / "phrases")
    await cache.warmup(languages=("en",))
    engine = PlaceholderTurnEngine(
        language="en",
        cache=cache,
        stt=ScriptedStreamingSpeechToText(finals=[f"PIN {PLANTED_PIN} please"]),
        ttfb=TtfbHarness(),
    )
    await engine.start()
    outbound: asyncio.Queue[str] = asyncio.Queue()
    result = await engine.handle_utterance(_utterance_mulaw(), outbound)

    assert result is not None
    assert result.transcript == f"PIN {PLANTED_PIN} please"
    assert _audio_leaks(tmp_path) == []
    text = caplog.text
    assert "placeholder_turn" in text
    assert "transcript=" not in text
    assert not log_contains_secret(text, full_e164=FULL_E164, pin=PLANTED_PIN)
