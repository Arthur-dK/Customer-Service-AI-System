"""Local faster-whisper STT on the streaming protocol (selected language only)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from services.ivr.audio import TWILIO_SAMPLE_RATE, mulaw_to_pcm16, resample_pcm16
from services.ivr.streaming_stt import Transcript
from services.ivr.tts_lang import normalize_language

logger = logging.getLogger(__name__)

WHISPER_SAMPLE_RATE = 16000
_model_lock = threading.Lock()
_shared_model: Any = None
_shared_model_size: str | None = None

TranscribeFn = Callable[[bytes, str], str]


def _pcm16_to_float32(pcm16: bytes) -> Any:
    import numpy as np

    return np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0


def _load_whisper_model(model_size: str) -> Any:
    global _shared_model, _shared_model_size
    with _model_lock:
        if _shared_model is not None and _shared_model_size == model_size:
            return _shared_model
        from faster_whisper import WhisperModel

        _shared_model = WhisperModel(model_size, device="cpu", compute_type="int8")
        _shared_model_size = model_size
        return _shared_model


class WhisperStreamingSpeechToText:
    """Buffer μ-law in memory; transcribe on ``finish`` using the call language."""

    def __init__(
        self,
        *,
        model_size: str = "base",
        transcribe_fn: TranscribeFn | None = None,
    ) -> None:
        self.model_size = model_size
        self._transcribe_fn = transcribe_fn
        self._language = "en"
        self._buf = bytearray()

    def warm(self) -> None:
        """Load faster-whisper once. No-op when a test ``transcribe_fn`` is injected."""
        if self._transcribe_fn is not None:
            return
        _load_whisper_model(self.model_size)

    async def start(self, *, language: str) -> None:
        self._language = normalize_language(language) or "en"
        self._buf.clear()

    async def feed_mulaw(self, chunk: bytes) -> list[Transcript]:
        if chunk:
            self._buf.extend(chunk)
        return []

    async def finish(self) -> Transcript | None:
        mulaw = bytes(self._buf)
        self._buf.clear()
        if not mulaw:
            return Transcript(text="", is_final=True, language=self._language)
        started = time.perf_counter()
        text = await asyncio.to_thread(self._recognize, mulaw, self._language)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "whisper_stt language=%s bytes=%s stt_ms=%s",
            self._language,
            len(mulaw),
            round(elapsed_ms, 1),
        )
        return Transcript(text=text, is_final=True, language=self._language)

    async def aclose(self) -> None:
        self._buf.clear()

    def _recognize(self, mulaw: bytes, language: str) -> str:
        if self._transcribe_fn is not None:
            return self._transcribe_fn(mulaw, language)
        try:
            model = _load_whisper_model(self.model_size)
        except ModuleNotFoundError:
            logger.warning(
                "faster_whisper is not installed; STT returned empty. "
                "Rebuild with requirements-ivr-intent.txt / requirements-render.txt."
            )
            return ""
        pcm16 = resample_pcm16(mulaw_to_pcm16(mulaw), TWILIO_SAMPLE_RATE, WHISPER_SAMPLE_RATE)
        audio = _pcm16_to_float32(pcm16)
        segments, _info = model.transcribe(
            audio,
            language=language,
            vad_filter=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
