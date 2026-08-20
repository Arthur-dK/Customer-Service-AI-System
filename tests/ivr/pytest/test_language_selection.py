"""State-machine tests for language selection (fake TTS/LID/queues only)."""

from __future__ import annotations

import asyncio

import pytest

from services.ivr.audio import chunk_mulaw, generate_silence_mulaw, generate_tone_mulaw
from services.ivr.language_selection import LanguageSelector
from services.ivr.lid import FixedLanguageIdentifier
from services.ivr.tts import ToneTextToSpeech
from services.ivr.vad import VadConfig


async def _wait_for_outbound(outbound: asyncio.Queue[str], min_chunks: int = 1, timeout: float = 2.0) -> None:
    async def _poll():
        while outbound.qsize() < min_chunks:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


async def _clear_outbound(outbound: asyncio.Queue[str]) -> None:
    while not outbound.empty():
        outbound.get_nowait()


async def _feed_speech(inbound: asyncio.Queue[bytes], duration_ms: int = 300) -> None:
    tone = generate_tone_mulaw(duration_ms=20, amplitude=0.6)
    frames = max(1, duration_ms // 20)
    for _ in range(frames):
        await inbound.put(tone)
    for chunk in chunk_mulaw(generate_silence_mulaw(500), chunk_ms=20):
        await inbound.put(chunk)


def _selector(lid_language: str = "he", silence_timeout_s: float = 0.3) -> LanguageSelector:
    return LanguageSelector(
        tts=ToneTextToSpeech(ms_per_char=5, min_ms=40, max_ms=80),
        lid=FixedLanguageIdentifier(language=lid_language, confidence=0.9),
        silence_timeout_s=silence_timeout_s,
        min_lid_confidence=0.1,
        vad_config=VadConfig(rms_threshold=500, speech_start_ms=40, speech_end_ms=60),
        outbound_chunk_ms=20,
        playback_realtime=False,
        max_dtmf_rounds=3,
    )


@pytest.mark.asyncio
async def test_speech_selects_language_for_known_country():
    selector = _selector(lid_language="he")
    inbound: asyncio.Queue[bytes] = asyncio.Queue()
    outbound: asyncio.Queue[str] = asyncio.Queue()
    dtmf: asyncio.Queue[str] = asyncio.Queue()

    async def feed():
        await _wait_for_outbound(outbound)
        await asyncio.sleep(0.05)  # allow prompt drain
        await _feed_speech(inbound)

    feeder = asyncio.create_task(feed())
    result = await selector.run(
        phone_number="+972501234567",
        inbound_audio=inbound,
        outbound_audio=outbound,
        dtmf_digits=dtmf,
    )
    await feeder

    assert result is not None
    assert result.language == "he"
    assert result.method == "speech"
    assert result.metrics.country_known is True
    assert result.metrics.prompt_language == "he"
    assert result.metrics.lid_language == "he"
    assert result.metrics.tts_calls >= 1
    assert result.metrics.outcome == "selected"
    assert result.metrics.total_selection_ms is not None


@pytest.mark.asyncio
async def test_silence_then_dtmf_selects_language():
    selector = _selector(lid_language="en", silence_timeout_s=0.25)
    inbound: asyncio.Queue[bytes] = asyncio.Queue()
    outbound: asyncio.Queue[str] = asyncio.Queue()
    dtmf: asyncio.Queue[str] = asyncio.Queue()

    async def feed():
        await _wait_for_outbound(outbound)
        await _clear_outbound(outbound)
        await asyncio.sleep(0.35)
        await _wait_for_outbound(outbound)  # DTMF menu
        await dtmf.put("2")

    feeder = asyncio.create_task(feed())
    result = await selector.run(
        phone_number="+972501234567",
        inbound_audio=inbound,
        outbound_audio=outbound,
        dtmf_digits=dtmf,
    )
    await feeder

    assert result is not None
    assert result.method == "dtmf"
    assert result.language == "ar"  # IL: he, ar, en, ru
    assert result.metrics.dtmf_fallback_entered is True
    assert result.metrics.dtmf_digit == "2"
    assert result.metrics.silence_timeouts >= 1


@pytest.mark.asyncio
async def test_speech_barge_in_during_dtmf_fallback():
    selector = _selector(lid_language="en", silence_timeout_s=0.25)
    inbound: asyncio.Queue[bytes] = asyncio.Queue()
    outbound: asyncio.Queue[str] = asyncio.Queue()
    dtmf: asyncio.Queue[str] = asyncio.Queue()

    async def feed():
        await _wait_for_outbound(outbound)
        await _clear_outbound(outbound)
        await asyncio.sleep(0.35)
        await _wait_for_outbound(outbound)
        await _feed_speech(inbound)

    feeder = asyncio.create_task(feed())
    result = await selector.run(
        phone_number="+972501234567",
        inbound_audio=inbound,
        outbound_audio=outbound,
        dtmf_digits=dtmf,
    )
    await feeder

    assert result is not None
    assert result.method == "speech_barge_in"
    assert result.language == "en"
    assert result.metrics.barge_in_during_dtmf is True
    assert result.metrics.dtmf_fallback_entered is True


@pytest.mark.asyncio
async def test_unknown_country_reprompts_in_english_before_dtmf():
    selector = _selector(lid_language="fr", silence_timeout_s=0.25)
    inbound: asyncio.Queue[bytes] = asyncio.Queue()
    outbound: asyncio.Queue[str] = asyncio.Queue()
    dtmf: asyncio.Queue[str] = asyncio.Queue()

    async def feed():
        await _wait_for_outbound(outbound)
        await _clear_outbound(outbound)
        await asyncio.sleep(0.35)
        await _wait_for_outbound(outbound)
        await _clear_outbound(outbound)
        await asyncio.sleep(0.35)
        await _wait_for_outbound(outbound)
        await dtmf.put("1")

    feeder = asyncio.create_task(feed())
    result = await selector.run(
        phone_number=None,
        inbound_audio=inbound,
        outbound_audio=outbound,
        dtmf_digits=dtmf,
    )
    await feeder

    assert result is not None
    assert result.metrics.country_known is False
    assert result.metrics.english_reprompts == 2
    assert result.metrics.silence_timeouts >= 2
    assert result.method == "dtmf"
    assert result.language == "en"


@pytest.mark.asyncio
async def test_dtmf_during_initial_listen_selects_without_menu():
    selector = _selector(lid_language="he", silence_timeout_s=1.0)
    inbound: asyncio.Queue[bytes] = asyncio.Queue()
    outbound: asyncio.Queue[str] = asyncio.Queue()
    dtmf: asyncio.Queue[str] = asyncio.Queue()

    async def feed():
        await _wait_for_outbound(outbound)
        await asyncio.sleep(0.05)
        await dtmf.put("1")

    feeder = asyncio.create_task(feed())
    result = await selector.run(
        phone_number="+972501234567",
        inbound_audio=inbound,
        outbound_audio=outbound,
        dtmf_digits=dtmf,
    )
    await feeder

    assert result is not None
    assert result.method == "dtmf"
    assert result.language == "he"
    assert result.metrics.dtmf_fallback_entered is False
    assert result.metrics.dtmf_digit == "1"
