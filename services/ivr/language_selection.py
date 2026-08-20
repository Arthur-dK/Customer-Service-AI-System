"""Stream-native IVR language selection state machine."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from core.language import (
    CallerLocale,
    build_dtmf_menu_prompt,
    language_from_dtmf_digit,
    language_selection_prompt,
    resolve_caller_locale,
)
from services.ivr.audio import TWILIO_SAMPLE_RATE, chunk_mulaw, mulaw_to_pcm16, pcm16_rms
from services.ivr.lid import LanguageIdentifier
from services.ivr.metrics import LanguageSelectionMetrics
from services.ivr.tts import TextToSpeech, ToneTextToSpeech
from services.ivr.vad import EnergyVad, VadConfig

logger = logging.getLogger(__name__)

# Outbound queue sentinel: media-stream sender emits Twilio "clear" (flush buffer).
CLEAR_AUDIO_SENTINEL = "__TWILIO_CLEAR__"

# #region agent log
_DEBUG_LOG_PATH = (
    Path(__file__).resolve().parents[2] / "debug-6ce0b3.log"
)


def _agent_dbg(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
    run_id: str = "pre-fix",
) -> None:
    import json

    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "sessionId": "6ce0b3",
                        "runId": run_id,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    default=str,
                )
                + "\n"
            )
    except Exception:
        pass


# #endregion


class SelectionPhase(str, Enum):
    PROMPT = "prompt"
    LISTEN = "listen"
    DTMF_MENU = "dtmf_menu"
    DONE = "done"


@dataclass(frozen=True)
class LanguageSelectionResult:
    language: str
    method: str
    metrics: LanguageSelectionMetrics
    locale: CallerLocale


class LanguageSelector:
    """
    Run language selection entirely on media-stream queues (no Twilio TwiML Gather).

    Known country: prompt in top language → listen → speech LID or DTMF.
    Unknown country: English → 5s → English again → DTMF.
    During DTMF menu playback/listen, speech barge-in selects immediately via LID.
    """

    def __init__(
        self,
        tts: TextToSpeech,
        lid: LanguageIdentifier,
        *,
        silence_timeout_s: float = 5.0,
        min_lid_confidence: float = 0.15,
        vad_config: VadConfig | None = None,
        outbound_chunk_ms: int = 20,
        playback_realtime: bool = True,
        max_dtmf_rounds: int = 3,
    ) -> None:
        self.tts = tts
        self.lid = lid
        self.silence_timeout_s = silence_timeout_s
        self.min_lid_confidence = min_lid_confidence
        self.vad = EnergyVad(vad_config or VadConfig())
        self.outbound_chunk_ms = outbound_chunk_ms
        self.playback_realtime = playback_realtime
        self.max_dtmf_rounds = max_dtmf_rounds

    async def run(
        self,
        *,
        phone_number: str | None,
        inbound_audio: asyncio.Queue[bytes],
        outbound_audio: asyncio.Queue[str],
        dtmf_digits: asyncio.Queue[str],
        stop_event: asyncio.Event | None = None,
    ) -> LanguageSelectionResult | None:
        locale = resolve_caller_locale(phone_number)
        metrics = LanguageSelectionMetrics(
            country_code=locale.country_code,
            country_known=locale.country_known,
            prompt_language=locale.prompt_language,
            menu_languages=list(locale.languages),
        )
        started = time.perf_counter()
        stop_event = stop_event or asyncio.Event()
        playback_cancel = asyncio.Event()

        phase = SelectionPhase.PROMPT
        english_passes = 0
        dtmf_rounds = 0
        first_speech_at: float | None = None

        try:
            while not stop_event.is_set() and phase != SelectionPhase.DONE:
                if phase == SelectionPhase.PROMPT:
                    prompt_lang = locale.prompt_language if locale.country_known else "en"
                    if not locale.country_known:
                        english_passes += 1
                        metrics.english_reprompts = english_passes
                    text = language_selection_prompt(prompt_lang)
                    speak_lang = prompt_lang
                    if not self._tts_supports(prompt_lang):
                        text = language_selection_prompt("en")
                        speak_lang = "en"
                        logger.warning(
                            "TTS cannot speak %s; playing English prompt instead",
                            prompt_lang,
                        )
                    metrics.prompt_language = speak_lang
                    await self._play_prompt(
                        text, speak_lang, outbound_audio, playback_cancel, metrics
                    )
                    _drain_queue(inbound_audio)
                    phase = SelectionPhase.LISTEN
                    continue

                if phase == SelectionPhase.LISTEN:
                    outcome = await self._listen_for_speech_or_timeout(
                        inbound_audio=inbound_audio,
                        outbound_audio=outbound_audio,
                        dtmf_digits=dtmf_digits,
                        stop_event=stop_event,
                        allow_dtmf=True,
                        metrics=metrics,
                        first_speech_holder={"t": first_speech_at},
                    )
                    first_speech_at = outcome.get("first_speech_at", first_speech_at)

                    if outcome["kind"] == "stopped":
                        metrics.outcome = "abandoned"
                        metrics.total_selection_ms = (time.perf_counter() - started) * 1000.0
                        return None

                    if outcome["kind"] == "speech":
                        selected = await self._select_from_speech(
                            outcome["pcm16"], metrics, method="speech"
                        )
                        if selected:
                            metrics.total_selection_ms = (time.perf_counter() - started) * 1000.0
                            metrics.outcome = "selected"
                            _clear_twilio_playback(outbound_audio)
                            return LanguageSelectionResult(
                                language=selected,
                                method="speech",
                                metrics=metrics,
                                locale=locale,
                            )
                        metrics.silence_timeouts += 1
                        phase = self._after_silence(locale, english_passes)
                        continue

                    if outcome["kind"] == "dtmf":
                        language = language_from_dtmf_digit(outcome["digit"], locale.languages)
                        if language:
                            metrics.selected_language = language
                            metrics.selection_method = "dtmf"
                            metrics.dtmf_digit = outcome["digit"]
                            metrics.outcome = "selected"
                            metrics.total_selection_ms = (time.perf_counter() - started) * 1000.0
                            _clear_twilio_playback(outbound_audio)
                            return LanguageSelectionResult(
                                language=language,
                                method="dtmf",
                                metrics=metrics,
                                locale=locale,
                            )
                        logger.info("Ignoring invalid DTMF digit during listen: %s", outcome["digit"])
                        metrics.silence_timeouts += 1
                        phase = self._after_silence(locale, english_passes)
                        continue

                    metrics.silence_timeouts += 1
                    phase = self._after_silence(locale, english_passes)
                    continue

                if phase == SelectionPhase.DTMF_MENU:
                    dtmf_rounds += 1
                    if dtmf_rounds > self.max_dtmf_rounds:
                        metrics.outcome = "abandoned"
                        metrics.total_selection_ms = (time.perf_counter() - started) * 1000.0
                        return None

                    metrics.dtmf_fallback_entered = True
                    menu_lang = locale.prompt_language if locale.country_known else "en"
                    if not self._tts_supports(menu_lang):
                        menu_lang = "en"
                    text = build_dtmf_menu_prompt(locale.languages, spoken_in=menu_lang)
                    playback_cancel.clear()
                    _drain_queue(inbound_audio)

                    outcome = await self._play_menu_with_barge_in(
                        text=text,
                        language=menu_lang,
                        inbound_audio=inbound_audio,
                        outbound_audio=outbound_audio,
                        dtmf_digits=dtmf_digits,
                        stop_event=stop_event,
                        playback_cancel=playback_cancel,
                        metrics=metrics,
                        first_speech_at=first_speech_at,
                    )
                    first_speech_at = outcome.get("first_speech_at", first_speech_at)

                    if outcome["kind"] == "stopped":
                        metrics.outcome = "abandoned"
                        metrics.total_selection_ms = (time.perf_counter() - started) * 1000.0
                        return None

                    if outcome["kind"] == "dtmf":
                        language = language_from_dtmf_digit(outcome["digit"], locale.languages)
                        if language:
                            metrics.selected_language = language
                            metrics.selection_method = "dtmf"
                            metrics.dtmf_digit = outcome["digit"]
                            metrics.outcome = "selected"
                            metrics.total_selection_ms = (time.perf_counter() - started) * 1000.0
                            _clear_twilio_playback(outbound_audio)
                            return LanguageSelectionResult(
                                language=language,
                                method="dtmf",
                                metrics=metrics,
                                locale=locale,
                            )
                        logger.info("Ignoring invalid DTMF digit during menu: %s", outcome["digit"])
                        phase = SelectionPhase.DTMF_MENU
                        continue

                    if outcome["kind"] == "speech":
                        metrics.barge_in_during_dtmf = True
                        selected = await self._select_from_speech(
                            outcome["pcm16"], metrics, method="speech_barge_in"
                        )
                        if selected:
                            metrics.total_selection_ms = (time.perf_counter() - started) * 1000.0
                            metrics.outcome = "selected"
                            _clear_twilio_playback(outbound_audio)
                            return LanguageSelectionResult(
                                language=selected,
                                method="speech_barge_in",
                                metrics=metrics,
                                locale=locale,
                            )
                        phase = SelectionPhase.DTMF_MENU
                        continue

                    phase = SelectionPhase.DTMF_MENU
                    continue

            metrics.outcome = "abandoned"
            metrics.total_selection_ms = (time.perf_counter() - started) * 1000.0
            return None
        finally:
            logger.info("language_selection_metrics %s", metrics.to_dict())

    async def _play_menu_with_barge_in(
        self,
        *,
        text: str,
        language: str,
        inbound_audio: asyncio.Queue[bytes],
        outbound_audio: asyncio.Queue[str],
        dtmf_digits: asyncio.Queue[str],
        stop_event: asyncio.Event,
        playback_cancel: asyncio.Event,
        metrics: LanguageSelectionMetrics,
        first_speech_at: float | None,
    ) -> dict[str, Any]:
        listen_task = asyncio.create_task(
            self._listen_for_speech_or_timeout(
                inbound_audio=inbound_audio,
                outbound_audio=outbound_audio,
                dtmf_digits=dtmf_digits,
                stop_event=stop_event,
                allow_dtmf=True,
                metrics=metrics,
                first_speech_holder={"t": first_speech_at},
                timeout_s=3600.0,
                cancel_playback=playback_cancel,
            )
        )
        play_task = asyncio.create_task(
            self._play_prompt(text, language, outbound_audio, playback_cancel, metrics)
        )

        while True:
            if stop_event.is_set():
                playback_cancel.set()
                _clear_twilio_playback(outbound_audio)
                listen_task.cancel()
                play_task.cancel()
                await asyncio.gather(listen_task, play_task, return_exceptions=True)
                return {"kind": "stopped", "first_speech_at": first_speech_at}

            if listen_task.done():
                # DTMF or speech barge-in: stop local enqueue and flush Twilio buffer.
                playback_cancel.set()
                # #region agent log
                listen_result = listen_task.result()
                _agent_dbg(
                    "A",
                    "language_selection.py:menu_listen_done",
                    "barge_in_listen_completed_before_clear",
                    {
                        "outcome_kind": listen_result.get("kind"),
                        "clear_sent_after_utterance": True,
                        "play_task_done": play_task.done(),
                    },
                )
                # #endregion
                _clear_twilio_playback(outbound_audio)
                if not play_task.done():
                    play_task.cancel()
                    try:
                        await play_task
                    except asyncio.CancelledError:
                        pass
                return listen_result

            if play_task.done():
                # Burst mode finishes enqueue instantly while Twilio still plays the
                # menu for many seconds — keep listening for the full audio duration
                # plus silence, or the menu restarts while the caller is still hearing it.
                try:
                    mulaw_bytes = int(play_task.result() or 0)
                except Exception:
                    mulaw_bytes = 0
                listen_after_play_s = (mulaw_bytes / float(TWILIO_SAMPLE_RATE)) + self.silence_timeout_s
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(listen_task),
                        timeout=max(listen_after_play_s, self.silence_timeout_s),
                    )
                except asyncio.TimeoutError:
                    playback_cancel.set()
                    listen_task.cancel()
                    try:
                        await listen_task
                    except asyncio.CancelledError:
                        pass
                    return {"kind": "timeout", "first_speech_at": first_speech_at}

            await asyncio.sleep(0.01)

    def _after_silence(self, locale: CallerLocale, english_passes: int) -> SelectionPhase:
        if not locale.country_known and english_passes < 2:
            return SelectionPhase.PROMPT
        return SelectionPhase.DTMF_MENU

    def _tts_supports(self, language: str) -> bool:
        supports = getattr(self.tts, "supports_language", None)
        if callable(supports):
            return bool(supports(language))
        return True

    async def _select_from_speech(
        self,
        pcm16: bytes,
        metrics: LanguageSelectionMetrics,
        method: str,
    ) -> str | None:
        metrics.speech_utterances += 1
        result = await self.lid.identify(pcm16)
        duration_ms = int(1000 * (len(pcm16) / 2) / TWILIO_SAMPLE_RATE)
        if result is None:
            # #region agent log
            _agent_dbg(
                "B,D,E",
                "language_selection.py:_select_from_speech",
                "lid_returned_none",
                {"method": method, "duration_ms": duration_ms, "pcm16_bytes": len(pcm16)},
            )
            # #endregion
            return None
        metrics.lid_backend = result.backend
        metrics.lid_language = result.language
        metrics.lid_confidence = result.confidence
        metrics.lid_latency_ms = result.latency_ms
        accepted = result.confidence >= self.min_lid_confidence
        # #region agent log
        _agent_dbg(
            "D,E",
            "language_selection.py:_select_from_speech",
            "lid_decision",
            {
                "method": method,
                "duration_ms": duration_ms,
                "pcm16_bytes": len(pcm16),
                "utterance_rms": round(pcm16_rms(pcm16), 1),
                "language": result.language,
                "confidence": result.confidence,
                "remapped_from": getattr(result, "remapped_from", None),
                "backend": result.backend,
                "min_lid_confidence": self.min_lid_confidence,
                "accepted": accepted,
            },
            run_id="post-fix",
        )
        # #endregion
        if result.confidence < self.min_lid_confidence:
            logger.info(
                "LID confidence too low: lang=%s confidence=%.3f",
                result.language,
                result.confidence,
            )
            return None
        metrics.selected_language = result.language
        metrics.selection_method = method  # type: ignore[assignment]
        return result.language

    async def _play_prompt(
        self,
        text: str,
        language: str,
        outbound_audio: asyncio.Queue[str],
        cancel_event: asyncio.Event,
        metrics: LanguageSelectionMetrics,
    ) -> int:
        """Enqueue prompt audio. Returns μ-law byte length (≈ duration at 8 kHz)."""
        cancel_event.clear()
        synth_started = time.perf_counter()
        try:
            mulaw = await self.tts.synthesize(text, language)
        except Exception:
            logger.exception("TTS synthesize failed for lang=%s; falling back to tone stub", language)
            mulaw = await ToneTextToSpeech(ms_per_char=30, min_ms=600, max_ms=4000).synthesize(
                text, language
            )
        metrics.tts_calls += 1
        metrics.tts_synth_ms_total += (time.perf_counter() - synth_started) * 1000.0
        logger.info(
            "Playing prompt lang=%s bytes=%s realtime=%s text=%.80r",
            language,
            len(mulaw),
            self.playback_realtime,
            text,
        )

        # Twilio buffers outbound media and plays in order. Prefer bursting
        # frames (or one payload) so WS/event-loop jitter does not insert gaps.
        chunks = chunk_mulaw(mulaw, chunk_ms=self.outbound_chunk_ms)
        if not self.playback_realtime:
            if cancel_event.is_set():
                return len(mulaw)
            # Single payload is valid per Twilio and avoids inter-frame gaps.
            await outbound_audio.put(base64.b64encode(mulaw).decode("ascii"))
            await asyncio.sleep(0)
            return len(mulaw)

        # Absolute-clock pacing with a short pre-buffer if realtime is required.
        prebuffer_frames = max(1, 100 // self.outbound_chunk_ms)
        next_deadline = time.perf_counter()
        for index, chunk in enumerate(chunks):
            if cancel_event.is_set():
                break
            await outbound_audio.put(base64.b64encode(chunk).decode("ascii"))
            if index + 1 < prebuffer_frames:
                continue
            next_deadline += self.outbound_chunk_ms / 1000.0
            delay = next_deadline - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
        return len(mulaw)

    async def _listen_for_speech_or_timeout(
        self,
        *,
        inbound_audio: asyncio.Queue[bytes],
        dtmf_digits: asyncio.Queue[str],
        stop_event: asyncio.Event,
        allow_dtmf: bool,
        metrics: LanguageSelectionMetrics,
        first_speech_holder: dict[str, float | None],
        timeout_s: float | None = None,
        cancel_playback: asyncio.Event | None = None,
        outbound_audio: asyncio.Queue[str] | None = None,
    ) -> dict[str, Any]:
        self.vad.reset()
        deadline = time.perf_counter() + (
            timeout_s if timeout_s is not None else self.silence_timeout_s
        )
        listen_started = time.perf_counter()
        logger.info(
            "Listening allow_dtmf=%s timeout_s=%.2f vad_rms=%.1f",
            allow_dtmf,
            timeout_s if timeout_s is not None else self.silence_timeout_s,
            self.vad.config.rms_threshold,
        )

        while not stop_event.is_set():
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                logger.info("Listen timed out with no selection input")
                return {"kind": "timeout", "first_speech_at": first_speech_holder.get("t")}

            tasks = [asyncio.create_task(inbound_audio.get(), name="audio")]
            if allow_dtmf:
                tasks.append(asyncio.create_task(dtmf_digits.get(), name="dtmf"))

            done, pending = await asyncio.wait(
                tasks,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if not done:
                logger.info("Listen timed out with no selection input")
                return {"kind": "timeout", "first_speech_at": first_speech_holder.get("t")}

            finished = next(iter(done))
            if finished.get_name() == "dtmf":
                digit = finished.result()
                if cancel_playback is not None:
                    cancel_playback.set()
                if outbound_audio is not None and cancel_playback is not None:
                    _clear_twilio_playback(outbound_audio)
                logger.info("DTMF digit received during listen: %s", digit)
                return {
                    "kind": "dtmf",
                    "digit": digit,
                    "first_speech_at": first_speech_holder.get("t"),
                }

            chunk = finished.result()
            events = self.vad.process_mulaw(chunk)
            for event in events:
                if event.kind == "speech_start":
                    playback_was_active = (
                        cancel_playback is not None and not cancel_playback.is_set()
                    )
                    if cancel_playback is not None:
                        cancel_playback.set()
                    cleared_now = False
                    if outbound_audio is not None and cancel_playback is not None:
                        _clear_twilio_playback(outbound_audio)
                        cleared_now = True
                    # #region agent log
                    _agent_dbg(
                        "A,C",
                        "language_selection.py:speech_start",
                        "vad_speech_start",
                        {
                            "barge_in_listen": cancel_playback is not None,
                            "playback_cancel_was_unset": playback_was_active,
                            "cleared_twilio_on_speech_start": cleared_now,
                            "chunk_bytes": len(chunk),
                            "chunk_rms": round(pcm16_rms(mulaw_to_pcm16(chunk)), 1),
                        },
                        run_id="post-fix",
                    )
                    # #endregion
                    if first_speech_holder.get("t") is None:
                        first_speech_holder["t"] = (time.perf_counter() - listen_started) * 1000.0
                        metrics.time_to_first_speech_ms = first_speech_holder["t"]
                    logger.info("VAD speech_start")
                    deadline = time.perf_counter() + max(self.silence_timeout_s, 3.0)
                elif event.kind == "speech_end":
                    duration_ms = int(1000 * (len(event.audio_pcm16) / 2) / TWILIO_SAMPLE_RATE)
                    utterance_rms = round(pcm16_rms(event.audio_pcm16), 1)
                    # #region agent log
                    _agent_dbg(
                        "A,B,C",
                        "language_selection.py:speech_end",
                        "vad_speech_end",
                        {
                            "barge_in_listen": cancel_playback is not None,
                            "playback_cancel_set": (
                                cancel_playback.is_set() if cancel_playback is not None else None
                            ),
                            "pcm16_bytes": len(event.audio_pcm16),
                            "duration_ms": duration_ms,
                            "utterance_rms": utterance_rms,
                        },
                        run_id="post-fix",
                    )
                    # #endregion
                    logger.info("VAD speech_end bytes=%s", len(event.audio_pcm16))
                    return {
                        "kind": "speech",
                        "pcm16": event.audio_pcm16,
                        "first_speech_at": first_speech_holder.get("t"),
                    }

        return {"kind": "stopped", "first_speech_at": first_speech_holder.get("t")}


def _clear_twilio_playback(outbound_audio: asyncio.Queue[str]) -> None:
    """Drop unsent local frames and ask Twilio to flush its outbound buffer."""
    while not outbound_audio.empty():
        try:
            outbound_audio.get_nowait()
        except asyncio.QueueEmpty:
            break
    try:
        outbound_audio.put_nowait(CLEAR_AUDIO_SENTINEL)
    except asyncio.QueueFull:
        pass


def _drain_queue(queue: asyncio.Queue) -> int:
    drained = 0
    while not queue.empty():
        try:
            queue.get_nowait()
            drained += 1
        except asyncio.QueueEmpty:
            break
    if drained:
        logger.info("Drained %s queued inbound audio frames before listen", drained)
    return drained
