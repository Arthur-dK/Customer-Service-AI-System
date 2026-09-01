"""Streaming STT: inbound μ-law in, transcripts out after utterance end.

Energy VAD (ADR-004) still decides when the caller stopped talking. This module
only transcribes. Paid engines (Deepgram, etc.) and local models (Whisper-class)
should implement ``StreamingSpeechToText``. CI uses a scripted stub that does
not inspect audio and does not use the network.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Protocol

from services.ivr.audio import chunk_mulaw
from services.ivr.ttfb import TtfbHarness
from services.ivr.vad import EnergyVad, VadEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool
    language: str | None = None


class StreamingSpeechToText(Protocol):
    async def start(self, *, language: str) -> None:
        """Begin a call/session. Language is already known (LID once per call)."""
        ...

    async def feed_mulaw(self, chunk: bytes) -> list[Transcript]:
        """Push one inbound 8 kHz μ-law frame. May return interim transcripts."""
        ...

    async def finish(self) -> Transcript | None:
        """Utterance complete (VAD ``speech_end``). Return the final transcript."""
        ...

    async def aclose(self) -> None:
        """Release vendor sessions. Stubs are no-ops."""
        ...


class ScriptedStreamingSpeechToText:
    """Free CI/demo stub: ignores audio; emits queued finals on ``finish``."""

    def __init__(self, finals: list[str] | None = None) -> None:
        self._script = list(finals if finals is not None else ())
        self._queued = list(self._script)
        self._language = "en"
        self._bytes_fed = 0

    @property
    def bytes_fed(self) -> int:
        return self._bytes_fed

    def supports_language(self, language: str) -> bool:
        return True

    async def start(self, *, language: str) -> None:
        self._language = language.lower()
        self._bytes_fed = 0
        self._queued = list(self._script)

    async def feed_mulaw(self, chunk: bytes) -> list[Transcript]:
        self._bytes_fed += len(chunk)
        return []

    async def finish(self) -> Transcript | None:
        if not self._queued:
            return Transcript(text="", is_final=True, language=self._language)
        text = self._queued.pop(0)
        return Transcript(text=text, is_final=True, language=self._language)

    async def aclose(self) -> None:
        self._queued.clear()
        # Keep ``_script`` so a later ``start()`` can refill the demo queue.


def parse_stt_script(raw: str | None) -> list[str]:
    """Split ``IVR_STT_SCRIPT`` into queued finals (no network)."""
    if not raw or not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def build_default_streaming_stt(
    finals: list[str] | None = None,
    *,
    backend: str | None = None,
) -> StreamingSpeechToText:
    """``sapi`` uses Windows grammar STT. Linux/Render always uses the scripted stub."""
    script = list(finals if finals is not None else ())
    kind = (backend or "scripted").strip().lower()
    if kind == "sapi" and not sys.platform.startswith("win"):
        logger.warning(
            "IVR_STT_BACKEND=sapi is Windows-only; using scripted STT on this host"
        )
        kind = "scripted"
    if kind == "sapi":
        from services.ivr.sapi_stt import GrammarStreamingSpeechToText

        return GrammarStreamingSpeechToText()
    return ScriptedStreamingSpeechToText(finals=script)


async def feed_until_speech_end(
    mulaw: bytes,
    *,
    stt: StreamingSpeechToText,
    vad: EnergyVad,
    ttfb: TtfbHarness | None = None,
    chunk_ms: int = 20,
) -> Transcript | None:
    """Feed a μ-law buffer through VAD + STT. On ``speech_end``, start TTFB and finish STT."""
    for chunk in chunk_mulaw(mulaw, chunk_ms=chunk_ms):
        await stt.feed_mulaw(chunk)
        for event in vad.process_mulaw(chunk):
            if _is_speech_end(event):
                if ttfb is not None:
                    ttfb.mark_speech_end()
                return await stt.finish()
    return None


def _is_speech_end(event: VadEvent) -> bool:
    return event.kind == "speech_end"
