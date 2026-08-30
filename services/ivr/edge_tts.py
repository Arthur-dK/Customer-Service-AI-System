"""Microsoft Edge neural TTS for Linux/Render (no Windows SAPI).

Uses the public Edge read-aloud endpoint via the ``edge-tts`` package. Audio is
MP3; ``miniaudio`` decodes to PCM so we do not need ffmpeg. First synth is a
network call; ``CachedTextToSpeech`` then serves the μ-law cache.
"""

from __future__ import annotations

import logging

from services.ivr.audio import TWILIO_SAMPLE_RATE, pcm16_to_mulaw
from services.ivr.tts_lang import normalize_language

logger = logging.getLogger(__name__)

# ISO 639-1 → Edge neural voice. Unlisted languages are not spoken (no English-accented French).
EDGE_VOICES: dict[str, str] = {
    "ar": "ar-SA-ZariyahNeural",
    "de": "de-DE-KatjaNeural",
    "en": "en-US-JennyNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "hi": "hi-IN-SwaraNeural",
    "id": "id-ID-GadisNeural",
    "it": "it-IT-ElsaNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "nl": "nl-NL-FennaNeural",
    "pl": "pl-PL-ZofiaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "tr": "tr-TR-EmelNeural",
    "uk": "uk-UA-PolinaNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
}


def edge_tts_available() -> bool:
    try:
        import edge_tts  # noqa: F401
        import miniaudio  # noqa: F401
    except ImportError:
        return False
    return True


class EdgeTextToSpeech:
    """Spoken TTS for hosts without SAPI/Piper (Render Linux). Needs outbound HTTPS."""

    def supports_language(self, language: str) -> bool:
        return normalize_language(language) in EDGE_VOICES

    async def synthesize(self, text: str, language: str) -> bytes:
        import edge_tts

        lang = normalize_language(language)
        voice = EDGE_VOICES.get(lang)
        if voice is None:
            from services.ivr.tts import UnsupportedTtsLanguageError

            raise UnsupportedTtsLanguageError(lang)

        communicate = edge_tts.Communicate(text, voice)
        mp3_parts: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                mp3_parts.append(chunk["data"])
        mp3 = b"".join(mp3_parts)
        if not mp3:
            raise RuntimeError(f"Edge TTS returned no audio lang={lang}")
        return _mp3_to_mulaw(mp3)


def _mp3_to_mulaw(mp3: bytes) -> bytes:
    import miniaudio

    decoded = miniaudio.decode(
        mp3,
        nchannels=1,
        sample_rate=TWILIO_SAMPLE_RATE,
    )
    pcm = decoded.samples.tobytes()
    if decoded.nchannels > 1:
        import array

        samples = array.array("h")
        samples.frombytes(pcm)
        mono = array.array("h", (samples[i] for i in range(0, len(samples), decoded.nchannels)))
        pcm = mono.tobytes()
    return pcm16_to_mulaw(pcm)
