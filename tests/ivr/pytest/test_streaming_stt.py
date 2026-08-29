"""Streaming STT stub: known transcript after VAD speech_end (no network)."""

from __future__ import annotations

import pytest

from services.ivr.audio import generate_silence_mulaw, generate_tone_mulaw
from services.ivr.streaming_stt import (
    ScriptedStreamingSpeechToText,
    Transcript,
    build_default_streaming_stt,
    feed_until_speech_end,
)
from services.ivr.ttfb import TtfbHarness
from services.ivr.vad import EnergyVad, VadConfig


def _utterance_mulaw() -> bytes:
    return generate_tone_mulaw(400, amplitude=0.45) + generate_silence_mulaw(400)


class RecordingStt:
    """Constructor-swap stand-in for a vendor engine."""

    def __init__(self) -> None:
        self.started_language: str | None = None
        self.bytes_fed = 0
        self.finish_calls = 0

    async def start(self, *, language: str) -> None:
        self.started_language = language

    async def feed_mulaw(self, chunk: bytes) -> list[Transcript]:
        self.bytes_fed += len(chunk)
        return []

    async def finish(self) -> Transcript | None:
        self.finish_calls += 1
        return Transcript(text="balance", is_final=True, language=self.started_language)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_scripted_stub_returns_queued_final_on_finish():
    stt = ScriptedStreamingSpeechToText(finals=["balance", "goodbye"])
    await stt.start(language="en")
    interims = await stt.feed_mulaw(b"\xff" * 160)
    assert interims == []
    first = await stt.finish()
    second = await stt.finish()
    assert first == Transcript(text="balance", is_final=True, language="en")
    assert second == Transcript(text="goodbye", is_final=True, language="en")
    empty = await stt.finish()
    assert empty is not None
    assert empty.text == ""


@pytest.mark.asyncio
async def test_start_sets_language_without_re_detecting():
    stt = ScriptedStreamingSpeechToText(finals=["solde"])
    await stt.start(language="FR")
    result = await stt.finish()
    assert result is not None
    assert result.language == "fr"
    assert result.text == "solde"


@pytest.mark.asyncio
async def test_feed_until_speech_end_returns_stub_transcript_and_starts_ttfb():
    vad = EnergyVad(VadConfig(rms_threshold=500, speech_start_ms=100, speech_end_ms=200))
    stt = ScriptedStreamingSpeechToText(finals=["PIN"])
    await stt.start(language="en")
    harness = TtfbHarness()

    result = await feed_until_speech_end(_utterance_mulaw(), stt=stt, vad=vad, ttfb=harness)

    assert result is not None
    assert result.text == "PIN"
    assert result.is_final is True
    assert harness.turn_open is True
    assert stt.bytes_fed > 0


@pytest.mark.asyncio
async def test_feed_until_speech_end_does_not_finish_without_utterance():
    vad = EnergyVad(VadConfig(rms_threshold=500, speech_start_ms=100, speech_end_ms=200))
    stt = ScriptedStreamingSpeechToText(finals=["balance"])
    await stt.start(language="en")
    silence = generate_silence_mulaw(300)
    result = await feed_until_speech_end(silence, stt=stt, vad=vad)
    assert result is None
    leftover = await stt.finish()
    assert leftover is not None
    assert leftover.text == "balance"


def test_build_default_streaming_stt_is_scripted_stub():
    stt = build_default_streaming_stt(finals=["block card"])
    assert isinstance(stt, ScriptedStreamingSpeechToText)


@pytest.mark.asyncio
async def test_vendor_shaped_backend_swaps_by_constructor():
    vad = EnergyVad(VadConfig(rms_threshold=500, speech_start_ms=100, speech_end_ms=200))
    stt = RecordingStt()
    await stt.start(language="en")
    result = await feed_until_speech_end(_utterance_mulaw(), stt=stt, vad=vad)
    assert result is not None
    assert result.text == "balance"
    assert stt.bytes_fed > 0
    assert stt.finish_calls == 1
