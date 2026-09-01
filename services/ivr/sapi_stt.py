"""Windows SAPI command-and-control STT (free, local, no cloud).

Buffers inbound μ-law until VAD ``speech_end``, then recognizes against a small
grammar (balance / PIN / block / goodbye). Full dictation and Deepgram remain
later constructor swaps on ``StreamingSpeechToText``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import tempfile
import time
import wave
from collections.abc import Callable
from pathlib import Path

from services.ivr.audio import TWILIO_SAMPLE_RATE, mulaw_to_pcm16, resample_pcm16
from services.ivr.placeholder_intents import grammar_phrases
from services.ivr.streaming_stt import Transcript
from services.ivr.tts_lang import normalize_language

logger = logging.getLogger(__name__)

_RECOGNIZER_CULTURE = {
    "en": "en-US",
    "fr": "fr-FR",
    "es": "es-ES",
    "de": "de-DE",
    "it": "it-IT",
    "pt": "pt-BR",
    "nl": "nl-NL",
    "ja": "ja-JP",
    "zh": "zh-CN",
    "ar": "ar-SA",
    "hi": "hi-IN",
    "pl": "pl-PL",
    "ru": "ru-RU",
    "ko": "ko-KR",
    "tr": "tr-TR",
    "sv": "sv-SE",
    "da": "da-DK",
    "fi": "fi-FI",
    "no": "nb-NO",
    "cs": "cs-CZ",
    "el": "el-GR",
    "he": "he-IL",
    "th": "th-TH",
    "vi": "vi-VN",
    "id": "id-ID",
    "ro": "ro-RO",
    "hu": "hu-HU",
    "uk": "uk-UA",
}

RecognizeFn = Callable[[bytes, str], str]


class GrammarStreamingSpeechToText:
    """Free live STT: inspects audio; Windows SAPI grammar (or an injected recognizer)."""

    def __init__(self, recognize: RecognizeFn | None = None) -> None:
        self._recognize = recognize if recognize is not None else sapi_grammar_recognize
        self._language = "en"
        self._buffer = bytearray()
        self._bytes_fed = 0

    @property
    def bytes_fed(self) -> int:
        return self._bytes_fed

    def supports_language(self, language: str) -> bool:
        return True

    async def start(self, *, language: str) -> None:
        self._language = normalize_language(language) or "en"
        self._buffer.clear()
        self._bytes_fed = 0

    async def feed_mulaw(self, chunk: bytes) -> list[Transcript]:
        self._buffer.extend(chunk)
        self._bytes_fed += len(chunk)
        return []

    async def finish(self) -> Transcript | None:
        mulaw = bytes(self._buffer)
        self._buffer.clear()
        started = time.perf_counter()
        text = await asyncio.to_thread(self._recognize, mulaw, self._language)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        cleaned = (text or "").strip()
        logger.info(
            "grammar_stt language=%s chars=%s stt_ms=%.1f text=%r",
            self._language,
            len(cleaned),
            elapsed_ms,
            cleaned,
        )
        return Transcript(text=cleaned, is_final=True, language=self._language)

    async def aclose(self) -> None:
        self._buffer.clear()


def recognizer_culture(language: str) -> str:
    lang = normalize_language(language) or "en"
    return _RECOGNIZER_CULTURE.get(lang, "en-US")


def sapi_grammar_recognize(mulaw: bytes, language: str) -> str:
    """Recognize one utterance. Empty string if SAPI is missing or hears nothing."""
    if not mulaw or not sys.platform.startswith("win"):
        return ""
    phrases = grammar_phrases(language)
    culture = recognizer_culture(language)
    with tempfile.TemporaryDirectory(prefix="ivr-sapi-stt-") as tmp:
        wav_path = Path(tmp) / "utterance.wav"
        _write_16k_pcm_wav(mulaw, wav_path)
        script = _recognition_script(wav_path.name, culture, phrases)
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("SAPI grammar STT failed to run")
            return ""
        if completed.returncode != 0:
            logger.warning(
                "SAPI grammar STT exit=%s stderr=%s",
                completed.returncode,
                (completed.stderr or completed.stdout or "")[:400],
            )
            return ""
        return (completed.stdout or "").strip()


def _write_16k_pcm_wav(mulaw: bytes, path: Path) -> None:
    pcm = resample_pcm16(mulaw_to_pcm16(mulaw), TWILIO_SAMPLE_RATE, 16000)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm)


def _recognition_script(wav_name: str, culture: str, phrases: tuple[str, ...]) -> str:
    payload = json.dumps(list(phrases), ensure_ascii=True).replace("'", "''")
    safe_culture = culture.replace("'", "''")
    safe_wav = wav_name.replace("'", "''")
    return (
        "Add-Type -AssemblyName System.Speech; "
        f"$phrases = ConvertFrom-Json '{payload}'; "
        "try { "
        f"$culture = [Globalization.CultureInfo]::GetCultureInfo('{safe_culture}'); "
        "} catch { "
        "$culture = [Globalization.CultureInfo]::GetCultureInfo('en-US'); "
        "} "
        "try { "
        "$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine $culture; "
        "} catch { "
        "$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine "
        "([Globalization.CultureInfo]::GetCultureInfo('en-US')); "
        "} "
        "$choices = New-Object System.Speech.Recognition.Choices; "
        "foreach ($p in $phrases) { [void]$choices.Add([string]$p) }; "
        "$builder = New-Object System.Speech.Recognition.GrammarBuilder $choices; "
        "$builder.Culture = $engine.RecognizerInfo.Culture; "
        "$engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar $builder)); "
        f"$engine.SetInputToWaveFile('{safe_wav}'); "
        "$result = $engine.Recognize([TimeSpan]::FromSeconds(8)); "
        "if ($result -ne $null) { Write-Output $result.Text }; "
        "$engine.Dispose();"
    )
