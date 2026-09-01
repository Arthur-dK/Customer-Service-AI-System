"""Local grammar STT: audio in, placeholder-task transcript out (no network)."""

from __future__ import annotations

import pytest

from services.ivr.audio import generate_silence_mulaw, generate_tone_mulaw
from services.ivr.placeholder_intents import grammar_phrases, map_placeholder_intent
from services.ivr.sapi_stt import GrammarStreamingSpeechToText, recognizer_culture
from services.ivr.streaming_stt import build_default_streaming_stt
from core.language.phrases import PLACEHOLDER_BALANCE


def _utterance_mulaw() -> bytes:
    return generate_tone_mulaw(400, amplitude=0.45) + generate_silence_mulaw(200)


def test_grammar_phrases_cover_english_and_french_intents():
    en = " ".join(grammar_phrases("en"))
    fr = " ".join(grammar_phrases("fr"))
    assert map_placeholder_intent("please tell me my balance") == PLACEHOLDER_BALANCE
    assert "balance" in en
    assert "solde" in fr
    assert map_placeholder_intent(fr.split()[0]) == PLACEHOLDER_BALANCE


def test_recognizer_culture_maps_iso_to_sapi():
    assert recognizer_culture("fr") == "fr-FR"
    assert recognizer_culture("en-GB") == "en-US"


@pytest.mark.asyncio
async def test_grammar_stt_uses_injected_recognizer_and_audio_bytes():
    seen: dict[str, object] = {}

    def fake_recognize(mulaw: bytes, language: str) -> str:
        seen["bytes"] = len(mulaw)
        seen["language"] = language
        return "balance"

    stt = GrammarStreamingSpeechToText(recognize=fake_recognize)
    await stt.start(language="EN")
    audio = _utterance_mulaw()
    await stt.feed_mulaw(audio)
    result = await stt.finish()
    assert result is not None
    assert result.text == "balance"
    assert result.is_final is True
    assert result.language == "en"
    assert seen["bytes"] == len(audio)
    assert seen["language"] == "en"


@pytest.mark.asyncio
async def test_grammar_stt_empty_audio_still_calls_recognizer():
    def fake_recognize(mulaw: bytes, language: str) -> str:
        return ""

    stt = GrammarStreamingSpeechToText(recognize=fake_recognize)
    await stt.start(language="fr")
    result = await stt.finish()
    assert result is not None
    assert result.text == ""
    assert result.language == "fr"


def test_sapi_backend_uses_grammar_stt_even_if_script_present():
    import sys

    stt = build_default_streaming_stt(finals=["balance"], backend="sapi")
    if sys.platform.startswith("win"):
        assert isinstance(stt, GrammarStreamingSpeechToText)
    else:
        from services.ivr.streaming_stt import ScriptedStreamingSpeechToText

        assert isinstance(stt, ScriptedStreamingSpeechToText)


def test_sapi_backend_on_linux_uses_scripted_stub(monkeypatch):
    import services.ivr.streaming_stt as stt_mod
    from services.ivr.streaming_stt import ScriptedStreamingSpeechToText

    monkeypatch.setattr(stt_mod.sys, "platform", "linux")
    stt = build_default_streaming_stt(finals=["balance"], backend="sapi")
    assert isinstance(stt, ScriptedStreamingSpeechToText)


def test_build_default_stt_sapi_when_no_script():
    import sys

    stt = build_default_streaming_stt(finals=[], backend="sapi")
    if sys.platform.startswith("win"):
        assert isinstance(stt, GrammarStreamingSpeechToText)
    else:
        from services.ivr.streaming_stt import ScriptedStreamingSpeechToText

        assert isinstance(stt, ScriptedStreamingSpeechToText)
