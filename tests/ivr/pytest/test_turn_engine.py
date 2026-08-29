"""Placeholder turn engine: speech_end → canned reply; median TTFB ≤ 500ms."""

from __future__ import annotations

import asyncio

import pytest

from core.language.phrases import (
    DID_NOT_CATCH,
    GOODBYE,
    MAIN_MENU,
    PLACEHOLDER_BALANCE,
    PLACEHOLDER_BLOCKED,
    PLACEHOLDER_PIN,
)
from services.ivr.audio import chunk_mulaw, generate_silence_mulaw, generate_tone_mulaw
from services.ivr.phrase_cache import PhraseAudioCache
from services.ivr.placeholder_intents import map_placeholder_intent
from services.ivr.streaming_stt import ScriptedStreamingSpeechToText
from services.ivr.tts import ToneTextToSpeech
from services.ivr.ttfb import CANNED_TTFB_BUDGET_MS, TtfbHarness
from services.ivr.turn_engine import PlaceholderTurnEngine


class CountingTone(ToneTextToSpeech):
    def __init__(self) -> None:
        super().__init__(ms_per_char=5, min_ms=40, max_ms=200)
        self.calls = 0

    async def synthesize(self, text: str, language: str) -> bytes:
        self.calls += 1
        return await super().synthesize(text, language)


def _utterance_mulaw() -> bytes:
    return generate_tone_mulaw(400, amplitude=0.45) + generate_silence_mulaw(400)


def test_map_placeholder_intent_english_and_french():
    assert map_placeholder_intent("What's my balance?") == PLACEHOLDER_BALANCE
    assert map_placeholder_intent("solde s'il vous plait") == PLACEHOLDER_BALANCE
    assert map_placeholder_intent("PIN please") == PLACEHOLDER_PIN
    assert map_placeholder_intent("bloquer la carte") == PLACEHOLDER_BLOCKED
    assert map_placeholder_intent("Goodbye") == GOODBYE
    assert map_placeholder_intent("xyzzy") == DID_NOT_CATCH
    assert map_placeholder_intent("  ") == DID_NOT_CATCH


async def _warmed_engine(
    tmp_path,
    *,
    language: str = "en",
    finals: list[str],
) -> tuple[PlaceholderTurnEngine, CountingTone]:
    tts = CountingTone()
    cache = PhraseAudioCache(tts, cache_dir=tmp_path)
    await cache.warmup(languages=(language,))
    engine = PlaceholderTurnEngine(
        language=language,
        cache=cache,
        stt=ScriptedStreamingSpeechToText(finals=finals),
        ttfb=TtfbHarness(),
    )
    await engine.start()
    return engine, tts


@pytest.mark.asyncio
async def test_balance_turn_plays_canned_phrase_and_records_ttfb(tmp_path):
    engine, tts = await _warmed_engine(tmp_path, finals=["I want my balance"])
    calls_after_warm = tts.calls
    outbound: asyncio.Queue[str] = asyncio.Queue()

    result = await engine.handle_utterance(_utterance_mulaw(), outbound)

    assert result is not None
    assert result.phrase_id == PLACEHOLDER_BALANCE
    assert result.ended is False
    assert result.chunks_sent >= 1
    assert outbound.qsize() == result.chunks_sent
    assert tts.calls == calls_after_warm
    assert engine.ttfb.samples[0].within_budget is True
    assert engine.ttfb.samples[0].ttfb_ms < CANNED_TTFB_BUDGET_MS


@pytest.mark.asyncio
async def test_unknown_transcript_plays_did_not_catch(tmp_path):
    engine, _ = await _warmed_engine(tmp_path, finals=["humming"])
    outbound: asyncio.Queue[str] = asyncio.Queue()
    result = await engine.handle_utterance(_utterance_mulaw(), outbound)
    assert result is not None
    assert result.phrase_id == DID_NOT_CATCH


@pytest.mark.asyncio
async def test_french_solde_uses_french_buffers(tmp_path):
    engine, _ = await _warmed_engine(tmp_path, language="fr", finals=["mon solde"])
    outbound: asyncio.Queue[str] = asyncio.Queue()
    result = await engine.handle_utterance(_utterance_mulaw(), outbound)
    assert result is not None
    assert result.language == "fr"
    assert result.phrase_id == PLACEHOLDER_BALANCE
    assert engine.cache.is_ready(PLACEHOLDER_BALANCE, "fr")


@pytest.mark.asyncio
async def test_scripted_session_menu_then_goodbye(tmp_path):
    engine, _ = await _warmed_engine(tmp_path, finals=["goodbye"])
    outbound: asyncio.Queue[str] = asyncio.Queue()
    results = await engine.run_scripted_session([_utterance_mulaw()], outbound, play_menu=True)
    assert len(results) == 1
    assert results[0].phrase_id == GOODBYE
    assert results[0].ended is True
    # Menu is not a TTFB sample; goodbye reply is.
    assert len(engine.ttfb.samples) == 1
    menu_audio = engine.cache.get_ready(MAIN_MENU, "en")
    assert outbound.qsize() > 1
    assert len(menu_audio) > 0


@pytest.mark.asyncio
async def test_median_canned_ttfb_under_500ms_benchmark(tmp_path):
    finals = ["balance", "PIN", "block card", "humming", "goodbye"]
    engine, tts = await _warmed_engine(tmp_path, finals=list(finals))
    calls_after_warm = tts.calls
    outbound: asyncio.Queue[str] = asyncio.Queue()
    utterances = [_utterance_mulaw() for _ in finals]

    results = await engine.run_scripted_session(utterances, outbound, play_menu=True)

    assert [item.phrase_id for item in results] == [
        PLACEHOLDER_BALANCE,
        PLACEHOLDER_PIN,
        PLACEHOLDER_BLOCKED,
        DID_NOT_CATCH,
        GOODBYE,
    ]
    assert tts.calls == calls_after_warm
    assert engine.ttfb.canned_typical_within_budget() is True
    typical = engine.ttfb.typical_canned_ttfb_ms()
    assert typical is not None
    assert typical <= CANNED_TTFB_BUDGET_MS
    assert all(sample.ttfb_ms < 100.0 for sample in engine.ttfb.samples)


@pytest.mark.asyncio
async def test_inbound_queue_turn_matches_blob_path(tmp_path):
    engine, _ = await _warmed_engine(tmp_path, finals=["balance"])
    inbound: asyncio.Queue[bytes] = asyncio.Queue()
    outbound: asyncio.Queue[str] = asyncio.Queue()
    stop = asyncio.Event()
    for chunk in chunk_mulaw(_utterance_mulaw(), chunk_ms=20):
        await inbound.put(chunk)

    result = await engine.handle_inbound_queue(inbound, outbound, stop)

    assert result is not None
    assert result.phrase_id == PLACEHOLDER_BALANCE
    assert result.chunks_sent >= 1
    assert engine.ttfb.samples[0].within_budget is True


@pytest.mark.asyncio
async def test_unsupported_lid_language_plays_english_menu(tmp_path):
    tts = CountingTone()
    cache = PhraseAudioCache(tts, cache_dir=tmp_path)
    await cache.warmup(languages=("en",))
    engine = PlaceholderTurnEngine(
        language="kk",
        cache=cache,
        stt=ScriptedStreamingSpeechToText(finals=["balance"]),
    )
    outbound: asyncio.Queue[str] = asyncio.Queue()
    sent = await engine.play_phrase(MAIN_MENU, outbound)
    assert sent >= 1
    assert outbound.qsize() == sent

