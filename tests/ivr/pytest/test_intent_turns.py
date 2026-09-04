"""Intent dialogue: allowlist hangup, GET vs confirm, two rejects → DTMF."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pytest

from core.cards.store import build_caller_store
from core.intents import CardAction, ConfirmInterpreter, ConfirmResult, RouteResult
from core.language.phrases import (
    CARD_BLOCKED,
    CONFIRM_BLOCK,
    DID_NOT_CATCH,
    DTMF_ACTIONS,
    DTMF_CONFIRM,
    GET_BALANCE,
    GET_CARD,
    PIN_VIA_SMS,
    UNKNOWN_CALLER,
)
from services.ivr.audio import generate_silence_mulaw, generate_tone_mulaw
from services.ivr.intent_turns import IntentTurnEngine
from services.ivr.phrase_cache import PhraseAudioCache
from services.ivr.streaming_stt import ScriptedStreamingSpeechToText
from services.ivr.streaming_tts import ToneStreamingTextToSpeech
from services.ivr.tts import ToneTextToSpeech
from services.ivr.ttfb import TtfbHarness

EXAMPLE = Path(__file__).resolve().parents[3] / "data" / "callers.example.json"
PHONE = "+15555550100"
PLANTED_PIN = "9876"
_DIGIT_RUN = re.compile(r"\d{2,}")


class RecordingTone(ToneTextToSpeech):
    def __init__(self) -> None:
        super().__init__(ms_per_char=5, min_ms=40, max_ms=80)
        self.spoken: list[str] = []

    async def synthesize(self, text: str, language: str) -> bytes:
        self.spoken.append(text)
        return await super().synthesize(text, language)


class QueueRouter:
    def __init__(self, results: list[RouteResult]) -> None:
        self._results = list(results)

    def route(self, text: str) -> RouteResult:
        return self._results.pop(0)


class QueueConfirm:
    def __init__(self, answers: list[bool | None]) -> None:
        self._answers = list(answers)

    def interpret(self, text: str) -> ConfirmResult:
        return ConfirmResult(self._answers.pop(0))


def _ok(action: CardAction) -> RouteResult:
    return RouteResult(action=action, score=1.0, second_score=0.0, margin=1.0, rejected=False)


def _reject() -> RouteResult:
    return RouteResult(action=None, score=0.0, second_score=0.0, margin=0.0, rejected=True)


def _utterance_mulaw() -> bytes:
    return generate_tone_mulaw(400, amplitude=0.45) + generate_silence_mulaw(400)


def _store(tmp_path: Path, *, pin: str | None = None):
    local = tmp_path / "callers.local.json"
    if pin is not None:
        local.write_text(
            '{"cards":[{"card_id":"stub-card-demo","pin":"%s"}]}' % pin,
            encoding="utf-8",
        )
    else:
        local.write_text("{}", encoding="utf-8")
    return build_caller_store(
        sqlite_path=tmp_path / "callers.sqlite",
        example_path=EXAMPLE,
        local_path=local,
    )


async def _engine(
    tmp_path: Path,
    *,
    router: QueueRouter,
    phone: str | None = PHONE,
    finals: list[str] | None = None,
    confirm: QueueConfirm | ConfirmInterpreter | None = None,
    pin: str | None = None,
) -> tuple[IntentTurnEngine, RecordingTone]:
    tts = RecordingTone()
    cache = PhraseAudioCache(tts, cache_dir=tmp_path / "phrases")
    await cache.warmup(languages=("en",))
    engine = IntentTurnEngine(
        language="en",
        phone_number=phone,
        store=_store(tmp_path, pin=pin),
        router=router,
        cache=cache,
        stt=ScriptedStreamingSpeechToText(finals=finals or ["ignored"]),
        ttfb=TtfbHarness(),
        fallback_tts=ToneStreamingTextToSpeech(inner=tts),
        confirm=confirm or ConfirmInterpreter(),
    )
    await engine.start()
    return engine, tts


def test_pin_via_sms_copy_has_no_digit_runs():
    from core.language.phrases import load_phrase_catalog

    catalog = load_phrase_catalog()
    spoken = catalog.text(PIN_VIA_SMS, "en")
    assert _DIGIT_RUN.search(spoken) is None
    assert "PIN" in spoken


@pytest.mark.asyncio
async def test_unknown_caller_hangs_up_without_menu(tmp_path: Path):
    engine, tts = await _engine(tmp_path, router=QueueRouter([]), phone="+15555550999")
    spoken_after_warm = list(tts.spoken)
    outbound: asyncio.Queue[str] = asyncio.Queue()
    results = await engine.run_scripted_session([_utterance_mulaw()], outbound, play_menu=True)
    assert len(results) == 1
    assert results[0].phrase_id == UNKNOWN_CALLER
    assert results[0].hung_up is True
    assert results[0].ended is True
    assert engine.cache.is_ready(UNKNOWN_CALLER, "en")
    assert tts.spoken == spoken_after_warm


@pytest.mark.asyncio
async def test_get_balance_interpolates_stub_field(tmp_path: Path):
    engine, tts = await _engine(tmp_path, router=QueueRouter([_ok(CardAction.GET_BALANCE)]))
    outbound: asyncio.Queue[str] = asyncio.Queue()
    result = await engine.handle_transcript("balance please", outbound)
    assert result.phrase_id == GET_BALANCE
    assert result.action == CardAction.GET_BALANCE
    assert result.ended is False
    assert "one hundred US dollars" in tts.spoken[-1]
    assert engine.store.lookup(PHONE) is not None


@pytest.mark.asyncio
async def test_get_card_speaks_last4_and_status(tmp_path: Path):
    engine, tts = await _engine(tmp_path, router=QueueRouter([_ok(CardAction.GET_CARD)]))
    outbound: asyncio.Queue[str] = asyncio.Queue()
    result = await engine.handle_transcript("which card", outbound)
    assert result.phrase_id == GET_CARD
    assert "4242" in tts.spoken[-1]
    assert "active" in tts.spoken[-1]


@pytest.mark.asyncio
async def test_get_pin_plays_sms_placeholder_not_overlay_digits(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    engine, tts = await _engine(
        tmp_path,
        router=QueueRouter([_ok(CardAction.GET_PIN)]),
        pin=PLANTED_PIN,
    )
    outbound: asyncio.Queue[str] = asyncio.Queue()
    result = await engine.handle_transcript("send my pin", outbound)
    assert result.phrase_id == PIN_VIA_SMS
    joined = " ".join(tts.spoken)
    assert PLANTED_PIN not in joined
    assert PLANTED_PIN not in caplog.text
    assert "send my pin" not in caplog.text
    assert "transcript=" not in caplog.text


@pytest.mark.asyncio
async def test_block_confirm_yes_mutates_store(tmp_path: Path):
    engine, tts = await _engine(
        tmp_path,
        router=QueueRouter([_ok(CardAction.BLOCK_CARD)]),
        confirm=QueueConfirm([True]),
    )
    outbound: asyncio.Queue[str] = asyncio.Queue()
    first = await engine.handle_transcript("block it", outbound)
    assert first.phrase_id == CONFIRM_BLOCK
    assert "4242" in tts.spoken[-1]
    assert engine.store.lookup(PHONE).blocked is False
    second = await engine.handle_transcript("yes", outbound)
    assert second.phrase_id == CARD_BLOCKED
    assert engine.store.lookup(PHONE).blocked is True


@pytest.mark.asyncio
async def test_block_confirm_no_does_not_mutate(tmp_path: Path):
    engine, _ = await _engine(
        tmp_path,
        router=QueueRouter([_ok(CardAction.BLOCK_CARD)]),
        confirm=QueueConfirm([False]),
    )
    outbound: asyncio.Queue[str] = asyncio.Queue()
    await engine.handle_transcript("block it", outbound)
    second = await engine.handle_transcript("no", outbound)
    assert second.phrase_id == DID_NOT_CATCH
    assert engine.store.lookup(PHONE).blocked is False
    assert engine.mode.value == "listen"


@pytest.mark.asyncio
async def test_two_rejects_offer_dtmf_then_digit_runs_get(tmp_path: Path):
    engine, tts = await _engine(
        tmp_path,
        router=QueueRouter([_reject(), _reject()]),
    )
    outbound: asyncio.Queue[str] = asyncio.Queue()
    first = await engine.handle_transcript("humming", outbound)
    assert first.phrase_id == DID_NOT_CATCH
    second = await engine.handle_transcript("weather", outbound)
    assert second.phrase_id == DTMF_ACTIONS
    assert engine.mode.value == "dtmf_actions"
    third = await engine.handle_dtmf("1", outbound)
    assert third.phrase_id == GET_BALANCE
    assert "one hundred US dollars" in tts.spoken[-1]


@pytest.mark.asyncio
async def test_two_unclear_confirms_offer_dtmf_confirm(tmp_path: Path):
    engine, _ = await _engine(
        tmp_path,
        router=QueueRouter([_ok(CardAction.BLOCK_CARD)]),
        confirm=QueueConfirm([None, None]),
    )
    outbound: asyncio.Queue[str] = asyncio.Queue()
    await engine.handle_transcript("block it", outbound)
    unclear = await engine.handle_transcript("maybe", outbound)
    assert unclear.phrase_id == DID_NOT_CATCH
    dtmf = await engine.handle_transcript("still maybe", outbound)
    assert dtmf.phrase_id == DTMF_CONFIRM
    yes = await engine.handle_dtmf("1", outbound)
    assert yes.phrase_id == CARD_BLOCKED
    assert engine.store.lookup(PHONE).blocked is True
