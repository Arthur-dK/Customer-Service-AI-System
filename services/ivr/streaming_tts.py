"""Streaming TTS: μ-law chunks, first chunk stops the TTFB clock.

Paid cloud engines (Cartesia, etc.) should implement ``StreamingTextToSpeech``.
Until then, wrap any batch ``TextToSpeech`` (tone / Piper / SAPI) and chunk the
finished buffer. Canned phrases stream from ``PhraseAudioCache.get_ready``.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Protocol

from services.ivr.audio import chunk_mulaw
from services.ivr.phrase_cache import PhraseAudioCache, PhraseNotReadyError
from services.ivr.tts import TextToSpeech, ToneTextToSpeech
from services.ivr.ttfb import ReplyKind, TtfbHarness

DEFAULT_CHUNK_MS = 20


class StreamingTextToSpeech(Protocol):
    def supports_language(self, language: str) -> bool:
        """Return True when this backend can speak the language intelligibly."""
        ...

    def stream(
        self,
        text: str,
        language: str,
        *,
        chunk_ms: int = DEFAULT_CHUNK_MS,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield 8 kHz μ-law frames. First yield is TTFB-stop for generated speech."""
        ...


class ToneStreamingTextToSpeech:
    """Free CI/demo stub: tone buffer split into frames (no paid API)."""

    def __init__(self, inner: ToneTextToSpeech | None = None) -> None:
        self.inner = inner or ToneTextToSpeech()

    def supports_language(self, language: str) -> bool:
        return self.inner.supports_language(language)

    async def stream(
        self,
        text: str,
        language: str,
        *,
        chunk_ms: int = DEFAULT_CHUNK_MS,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        mulaw = await self.inner.synthesize(text, language)
        async for chunk in _yield_chunks(mulaw, chunk_ms=chunk_ms, cancel=cancel):
            yield chunk


class BatchStreamingTextToSpeech:
    """Adapter: any batch ``TextToSpeech`` (Piper, SAPI, cached) → chunk stream.

    First chunk waits until the full utterance is synthesized. Swap this out for
    a native streaming vendor without changing callers.
    """

    def __init__(self, inner: TextToSpeech) -> None:
        self.inner = inner

    def supports_language(self, language: str) -> bool:
        return self.inner.supports_language(language)

    async def stream(
        self,
        text: str,
        language: str,
        *,
        chunk_ms: int = DEFAULT_CHUNK_MS,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        mulaw = await self.inner.synthesize(text, language)
        async for chunk in _yield_chunks(mulaw, chunk_ms=chunk_ms, cancel=cancel):
            yield chunk


def build_default_streaming_tts(batch: TextToSpeech | None = None) -> StreamingTextToSpeech:
    """Tone stub when no batch engine is passed; otherwise wrap that engine."""
    if batch is None:
        return ToneStreamingTextToSpeech()
    return BatchStreamingTextToSpeech(batch)


async def stream_ready_phrase(
    cache: PhraseAudioCache,
    phrase_id: str,
    language: str,
    *,
    chunk_ms: int = DEFAULT_CHUNK_MS,
    cancel: asyncio.Event | None = None,
    fallback: StreamingTextToSpeech | None = None,
) -> AsyncIterator[bytes]:
    """Stream a warmed catalog phrase. Optional fallback synthesizes if not ready."""
    play_lang = cache.catalog.resolve_language(phrase_id, language)
    try:
        mulaw = cache.get_ready(phrase_id, play_lang)
    except PhraseNotReadyError:
        if fallback is None:
            raise
        async for chunk in fallback.stream(
            cache.catalog.text(phrase_id, play_lang, strict=True),
            play_lang,
            chunk_ms=chunk_ms,
            cancel=cancel,
        ):
            yield chunk
        return
    async for chunk in _yield_chunks(mulaw, chunk_ms=chunk_ms, cancel=cancel):
        yield chunk


async def enqueue_tts_stream(
    chunks: AsyncIterator[bytes],
    outbound: asyncio.Queue[str],
    ttfb: TtfbHarness | None = None,
    *,
    reply_kind: ReplyKind = ReplyKind.CANNED,
    cancel: asyncio.Event | None = None,
) -> int:
    """Put base64 μ-law frames on the outbound queue. First frame stops TTFB."""
    sent = 0
    async for chunk in chunks:
        if cancel is not None and cancel.is_set():
            break
        if not chunk:
            continue
        if sent == 0 and ttfb is not None:
            ttfb.mark_first_audio_byte(reply_kind=reply_kind)
        await outbound.put(base64.b64encode(chunk).decode("ascii"))
        sent += 1
    return sent


async def _yield_chunks(
    mulaw: bytes,
    *,
    chunk_ms: int,
    cancel: asyncio.Event | None,
) -> AsyncIterator[bytes]:
    for chunk in chunk_mulaw(mulaw, chunk_ms=chunk_ms):
        if cancel is not None and cancel.is_set():
            return
        yield chunk
        await asyncio.sleep(0)
