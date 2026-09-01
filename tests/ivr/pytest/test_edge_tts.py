"""Edge TTS voice routing (no network)."""

from services.ivr.edge_tts import EDGE_VOICES, EdgeTextToSpeech


def test_edge_supports_english_and_french_not_random():
    tts = EdgeTextToSpeech()
    assert tts.supports_language("en") is True
    assert tts.supports_language("fr") is True
    assert tts.supports_language("kk") is False
    assert "en" in EDGE_VOICES
    assert "fr" in EDGE_VOICES
