"""Unit + contract checks for IVR TTS backends."""

from __future__ import annotations

import asyncio
import sys

import pytest

from services.ivr.audio import TWILIO_SAMPLE_RATE, mulaw_duration_ms, mulaw_to_pcm16, pcm16_rms
from services.ivr.tts import CachedTextToSpeech, ToneTextToSpeech, WindowsSapiTextToSpeech, build_default_tts


@pytest.mark.asyncio
async def test_tone_tts_returns_mulaw_with_energy():
    tts = ToneTextToSpeech(ms_per_char=10, min_ms=200, max_ms=1000)
    audio = await tts.synthesize("Hello language selection", "en")
    assert isinstance(audio, (bytes, bytearray))
    assert len(audio) >= int(TWILIO_SAMPLE_RATE * 0.2)
    assert pcm16_rms(mulaw_to_pcm16(audio)) > 500.0
    assert tts.supports_language("he") is True


@pytest.mark.asyncio
async def test_tone_tts_duration_scales_with_text():
    tts = ToneTextToSpeech(ms_per_char=20, min_ms=100, max_ms=5000)
    short = await tts.synthesize("Hi", "en")
    long = await tts.synthesize("This is a much longer prompt for duration checks", "en")
    assert mulaw_duration_ms(long) > mulaw_duration_ms(short)


@pytest.mark.asyncio
async def test_cached_tts_avoids_second_inner_call(tmp_path):
    calls = {"n": 0}

    class CountingTone(ToneTextToSpeech):
        async def synthesize(self, text: str, language: str) -> bytes:
            calls["n"] += 1
            return await super().synthesize(text, language)

    tts = CachedTextToSpeech(CountingTone(ms_per_char=5, min_ms=50, max_ms=200), cache_dir=tmp_path)
    first = await tts.synthesize("Cache me", "en")
    second = await tts.synthesize("Cache me", "en")
    assert first == second
    assert calls["n"] == 1


@pytest.mark.asyncio
@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows SAPI only")
async def test_windows_sapi_speaks_english_mulaw():
    tts = WindowsSapiTextToSpeech()
    assert tts.supports_language("en") is True
    assert tts.supports_language("he") is False
    audio = await tts.synthesize("Please say the language you would like to use.", "en")
    assert len(audio) > TWILIO_SAMPLE_RATE  # > 1 second
    assert pcm16_rms(mulaw_to_pcm16(audio)) > 500.0


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows SAPI only")
def test_windows_sapi_works_under_selector_event_loop():
    """Regression: uvicorn on Windows often uses a selector loop without subprocess support."""
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def _run():
        tts = WindowsSapiTextToSpeech()
        return await tts.synthesize("Hello from selector loop.", "en")

    audio = asyncio.run(_run())
    assert len(audio) > 1000
    assert pcm16_rms(mulaw_to_pcm16(audio)) > 500.0


def test_build_default_tts_on_windows_prefers_sapi():
    if not sys.platform.startswith("win"):
        pytest.skip("Windows only")
    tts = build_default_tts(cache=False)
    assert isinstance(tts, WindowsSapiTextToSpeech)
    wrapped = build_default_tts(cache=True)
    assert isinstance(wrapped, CachedTextToSpeech)
    assert isinstance(wrapped.inner, WindowsSapiTextToSpeech)
