"""Local faster-whisper STT: injected recognizer, selected language, no disk audio."""

from __future__ import annotations

import logging
import sys

import pytest

from services.ivr.audio import generate_silence_mulaw, generate_tone_mulaw
from services.ivr.streaming_stt import build_default_streaming_stt
from services.ivr.whisper_stt import WhisperStreamingSpeechToText


def _utterance_mulaw() -> bytes:
    return generate_tone_mulaw(400, amplitude=0.45) + generate_silence_mulaw(200)


@pytest.mark.asyncio
async def test_uses_start_language_not_a_new_lid():
    seen: list[tuple[int, str]] = []

    def fake(mulaw: bytes, language: str) -> str:
        seen.append((len(mulaw), language))
        return "how much is left on the card"

    stt = WhisperStreamingSpeechToText(transcribe_fn=fake)
    await stt.start(language="HE")
    await stt.feed_mulaw(_utterance_mulaw())
    result = await stt.finish()
    assert result is not None
    assert result.language == "he"
    assert result.text == "how much is left on the card"
    assert seen == [(len(_utterance_mulaw()), "he")]
    assert len(stt._buf) == 0


@pytest.mark.asyncio
async def test_empty_utterance_skips_recognizer():
    def fake(_mulaw: bytes, _language: str) -> str:
        raise AssertionError("recognizer must not run on empty buffer")

    stt = WhisperStreamingSpeechToText(transcribe_fn=fake)
    await stt.start(language="ar")
    result = await stt.finish()
    assert result is not None
    assert result.text == ""
    assert result.language == "ar"


@pytest.mark.asyncio
async def test_does_not_import_faster_whisper_when_injected():
    sys.modules.pop("faster_whisper", None)
    stt = WhisperStreamingSpeechToText(transcribe_fn=lambda _m, _l: "balance")
    await stt.start(language="en")
    await stt.feed_mulaw(b"\xff" * 160)
    await stt.finish()
    assert "faster_whisper" not in sys.modules


@pytest.mark.asyncio
async def test_logs_omit_transcript(caplog: pytest.LogCaptureFixture):
    secret = "PIN-7744-DO-NOT-LOG"
    caplog.set_level(logging.INFO)
    stt = WhisperStreamingSpeechToText(transcribe_fn=lambda _m, _l: secret)
    await stt.start(language="en")
    await stt.feed_mulaw(_utterance_mulaw())
    await stt.finish()
    assert secret not in caplog.text
    assert "whisper_stt" in caplog.text


def test_build_default_whisper_backend():
    stt = build_default_streaming_stt(backend="whisper")
    assert isinstance(stt, WhisperStreamingSpeechToText)


@pytest.mark.asyncio
async def test_missing_faster_whisper_returns_empty_not_crash(monkeypatch):
    def boom(_size: str):
        raise ModuleNotFoundError("faster_whisper")

    monkeypatch.setattr("services.ivr.whisper_stt._load_whisper_model", boom)
    stt = WhisperStreamingSpeechToText()
    await stt.start(language="en")
    await stt.feed_mulaw(_utterance_mulaw())
    result = await stt.finish()
    assert result is not None
    assert result.text == ""
    assert result.is_final is True


@pytest.mark.slow
@pytest.mark.asyncio
async def test_optional_real_faster_whisper_on_tone():
    pytest.importorskip("faster_whisper")
    stt = WhisperStreamingSpeechToText(model_size="tiny")
    await stt.start(language="en")
    await stt.feed_mulaw(_utterance_mulaw())
    result = await stt.finish()
    assert result is not None
    assert result.is_final is True
    assert result.language == "en"
