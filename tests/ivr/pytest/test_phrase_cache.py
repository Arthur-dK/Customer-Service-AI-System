"""Phrase catalog + instant μ-law cache (no TTS on the hot path)."""

from __future__ import annotations

import pytest

from core.language.phrases import (
    DID_NOT_CATCH,
    GOODBYE,
    MAIN_MENU,
    PLACEHOLDER_BALANCE,
    UnknownPhraseError,
    load_phrase_catalog,
)
from services.ivr.phrase_cache import PhraseAudioCache, PhraseNotReadyError
from services.ivr.tts import ToneTextToSpeech
from services.ivr.ttfb import TtfbHarness


class CountingTone(ToneTextToSpeech):
    def __init__(self) -> None:
        super().__init__(ms_per_char=5, min_ms=40, max_ms=200)
        self.calls = 0

    async def synthesize(self, text: str, language: str) -> bytes:
        self.calls += 1
        return await super().synthesize(text, language)


def test_catalog_has_english_and_french_for_static_lines():
    catalog = load_phrase_catalog()
    assert catalog.warmup_languages == ("en", "fr")
    for phrase_id in (DID_NOT_CATCH, MAIN_MENU, PLACEHOLDER_BALANCE, GOODBYE):
        assert catalog.has(phrase_id, "en")
        assert catalog.has(phrase_id, "fr")
        assert catalog.text(phrase_id, "en")
        assert catalog.text(phrase_id, "fr")
        assert catalog.text(phrase_id, "en") != catalog.text(phrase_id, "fr")


def test_unknown_phrase_id_raises():
    catalog = load_phrase_catalog()
    with pytest.raises(UnknownPhraseError):
        catalog.text("not_a_real_phrase", "en")
    with pytest.raises(UnknownPhraseError):
        catalog.text(MAIN_MENU, "zh")


@pytest.mark.asyncio
async def test_warmup_then_get_ready_does_not_call_tts(tmp_path):
    tts = CountingTone()
    cache = PhraseAudioCache(tts, cache_dir=tmp_path)

    with pytest.raises(PhraseNotReadyError):
        cache.get_ready(MAIN_MENU, "en")

    warmed = await cache.warmup()
    assert warmed == len(cache.catalog.ids) * len(cache.catalog.warmup_languages)
    assert tts.calls == warmed

    en_menu = cache.get_ready(MAIN_MENU, "en")
    fr_menu = cache.get_ready(MAIN_MENU, "fr")
    en_error = cache.get_ready(DID_NOT_CATCH, "en")
    assert en_menu and fr_menu and en_error
    assert en_menu != fr_menu
    assert tts.calls == warmed


@pytest.mark.asyncio
async def test_get_fills_once_then_memory_and_disk_hit(tmp_path):
    tts = CountingTone()
    cache = PhraseAudioCache(tts, cache_dir=tmp_path)
    first = await cache.get(GOODBYE, "en")
    second = await cache.get(GOODBYE, "en")
    assert first == second
    assert tts.calls == 1
    assert (tmp_path / "goodbye.en.mulaw").exists()

    tts_other = CountingTone()
    reloaded = PhraseAudioCache(tts_other, cache_dir=tmp_path)
    from_disk = await reloaded.get(GOODBYE, "en")
    assert from_disk == first
    assert tts_other.calls == 0
    assert reloaded.get_ready(GOODBYE, "en") == first


@pytest.mark.asyncio
async def test_warmup_skips_languages_tts_cannot_speak(tmp_path):
    class EnglishOnly(CountingTone):
        def supports_language(self, language: str) -> bool:
            return language.lower().startswith("en")

    tts = EnglishOnly()
    cache = PhraseAudioCache(tts, cache_dir=tmp_path)
    warmed = await cache.warmup()
    assert warmed == len(cache.catalog.ids)
    assert cache.is_ready(MAIN_MENU, "en")
    assert not cache.is_ready(MAIN_MENU, "fr")


@pytest.mark.asyncio
async def test_ready_phrase_ttfb_is_lookup_only(tmp_path):
    cache = PhraseAudioCache(CountingTone(), cache_dir=tmp_path)
    await cache.warmup(languages=("en",))
    harness = TtfbHarness()
    harness.mark_speech_end()
    audio = cache.get_ready(PLACEHOLDER_BALANCE, "en")
    sample = harness.mark_first_audio_byte()
    assert audio
    assert sample is not None
    assert sample.within_budget is True
    assert sample.ttfb_ms < 50.0
