"""Streaming TTS stub, batch adapter, phrase streaming, and TTFB-on-first-chunk."""

from __future__ import annotations

import asyncio
import base64

import pytest

from core.language.phrases import GOODBYE, MAIN_MENU
from services.ivr.audio import chunk_mulaw
from services.ivr.phrase_cache import PhraseAudioCache
from services.ivr.streaming_tts import (
    BatchStreamingTextToSpeech,
    ToneStreamingTextToSpeech,
    build_default_streaming_tts,
    enqueue_tts_stream,
    stream_ready_phrase,
)
from services.ivr.tts import ToneTextToSpeech
from services.ivr.ttfb import TtfbHarness


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class CountingTone(ToneTextToSpeech):
    def __init__(self) -> None:
        super().__init__(ms_per_char=5, min_ms=80, max_ms=400)
        self.calls = 0

    async def synthesize(self, text: str, language: str) -> bytes:
        self.calls += 1
        return await super().synthesize(text, language)


class ImmediateFirstChunkTts:
    """Native-style stream: first frame is available before later frames."""

    def supports_language(self, language: str) -> bool:
        return True

    async def stream(
        self,
        text: str,
        language: str,
        *,
        chunk_ms: int = 20,
        cancel: asyncio.Event | None = None,
    ):
        frame = b"\xff" * 160
        yield frame
        await asyncio.sleep(0.15)
        if cancel is not None and cancel.is_set():
            return
        yield frame


@pytest.mark.asyncio
async def test_tone_stub_yields_chunks_that_rebuild_full_utterance():
    inner = ToneTextToSpeech(ms_per_char=5, min_ms=80, max_ms=400)
    tts = ToneStreamingTextToSpeech(inner)
    text, language = "hello streaming", "en"
    full = await inner.synthesize(text, language)
    chunks = [chunk async for chunk in tts.stream(text, language, chunk_ms=20)]
    assert len(chunks) > 1
    assert b"".join(chunks) == full
    assert chunks == chunk_mulaw(full, chunk_ms=20)


@pytest.mark.asyncio
async def test_batch_adapter_matches_wrapped_engine():
    inner = ToneTextToSpeech(ms_per_char=5, min_ms=80, max_ms=400)
    wrapped = BatchStreamingTextToSpeech(inner)
    full = await inner.synthesize("adapter", "fr")
    chunks = [chunk async for chunk in wrapped.stream("adapter", "fr")]
    assert b"".join(chunks) == full


def test_build_default_streaming_tts_swaps_by_constructor():
    stub = build_default_streaming_tts()
    assert isinstance(stub, ToneStreamingTextToSpeech)
    batch = ToneTextToSpeech()
    wrapped = build_default_streaming_tts(batch)
    assert isinstance(wrapped, BatchStreamingTextToSpeech)
    assert wrapped.inner is batch


@pytest.mark.asyncio
async def test_first_chunk_records_ttfb_on_fake_clock():
    clock = FakeClock()
    harness = TtfbHarness(clock=clock)
    outbound: asyncio.Queue[str] = asyncio.Queue()
    tts = ToneStreamingTextToSpeech(ToneTextToSpeech(ms_per_char=5, min_ms=80, max_ms=200))

    harness.mark_speech_end()
    clock.advance(0.09)
    sent = await enqueue_tts_stream(tts.stream("hi", "en"), outbound, harness)

    assert sent >= 1
    assert harness.samples[0].ttfb_ms == pytest.approx(90.0)
    assert harness.samples[0].within_budget is True
    first = await outbound.get()
    assert base64.b64decode(first)


@pytest.mark.asyncio
async def test_native_stream_ttfb_does_not_wait_for_later_chunks():
    harness = TtfbHarness()
    outbound: asyncio.Queue[str] = asyncio.Queue()
    harness.mark_speech_end()
    sent = await enqueue_tts_stream(ImmediateFirstChunkTts().stream("x", "en"), outbound, harness)
    assert sent == 2
    assert harness.samples[0].ttfb_ms < 100.0
    assert harness.samples[0].within_budget is True


@pytest.mark.asyncio
async def test_cancel_stops_enqueue_after_open_turn():
    cancel = asyncio.Event()

    async def chunks():
        yield b"\xff" * 160
        cancel.set()
        yield b"\x00" * 160
        yield b"\x00" * 160

    outbound: asyncio.Queue[str] = asyncio.Queue()
    sent = await enqueue_tts_stream(chunks(), outbound, cancel=cancel)
    assert sent == 1
    assert outbound.qsize() == 1


@pytest.mark.asyncio
async def test_ready_phrase_streams_without_tts(tmp_path):
    inner = CountingTone()
    cache = PhraseAudioCache(inner, cache_dir=tmp_path)
    await cache.warmup(languages=("en",))
    calls_after_warm = inner.calls

    chunks = [
        chunk
        async for chunk in stream_ready_phrase(cache, MAIN_MENU, "en", chunk_ms=20)
    ]
    assert b"".join(chunks) == cache.get_ready(MAIN_MENU, "en")
    assert len(chunks) > 1
    assert inner.calls == calls_after_warm

    goodbye = [chunk async for chunk in stream_ready_phrase(cache, GOODBYE, "en")]
    assert goodbye
    assert inner.calls == calls_after_warm
