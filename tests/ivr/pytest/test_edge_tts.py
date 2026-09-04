"""Edge TTS voice routing (no network)."""

from services.ivr.edge_tts import EDGE_VOICES, EdgeTextToSpeech


def test_edge_supports_english_french_hebrew_arabic_not_random():
    tts = EdgeTextToSpeech()
    assert tts.supports_language("en") is True
    assert tts.supports_language("fr") is True
    assert tts.supports_language("he") is True
    assert tts.supports_language("ar") is True
    assert tts.supports_language("kk") is False
    assert "en" in EDGE_VOICES
    assert "fr" in EDGE_VOICES
    assert "he" in EDGE_VOICES
    assert "ar" in EDGE_VOICES
