"""Text-to-speech backends for IVR prompts (tone stub, Windows SAPI, optional Piper)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from services.ivr.audio import TWILIO_SAMPLE_RATE, generate_tone_mulaw, pcm16_to_mulaw
from services.ivr.tts_lang import (
    language_from_piper_path,
    normalize_language,
    parse_piper_voice_map,
    piper_models_in_dir,
)

logger = logging.getLogger(__name__)

DEFAULT_TTS_CACHE_DIR = Path(".cache") / "ivr-tts"


class UnsupportedTtsLanguageError(RuntimeError):
    """No installed/configured voice can speak this language intelligibly."""


@dataclass(frozen=True)
class InstalledVoice:
    """One OS or engine voice tagged with an ISO language code."""

    name: str | None
    language: str
    culture: str = ""


_SAPI_VOICES_CACHE: list[InstalledVoice] | None = None


def list_installed_sapi_voices() -> list[InstalledVoice]:
    """Query Windows SAPI voices once per process. English-only if listing fails."""
    global _SAPI_VOICES_CACHE
    if _SAPI_VOICES_CACHE is not None:
        return _SAPI_VOICES_CACHE
    if not sys.platform.startswith("win"):
        _SAPI_VOICES_CACHE = []
        return _SAPI_VOICES_CACHE
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.GetInstalledVoices() | ForEach-Object { "
        "Write-Output ($_.VoiceInfo.Name + [char]9 + "
        "$_.VoiceInfo.Culture.TwoLetterISOLanguageName + [char]9 + "
        "$_.VoiceInfo.Culture.Name) "
        "}; "
        "$s.Dispose();"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.exception("Could not list Windows SAPI voices; assuming English only")
        _SAPI_VOICES_CACHE = [InstalledVoice(name=None, language="en", culture="en-US")]
        return _SAPI_VOICES_CACHE

    voices: list[InstalledVoice] = []
    for line in (completed.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name, iso = parts[0].strip(), parts[1].strip()
        culture = parts[2].strip() if len(parts) > 2 else ""
        lang = normalize_language(iso)
        if name and lang:
            voices.append(InstalledVoice(name=name, language=lang, culture=culture))
    if not voices:
        logger.warning("SAPI voice list empty; assuming default English voice")
        voices = [InstalledVoice(name=None, language="en", culture="en-US")]
    _SAPI_VOICES_CACHE = voices
    logger.info(
        "SAPI installed voices=%s",
        [(voice.language, voice.name) for voice in voices],
    )
    return voices


class TextToSpeech(Protocol):
    async def synthesize(self, text: str, language: str) -> bytes:
        """Return 8 kHz μ-law audio for Twilio Media Streams."""

    def supports_language(self, language: str) -> bool:
        """Return True when this backend can speak the language intelligibly."""
        ...


class CachedTextToSpeech:
    """Memory + optional disk cache around a TTS backend (static IVR prompts hit instantly)."""

    def __init__(self, inner: TextToSpeech, cache_dir: Path | None = DEFAULT_TTS_CACHE_DIR) -> None:
        self.inner = inner
        self._memory: dict[tuple[str, str], bytes] = {}
        self._cache_dir = cache_dir
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def supports_language(self, language: str) -> bool:
        return self.inner.supports_language(language)

    async def synthesize(self, text: str, language: str) -> bytes:
        lang = normalize_language(language)
        if not self.inner.supports_language(lang):
            raise UnsupportedTtsLanguageError(lang)
        key = (text, lang)
        cached = self._memory.get(key)
        if cached is not None:
            return cached

        disk_path = self._disk_path(key)
        if disk_path is not None and disk_path.exists():
            audio = disk_path.read_bytes()
            self._memory[key] = audio
            logger.info("TTS cache hit (disk) lang=%s bytes=%s", lang, len(audio))
            return audio

        audio = await self.inner.synthesize(text, lang)
        self._memory[key] = audio
        if disk_path is not None:
            disk_path.write_bytes(audio)
            logger.info("TTS cache store lang=%s bytes=%s path=%s", lang, len(audio), disk_path)
        return audio

    def _disk_path(self, key: tuple[str, str]) -> Path | None:
        if self._cache_dir is None:
            return None
        digest = hashlib.sha256(f"{key[1]}\n{key[0]}".encode("utf-8")).hexdigest()[:32]
        return self._cache_dir / f"{digest}.mulaw"


class ToneTextToSpeech:
    """Deterministic stub: tone length scales with text (no external model)."""

    def __init__(self, ms_per_char: int = 40, min_ms: int = 400, max_ms: int = 8000) -> None:
        self.ms_per_char = ms_per_char
        self.min_ms = min_ms
        self.max_ms = max_ms

    def supports_language(self, language: str) -> bool:
        # Tones are placeholders; keep requested language for metrics/tests.
        return True

    async def synthesize(self, text: str, language: str) -> bytes:
        duration = min(self.max_ms, max(self.min_ms, len(text) * self.ms_per_char))
        frequency = 350.0 + (sum(ord(ch) for ch in language) % 200)
        return generate_tone_mulaw(duration_ms=duration, frequency_hz=frequency, amplitude=0.35)


class WindowsSapiTextToSpeech:
    """
    Local Windows SAPI5 TTS via System.Speech.

    Uses a worker thread + subprocess.run so it works under uvicorn's Windows
    SelectorEventLoop (asyncio.create_subprocess_* often cannot).

    Speaks only languages that have an installed SAPI voice (often English only
    until extra speech packs are installed). Never uses an English voice for
    French (or other) text.
    """

    def __init__(self, voices: list[InstalledVoice] | None = None) -> None:
        if voices is not None:
            self.voices = list(voices)
        else:
            self.voices = list(list_installed_sapi_voices())

    def supports_language(self, language: str) -> bool:
        return self._voice_for(language) is not None

    def _voice_for(self, language: str) -> InstalledVoice | None:
        lang = normalize_language(language)
        if not lang:
            return None
        for voice in self.voices:
            if voice.language == lang:
                return voice
        return None

    async def synthesize(self, text: str, language: str) -> bytes:
        voice = self._voice_for(language)
        if voice is None:
            raise UnsupportedTtsLanguageError(normalize_language(language))
        return await asyncio.to_thread(self._synthesize_blocking, text, voice)

    def _synthesize_blocking(self, text: str, voice: InstalledVoice) -> bytes:
        with tempfile.TemporaryDirectory(prefix="ivr-sapi-") as tmp:
            wav_path = Path(tmp) / "out.wav"
            safe_text = text.replace("'", "''")
            select = ""
            if voice.name:
                safe_name = voice.name.replace("'", "''")
                select = f"$s.SelectVoice('{safe_name}'); "
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"{select}"
                f"$s.SetOutputToWaveFile('{wav_path.name}'); "
                f"$s.Speak('{safe_text}'); "
                "$s.Dispose();"
            )
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                cwd=tmp,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0 or not wav_path.exists():
                raise RuntimeError(
                    f"Windows SAPI TTS failed (code={completed.returncode}): "
                    f"{completed.stderr or completed.stdout}"
                )
            pcm, sample_rate = _wav_to_pcm16_with_rate(wav_path)
            return pcm16_to_mulaw(_downsample_pcm16(pcm, sample_rate, TWILIO_SAMPLE_RATE))


class RoutedTextToSpeech:
    """First backend that ``supports_language`` wins. Never cross-speak."""

    def __init__(self, backends: list[TextToSpeech]) -> None:
        if not backends:
            raise ValueError("RoutedTextToSpeech needs at least one backend")
        self.backends = backends

    def supports_language(self, language: str) -> bool:
        lang = normalize_language(language)
        return any(backend.supports_language(lang) for backend in self.backends)

    async def synthesize(self, text: str, language: str) -> bytes:
        lang = normalize_language(language)
        for backend in self.backends:
            if backend.supports_language(lang):
                return await backend.synthesize(text, lang)
        raise UnsupportedTtsLanguageError(lang)


class PiperTextToSpeech:
    """Local Piper TTS via CLI. One model speaks one language (from filename or ``language``)."""

    def __init__(
        self,
        model_path: str,
        piper_bin: str | None = None,
        sample_rate: int = 22050,
        language: str | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.piper_bin = piper_bin or shutil.which("piper") or "piper"
        self.sample_rate = sample_rate
        inferred = language_from_piper_path(self.model_path)
        self.language = normalize_language(language or inferred or "en")

    def supports_language(self, language: str) -> bool:
        return normalize_language(language) == self.language

    async def synthesize(self, text: str, language: str) -> bytes:
        return await asyncio.to_thread(self._synthesize_blocking, text, language)

    def _synthesize_blocking(self, text: str, language: str) -> bytes:
        if normalize_language(language) != self.language:
            raise UnsupportedTtsLanguageError(normalize_language(language))
        if not self.model_path.exists():
            raise FileNotFoundError(f"Piper model not found: {self.model_path}")

        with tempfile.TemporaryDirectory(prefix="ivr-piper-") as tmp:
            wav_path = Path(tmp) / "out.wav"
            completed = subprocess.run(
                [
                    self.piper_bin,
                    "--model",
                    str(self.model_path),
                    "--output_file",
                    str(wav_path),
                ],
                input=text,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"Piper failed (lang={language}): {completed.stderr or completed.stdout}"
                )
            pcm, sample_rate = _wav_to_pcm16_with_rate(wav_path)
            return pcm16_to_mulaw(
                _downsample_pcm16(pcm, sample_rate or self.sample_rate, TWILIO_SAMPLE_RATE)
            )


def build_default_tts(
    piper_model_path: str | None = None,
    piper_bin: str | None = None,
    *,
    piper_voices: str | None = None,
    piper_voice_dir: str | None = None,
    use_edge: bool | None = None,
    cache: bool = True,
    cache_dir: Path | None = DEFAULT_TTS_CACHE_DIR,
) -> TextToSpeech:
    """Route each language to a matching Piper model and/or SAPI voice. Cached by default."""
    backends: list[TextToSpeech] = []
    backends.extend(
        _piper_backends(
            model_path=piper_model_path,
            piper_bin=piper_bin,
            voices_json=piper_voices,
            voice_dir=piper_voice_dir,
        )
    )
    if sys.platform.startswith("win"):
        sapi = WindowsSapiTextToSpeech()
        spoken = sorted({voice.language for voice in sapi.voices})
        logger.info(
            "Windows SAPI voices languages=%s. "
            "Cold synth is ~2–3s via PowerShell; prompts are cached after first render. "
            "Install a Windows speech pack or add Piper models for other languages.",
            spoken,
        )
        backends.append(sapi)
    want_edge = (not sys.platform.startswith("win")) if use_edge is None else bool(use_edge)
    if want_edge:
        from services.ivr.edge_tts import EdgeTextToSpeech, edge_tts_available

        if edge_tts_available():
            logger.info("Using Edge neural TTS (network) for languages without a local voice")
            backends.append(EdgeTextToSpeech())
        else:
            logger.warning(
                "IVR_USE_EDGE_TTS requested but edge-tts/miniaudio are not installed; "
                "Linux hosts will use tones. pip install edge-tts miniaudio"
            )
    if not backends:
        logger.warning(
            "No speech TTS configured — using tone stub. "
            "Callers will hear beeps, not words. Install Piper or use Windows SAPI."
        )
        backends.append(ToneTextToSpeech())

    inner: TextToSpeech = backends[0] if len(backends) == 1 else RoutedTextToSpeech(backends)
    if cache:
        return CachedTextToSpeech(inner, cache_dir=cache_dir)
    return inner


def _piper_backends(
    *,
    model_path: str | None,
    piper_bin: str | None,
    voices_json: str | None,
    voice_dir: str | None,
) -> list[PiperTextToSpeech]:
    by_lang: dict[str, PiperTextToSpeech] = {}
    try:
        mapped = parse_piper_voice_map(voices_json)
    except (json.JSONDecodeError, ValueError):
        logger.exception("Invalid IVR_PIPER_VOICES JSON; ignoring")
        mapped = {}
    for lang, path in mapped.items():
        model = Path(path)
        if not model.exists():
            logger.warning("Piper voice missing lang=%s path=%s", lang, model)
            continue
        by_lang[lang] = PiperTextToSpeech(
            model_path=str(model), piper_bin=piper_bin, language=lang
        )
        logger.info("Piper voice lang=%s path=%s", lang, model)

    if voice_dir:
        directory = Path(voice_dir)
        for lang, model in piper_models_in_dir(directory).items():
            if lang in by_lang:
                continue
            by_lang[lang] = PiperTextToSpeech(
                model_path=str(model), piper_bin=piper_bin, language=lang
            )
            logger.info("Piper voice (dir) lang=%s path=%s", lang, model)
        if not directory.is_dir():
            logger.warning("IVR_PIPER_VOICE_DIR is not a directory: %s", directory)

    if model_path:
        model = Path(model_path)
        if model.exists():
            lang = language_from_piper_path(model) or "en"
            if lang not in by_lang:
                by_lang[lang] = PiperTextToSpeech(
                    model_path=str(model), piper_bin=piper_bin, language=lang
                )
                logger.info("Piper voice (IVR_PIPER_MODEL_PATH) lang=%s path=%s", lang, model)
        else:
            logger.warning("Piper model path set but missing (%s)", model)

    return list(by_lang.values())


def list_spoken_languages(tts: TextToSpeech) -> tuple[str, ...]:
    """Languages this stack can speak intelligibly (unwrap cache/router)."""
    inner = tts.inner if isinstance(tts, CachedTextToSpeech) else tts
    if isinstance(inner, ToneTextToSpeech):
        return ("*",)
    if isinstance(inner, RoutedTextToSpeech):
        langs: set[str] = set()
        for backend in inner.backends:
            langs.update(list_spoken_languages(backend))
        return tuple(sorted(lang for lang in langs if lang != "*"))
    if isinstance(inner, WindowsSapiTextToSpeech):
        return tuple(sorted({voice.language for voice in inner.voices}))
    if isinstance(inner, PiperTextToSpeech):
        return (inner.language,)
    from services.ivr.edge_tts import EDGE_VOICES, EdgeTextToSpeech

    if isinstance(inner, EdgeTextToSpeech):
        return tuple(sorted(EDGE_VOICES))
    return tuple()


async def warm_language_selection_prompts(
    tts: TextToSpeech,
    languages: tuple[str, ...] | None = None,
) -> int:
    """Pre-synthesize the usual IVR prompt languages (not every file in prompts.json)."""
    from core.language.countries import load_prompts
    from core.language.phrases import load_phrase_catalog

    prompts = load_prompts()
    langs = languages if languages is not None else load_phrase_catalog().warmup_languages
    warmed = 0
    for language in langs:
        text = prompts.get(language)
        if not text or not tts.supports_language(language):
            continue
        await tts.synthesize(text, language)
        warmed += 1
    logger.info("Warmed %s language-selection TTS prompt(s)", warmed)
    return warmed


def _wav_to_pcm16_with_rate(path: Path) -> tuple[bytes, int]:
    import wave

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if sample_width != 2:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")
    if channels == 1:
        return frames, sample_rate
    import array

    samples = array.array("h")
    samples.frombytes(frames)
    mono = array.array("h", (samples[i] for i in range(0, len(samples), channels)))
    return mono.tobytes(), sample_rate


def _downsample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate:
        return pcm
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    duration = samples.size / float(src_rate)
    dst_len = max(1, int(duration * dst_rate))
    x_old = np.linspace(0.0, 1.0, num=samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=dst_len, endpoint=False)
    resampled = np.interp(x_new, x_old, samples)
    return resampled.astype(np.int16).tobytes()
