"""Simulated IVR turns: speech_end → stub transcript → canned phrase audio.

Language is already known. Replies are catalog buffers, not live card data or an LLM.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from core.language.phrases import GOODBYE, MAIN_MENU
from services.ivr.phrase_cache import PhraseAudioCache
from services.ivr.placeholder_intents import map_placeholder_intent
from services.ivr.streaming_stt import StreamingSpeechToText, Transcript, feed_until_speech_end
from services.ivr.streaming_tts import StreamingTextToSpeech, enqueue_tts_stream, stream_ready_phrase
from services.ivr.ttfb import ReplyKind, TtfbHarness
from services.ivr.vad import EnergyVad, VadConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    transcript: str
    phrase_id: str
    language: str
    chunks_sent: int
    ended: bool


class PlaceholderTurnEngine:
    """One listen→reply cycle after language selection (placeholder tasks only)."""

    def __init__(
        self,
        *,
        language: str,
        cache: PhraseAudioCache,
        stt: StreamingSpeechToText,
        ttfb: TtfbHarness | None = None,
        vad: EnergyVad | None = None,
        chunk_ms: int = 20,
        fallback_tts: StreamingTextToSpeech | None = None,
    ) -> None:
        self.language = language.lower()
        self.cache = cache
        self.stt = stt
        self.ttfb = ttfb if ttfb is not None else TtfbHarness()
        self.vad = vad or EnergyVad(VadConfig(rms_threshold=500, speech_start_ms=100, speech_end_ms=200))
        self.chunk_ms = chunk_ms
        self.fallback_tts = fallback_tts

    async def start(self) -> None:
        await self.stt.start(language=self.language)

    async def play_phrase(
        self,
        phrase_id: str,
        outbound: asyncio.Queue[str],
        *,
        measure_ttfb: bool = False,
        cancel: asyncio.Event | None = None,
    ) -> int:
        """Enqueue a warmed catalog line. TTFB is only for post-speech_end replies."""
        return await enqueue_tts_stream(
            stream_ready_phrase(
                self.cache,
                phrase_id,
                self.language,
                chunk_ms=self.chunk_ms,
                cancel=cancel,
                fallback=self.fallback_tts,
            ),
            outbound,
            self.ttfb if measure_ttfb else None,
            reply_kind=ReplyKind.CANNED,
            cancel=cancel,
        )

    async def handle_utterance(
        self,
        mulaw: bytes,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None = None,
    ) -> TurnResult | None:
        """VAD speech_end → STT finish → canned phrase. Returns None if no utterance."""
        self.vad.reset()
        transcript = await feed_until_speech_end(
            mulaw,
            stt=self.stt,
            vad=self.vad,
            ttfb=self.ttfb,
            chunk_ms=self.chunk_ms,
        )
        if transcript is None:
            return None
        return await self._reply(transcript, outbound, cancel=cancel)

    async def handle_inbound_queue(
        self,
        inbound_audio: asyncio.Queue[bytes],
        outbound: asyncio.Queue[str],
        stop_event: asyncio.Event,
        *,
        cancel: asyncio.Event | None = None,
    ) -> TurnResult | None:
        """Read live Media Stream frames until VAD speech_end, then canned reply."""
        self.vad.reset()
        while not stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(inbound_audio.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            await self.stt.feed_mulaw(chunk)
            for event in self.vad.process_mulaw(chunk):
                if event.kind == "speech_end":
                    self.ttfb.mark_speech_end()
                    transcript = await self.stt.finish()
                    if transcript is None:
                        return None
                    return await self._reply(transcript, outbound, cancel=cancel)
        return None

    async def run_on_queues(
        self,
        *,
        inbound_audio: asyncio.Queue[bytes],
        outbound_audio: asyncio.Queue[str],
        stop_event: asyncio.Event,
        play_menu: bool = True,
        max_turns: int = 8,
        on_turn: Callable[[list[TurnResult]], None] | None = None,
    ) -> list[TurnResult]:
        """Handoff after language selection: same inbound/outbound queues."""
        await self.start()
        if play_menu:
            await self.play_phrase(MAIN_MENU, outbound_audio, measure_ttfb=False)
        results: list[TurnResult] = []
        for _ in range(max_turns):
            if stop_event.is_set():
                break
            result = await self.handle_inbound_queue(
                inbound_audio, outbound_audio, stop_event
            )
            if result is None:
                break
            results.append(result)
            if on_turn is not None:
                on_turn(results)
            if result.ended:
                break
        return results

    async def run_scripted_session(
        self,
        utterances: list[bytes],
        outbound: asyncio.Queue[str],
        *,
        play_menu: bool = True,
    ) -> list[TurnResult]:
        """Optional main menu, then one turn per inbound utterance until goodbye."""
        await self.start()
        if play_menu:
            await self.play_phrase(MAIN_MENU, outbound, measure_ttfb=False)
        results: list[TurnResult] = []
        for mulaw in utterances:
            result = await self.handle_utterance(mulaw, outbound)
            if result is None:
                continue
            results.append(result)
            if result.ended:
                break
        return results

    async def _reply(
        self,
        transcript: Transcript,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None,
    ) -> TurnResult:
        phrase_id = map_placeholder_intent(transcript.text)
        sent = await self.play_phrase(
            phrase_id,
            outbound,
            measure_ttfb=True,
            cancel=cancel,
        )
        sample = self.ttfb.samples[-1] if self.ttfb.samples else None
        logger.info(
            "placeholder_turn language=%s phrase=%s chunks=%s ttfb_ms=%s within_budget=%s",
            self.language,
            phrase_id,
            sent,
            None if sample is None else round(sample.ttfb_ms, 1),
            None if sample is None else sample.within_budget,
        )
        return TurnResult(
            transcript=transcript.text,
            phrase_id=phrase_id,
            language=self.language,
            chunks_sent=sent,
            ended=phrase_id == GOODBYE,
        )
