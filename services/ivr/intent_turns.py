"""IVR dialogue after language select: allowlist, GET vs confirm, 2-fail DTMF.

The six-action embedder stays in ``core.intents``. This module is product flow only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from core.cards import CallerStore, StubCard
from core.intents import CardAction, RouteResult
from core.intents.confirm import ConfirmInterpreter
from core.language.phrases import (
    CARD_BLOCKED,
    CARD_UNBLOCKED,
    CONFIRM_BLOCK,
    CONFIRM_UNBLOCK,
    DID_NOT_CATCH,
    DTMF_ACTIONS,
    DTMF_CONFIRM,
    GET_BALANCE,
    GET_CARD,
    GET_CARD_STATEMENT,
    MAIN_MENU,
    PIN_VIA_SMS,
    UNKNOWN_CALLER,
)
from services.ivr.phrase_cache import PhraseAudioCache
from services.ivr.streaming_stt import StreamingSpeechToText, feed_until_speech_end
from services.ivr.streaming_tts import StreamingTextToSpeech, enqueue_tts_stream, stream_ready_phrase
from services.ivr.ttfb import ReplyKind, TtfbHarness
from services.ivr.vad import EnergyVad, VadConfig

logger = logging.getLogger(__name__)

FAIL_LIMIT = 2
GET_ACTIONS = frozenset(
    {
        CardAction.GET_BALANCE,
        CardAction.GET_PIN,
        CardAction.GET_CARD,
        CardAction.GET_CARD_STATEMENT,
    }
)
DTMF_TO_ACTION: dict[str, CardAction] = {
    "1": CardAction.GET_BALANCE,
    "2": CardAction.GET_PIN,
    "3": CardAction.GET_CARD,
    "4": CardAction.GET_CARD_STATEMENT,
    "5": CardAction.BLOCK_CARD,
    "6": CardAction.UNBLOCK_CARD,
}


class DialogueMode(StrEnum):
    LISTEN = "listen"
    CONFIRM = "confirm"
    DTMF_ACTIONS = "dtmf_actions"
    DTMF_CONFIRM = "dtmf_confirm"


class IntentRouterProtocol(Protocol):
    def route(self, text: str) -> RouteResult: ...


@dataclass(frozen=True)
class IntentTurnResult:
    phrase_id: str
    language: str
    chunks_sent: int
    hung_up: bool
    ended: bool
    action: CardAction | None
    mode: str


class IntentTurnEngine:
    """Allowlisted card tasks: router in, stub store out. No issuer API."""

    def __init__(
        self,
        *,
        language: str,
        phone_number: str | None,
        store: CallerStore,
        router: IntentRouterProtocol,
        cache: PhraseAudioCache,
        stt: StreamingSpeechToText,
        ttfb: TtfbHarness | None = None,
        vad: EnergyVad | None = None,
        chunk_ms: int = 20,
        fallback_tts: StreamingTextToSpeech | None = None,
        confirm: ConfirmInterpreter | None = None,
    ) -> None:
        self.language = language.lower()
        self.phone_number = phone_number
        self.store = store
        self.router = router
        self.cache = cache
        self.stt = stt
        self.ttfb = ttfb if ttfb is not None else TtfbHarness()
        self.vad = vad or EnergyVad(VadConfig(rms_threshold=500, speech_start_ms=100, speech_end_ms=200))
        self.chunk_ms = chunk_ms
        self.fallback_tts = fallback_tts
        self.confirm = confirm or ConfirmInterpreter()
        self.fail_count = 0
        self.confirm_fail_count = 0
        self.mode = DialogueMode.LISTEN
        self.pending_action: CardAction | None = None

    def card(self) -> StubCard | None:
        return self.store.lookup(self.phone_number)

    async def start(self) -> None:
        await self.stt.start(language=self.language)

    async def play_phrase(
        self,
        phrase_id: str,
        outbound: asyncio.Queue[str],
        *,
        measure_ttfb: bool = False,
        cancel: asyncio.Event | None = None,
        slots: dict[str, str] | None = None,
    ) -> int:
        if slots:
            play_lang = self.cache.play_language(phrase_id, self.language)
            text = self.cache.catalog.formatted(phrase_id, play_lang, **slots)
            if self.fallback_tts is None:
                raise RuntimeError("slot replies need fallback_tts")
            return await enqueue_tts_stream(
                self.fallback_tts.stream(
                    text,
                    play_lang,
                    chunk_ms=self.chunk_ms,
                    cancel=cancel,
                ),
                outbound,
                self.ttfb if measure_ttfb else None,
                reply_kind=ReplyKind.CANNED,
                cancel=cancel,
            )
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

    async def refuse_unknown(self, outbound: asyncio.Queue[str]) -> IntentTurnResult:
        sent = await self.play_phrase(UNKNOWN_CALLER, outbound, measure_ttfb=False)
        logger.info("intent_turn phrase=%s hung_up=true", UNKNOWN_CALLER)
        return IntentTurnResult(
            phrase_id=UNKNOWN_CALLER,
            language=self.language,
            chunks_sent=sent,
            hung_up=True,
            ended=True,
            action=None,
            mode=self.mode.value,
        )

    async def handle_transcript(
        self,
        text: str,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None = None,
    ) -> IntentTurnResult:
        if self.mode in (DialogueMode.CONFIRM, DialogueMode.DTMF_CONFIRM):
            return await self._handle_confirm_voice(text, outbound, cancel=cancel)
        if self.mode == DialogueMode.DTMF_ACTIONS:
            sent = await self.play_phrase(DTMF_ACTIONS, outbound, measure_ttfb=True, cancel=cancel)
            return self._result(DTMF_ACTIONS, sent, action=None)
        return await self._handle_listen(text, outbound, cancel=cancel)

    async def handle_dtmf(
        self,
        digit: str,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None = None,
    ) -> IntentTurnResult:
        if self.mode == DialogueMode.DTMF_CONFIRM:
            if digit == "1":
                return await self._apply_pending(outbound, cancel=cancel)
            if digit == "2":
                self.pending_action = None
                self.mode = DialogueMode.LISTEN
                self.confirm_fail_count = 0
                sent = await self.play_phrase(DID_NOT_CATCH, outbound, measure_ttfb=True, cancel=cancel)
                return self._result(DID_NOT_CATCH, sent, action=None)
            return await self._confirm_unclear(outbound, cancel=cancel)
        action = DTMF_TO_ACTION.get(digit)
        if action is None:
            return await self._listen_fail(outbound, cancel=cancel)
        self.fail_count = 0
        self.mode = DialogueMode.LISTEN
        return await self._dispatch_action(action, outbound, cancel=cancel)

    async def handle_utterance(
        self,
        mulaw: bytes,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None = None,
    ) -> IntentTurnResult | None:
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
        return await self.handle_transcript(transcript.text, outbound, cancel=cancel)

    async def handle_inbound_queue(
        self,
        inbound_audio: asyncio.Queue[bytes],
        outbound: asyncio.Queue[str],
        stop_event: asyncio.Event,
        *,
        dtmf_digits: asyncio.Queue[str] | None = None,
        cancel: asyncio.Event | None = None,
    ) -> IntentTurnResult | None:
        """Read live Media Stream frames (or a DTMF digit) until one dialogue result."""
        self.vad.reset()
        while not stop_event.is_set():
            if dtmf_digits is not None:
                try:
                    digit = dtmf_digits.get_nowait()
                except asyncio.QueueEmpty:
                    digit = None
                if digit:
                    return await self.handle_dtmf(digit, outbound, cancel=cancel)
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
                    return await self.handle_transcript(
                        transcript.text, outbound, cancel=cancel
                    )
        return None

    async def run_on_queues(
        self,
        *,
        inbound_audio: asyncio.Queue[bytes],
        outbound_audio: asyncio.Queue[str],
        stop_event: asyncio.Event,
        dtmf_digits: asyncio.Queue[str] | None = None,
        play_menu: bool = True,
        max_turns: int = 8,
        on_turn: Callable[[list[IntentTurnResult]], None] | None = None,
    ) -> list[IntentTurnResult]:
        """Handoff after language selection: same inbound/outbound/DTMF queues."""
        await self.start()
        if self.card() is None:
            result = await self.refuse_unknown(outbound_audio)
            if on_turn is not None:
                on_turn([result])
            await outbound_audio.join()
            stop_event.set()
            return [result]
        if play_menu:
            await self.play_phrase(MAIN_MENU, outbound_audio, measure_ttfb=False)
        results: list[IntentTurnResult] = []
        for _ in range(max_turns):
            if stop_event.is_set():
                break
            result = await self.handle_inbound_queue(
                inbound_audio,
                outbound_audio,
                stop_event,
                dtmf_digits=dtmf_digits,
            )
            if result is None:
                break
            results.append(result)
            if on_turn is not None:
                on_turn(results)
            if result.hung_up:
                await outbound_audio.join()
                stop_event.set()
                break
            if result.ended:
                break
        return results

    async def run_scripted_session(
        self,
        utterances: list[bytes],
        outbound: asyncio.Queue[str],
        *,
        play_menu: bool = True,
        dtmf_digits: list[str] | None = None,
    ) -> list[IntentTurnResult]:
        await self.start()
        if self.card() is None:
            return [await self.refuse_unknown(outbound)]
        if play_menu:
            await self.play_phrase(MAIN_MENU, outbound, measure_ttfb=False)
        results: list[IntentTurnResult] = []
        digits = list(dtmf_digits or ())
        for mulaw in utterances:
            if self.mode in (DialogueMode.DTMF_ACTIONS, DialogueMode.DTMF_CONFIRM) and digits:
                result = await self.handle_dtmf(digits.pop(0), outbound)
            else:
                result = await self.handle_utterance(mulaw, outbound)
            if result is None:
                continue
            results.append(result)
            if result.ended:
                break
        return results

    async def _handle_listen(
        self,
        text: str,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None,
    ) -> IntentTurnResult:
        routed = self.router.route(text)
        if routed.rejected or routed.action is None:
            return await self._listen_fail(outbound, cancel=cancel)
        self.fail_count = 0
        return await self._dispatch_action(routed.action, outbound, cancel=cancel)

    async def _listen_fail(
        self,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None,
    ) -> IntentTurnResult:
        self.fail_count += 1
        if self.fail_count >= FAIL_LIMIT:
            self.mode = DialogueMode.DTMF_ACTIONS
            sent = await self.play_phrase(DTMF_ACTIONS, outbound, measure_ttfb=True, cancel=cancel)
            logger.info("intent_turn phrase=%s mode=dtmf_actions", DTMF_ACTIONS)
            return self._result(DTMF_ACTIONS, sent, action=None)
        sent = await self.play_phrase(DID_NOT_CATCH, outbound, measure_ttfb=True, cancel=cancel)
        return self._result(DID_NOT_CATCH, sent, action=None)

    async def _dispatch_action(
        self,
        action: CardAction,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None,
    ) -> IntentTurnResult:
        card = self.card()
        if card is None:
            return await self.refuse_unknown(outbound)
        if action in GET_ACTIONS:
            return await self._play_get(action, card, outbound, cancel=cancel)
        return await self._begin_confirm(action, card, outbound, cancel=cancel)

    async def _play_get(
        self,
        action: CardAction,
        card: StubCard,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None,
    ) -> IntentTurnResult:
        status = "blocked" if card.blocked else "active"
        if action == CardAction.GET_PIN:
            sent = await self.play_phrase(PIN_VIA_SMS, outbound, measure_ttfb=True, cancel=cancel)
            return self._result(PIN_VIA_SMS, sent, action=action)
        if action == CardAction.GET_BALANCE:
            sent = await self.play_phrase(
                GET_BALANCE,
                outbound,
                measure_ttfb=True,
                cancel=cancel,
                slots={"balance_text": card.balance_text},
            )
            return self._result(GET_BALANCE, sent, action=action)
        if action == CardAction.GET_CARD:
            sent = await self.play_phrase(
                GET_CARD,
                outbound,
                measure_ttfb=True,
                cancel=cancel,
                slots={"last4": card.last4, "status": status},
            )
            return self._result(GET_CARD, sent, action=action)
        sent = await self.play_phrase(
            GET_CARD_STATEMENT,
            outbound,
            measure_ttfb=True,
            cancel=cancel,
            slots={"statement": card.statement},
        )
        return self._result(GET_CARD_STATEMENT, sent, action=action)

    async def _begin_confirm(
        self,
        action: CardAction,
        card: StubCard,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None,
    ) -> IntentTurnResult:
        self.pending_action = action
        self.mode = DialogueMode.CONFIRM
        self.confirm_fail_count = 0
        phrase_id = CONFIRM_BLOCK if action == CardAction.BLOCK_CARD else CONFIRM_UNBLOCK
        sent = await self.play_phrase(
            phrase_id,
            outbound,
            measure_ttfb=True,
            cancel=cancel,
            slots={"last4": card.last4},
        )
        return self._result(phrase_id, sent, action=action, ended=False)

    async def _handle_confirm_voice(
        self,
        text: str,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None,
    ) -> IntentTurnResult:
        interpreted = self.confirm.interpret(text)
        if interpreted.answer is True:
            return await self._apply_pending(outbound, cancel=cancel)
        if interpreted.answer is False:
            self.pending_action = None
            self.mode = DialogueMode.LISTEN
            self.confirm_fail_count = 0
            sent = await self.play_phrase(DID_NOT_CATCH, outbound, measure_ttfb=True, cancel=cancel)
            return self._result(DID_NOT_CATCH, sent, action=None)
        return await self._confirm_unclear(outbound, cancel=cancel)

    async def _confirm_unclear(
        self,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None,
    ) -> IntentTurnResult:
        self.confirm_fail_count += 1
        if self.confirm_fail_count >= FAIL_LIMIT:
            self.mode = DialogueMode.DTMF_CONFIRM
            sent = await self.play_phrase(DTMF_CONFIRM, outbound, measure_ttfb=True, cancel=cancel)
            return self._result(DTMF_CONFIRM, sent, action=self.pending_action)
        sent = await self.play_phrase(DID_NOT_CATCH, outbound, measure_ttfb=True, cancel=cancel)
        return self._result(DID_NOT_CATCH, sent, action=self.pending_action)

    async def _apply_pending(
        self,
        outbound: asyncio.Queue[str],
        *,
        cancel: asyncio.Event | None,
    ) -> IntentTurnResult:
        action = self.pending_action
        card = self.card()
        self.pending_action = None
        self.mode = DialogueMode.LISTEN
        self.confirm_fail_count = 0
        if action is None or card is None:
            return await self.refuse_unknown(outbound)
        blocked = action == CardAction.BLOCK_CARD
        self.store.set_blocked(card.card_id, blocked)
        phrase_id = CARD_BLOCKED if blocked else CARD_UNBLOCKED
        sent = await self.play_phrase(
            phrase_id,
            outbound,
            measure_ttfb=True,
            cancel=cancel,
            slots={"last4": card.last4},
        )
        return self._result(phrase_id, sent, action=action)

    def _result(
        self,
        phrase_id: str,
        sent: int,
        *,
        action: CardAction | None,
        ended: bool = False,
        hung_up: bool = False,
    ) -> IntentTurnResult:
        logger.info(
            "intent_turn phrase=%s action=%s mode=%s hung_up=%s",
            phrase_id,
            None if action is None else action.value,
            self.mode.value,
            hung_up,
        )
        return IntentTurnResult(
            phrase_id=phrase_id,
            language=self.language,
            chunks_sent=sent,
            hung_up=hung_up,
            ended=ended or hung_up,
            action=action,
            mode=self.mode.value,
        )
