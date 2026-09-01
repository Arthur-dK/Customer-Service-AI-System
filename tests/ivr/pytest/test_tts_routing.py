"""TTS must use a voice that matches the call language."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.ivr.tts import (
    InstalledVoice,
    PiperTextToSpeech,
    RoutedTextToSpeech,
    ToneTextToSpeech,
    UnsupportedTtsLanguageError,
    WindowsSapiTextToSpeech,
)
from services.ivr.tts_lang import language_from_piper_path, normalize_language, parse_piper_voice_map


def test_normalize_language_strips_region_and_aliases():
    assert normalize_language("FR") == "fr"
    assert normalize_language("fr-FR") == "fr"
    assert normalize_language("fr_CA") == "fr"
    assert normalize_language("cmn") == "zh"


def test_language_from_piper_filename():
    assert language_from_piper_path("fr_FR-siwis-medium.onnx") == "fr"
    assert language_from_piper_path(Path("en_US-lessac-medium.onnx")) == "en"
    assert language_from_piper_path("mystery.onnx") == "en"


def test_parse_piper_voice_map():
    raw = '{"fr": "C:/voices/fr.onnx", "EN": "C:/voices/en.onnx"}'
    assert parse_piper_voice_map(raw)["fr"] == "C:/voices/fr.onnx"
    assert parse_piper_voice_map(raw)["en"] == "C:/voices/en.onnx"


def test_sapi_supports_only_installed_voice_languages():
    tts = WindowsSapiTextToSpeech(
        voices=[
            InstalledVoice(name="English", language="en", culture="en-US"),
            InstalledVoice(name="French", language="fr", culture="fr-FR"),
        ]
    )
    assert tts.supports_language("en") is True
    assert tts.supports_language("fr-FR") is True
    assert tts.supports_language("de") is False


@pytest.mark.asyncio
async def test_sapi_refuses_to_speak_unsupported_language():
    tts = WindowsSapiTextToSpeech(
        voices=[InstalledVoice(name="English", language="en", culture="en-US")]
    )
    with pytest.raises(UnsupportedTtsLanguageError):
        await tts.synthesize("Bonjour, comment allez-vous?", "fr")


@pytest.mark.asyncio
async def test_router_picks_backend_that_supports_the_language():
    class LangTone(ToneTextToSpeech):
        def __init__(self, language: str) -> None:
            super().__init__(ms_per_char=5, min_ms=40, max_ms=80)
            self.language = language
            self.heard: list[str] = []

        def supports_language(self, language: str) -> bool:
            return language.lower() == self.language

        async def synthesize(self, text: str, language: str) -> bytes:
            self.heard.append(language)
            return await super().synthesize(text, language)

    english = LangTone("en")
    french = LangTone("fr")
    routed = RoutedTextToSpeech([english, french])
    await routed.synthesize("Hello", "en")
    await routed.synthesize("Bonjour", "fr")
    assert english.heard == ["en"]
    assert french.heard == ["fr"]
    with pytest.raises(UnsupportedTtsLanguageError):
        await routed.synthesize("Hallo", "de")


def test_piper_one_model_one_language():
    tts = PiperTextToSpeech(model_path="fr_FR-siwis-medium.onnx", language="fr")
    assert tts.supports_language("fr") is True
    assert tts.supports_language("en") is False
