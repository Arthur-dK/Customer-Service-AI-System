"""Text-to-speech backends for IVR prompts (tone stub, Windows SAPI, optional Piper)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

from services.ivr.audio import TWILIO_SAMPLE_RATE, generate_tone_mulaw, pcm16_to_mulaw

logger = logging.getLogger(__name__)

DEFAULT_TTS_CACHE_DIR = Path(".cache") / "ivr-tts"


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
        key = (text, language.lower())
        cached = self._memory.get(key)
        if cached is not None:
            return cached

        disk_path = self._disk_path(key)
        if disk_path is not None and disk_path.exists():
            audio = disk_path.read_bytes()
            self._memory[key] = audio
            logger.info("TTS cache hit (disk) lang=%s bytes=%s", language, len(audio))
            return audio

        audio = await self.inner.synthesize(text, language)
        self._memory[key] = audio
        if disk_path is not None:
            disk_path.write_bytes(audio)
            logger.info("TTS cache store lang=%s bytes=%s path=%s", language, len(audio), disk_path)
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

    Each call spawns PowerShell (~2–3s). Prefer CachedTextToSpeech + warmup for
    static prompts; production should use Piper or pre-rendered assets.
    """

    def supports_language(self, language: str) -> bool:
        return language.lower().startswith("en")

    async def synthesize(self, text: str, language: str) -> bytes:
        return await asyncio.to_thread(self._synthesize_blocking, text)

    def _synthesize_blocking(self, text: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="ivr-sapi-") as tmp:
            wav_path = Path(tmp) / "out.wav"
            safe_text = text.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
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


class PiperTextToSpeech:
    """Local Piper TTS via CLI (optional). Expects a voice model path and piper binary."""

    def __init__(
        self,
        model_path: str,
        piper_bin: str | None = None,
        sample_rate: int = 22050,
    ) -> None:
        self.model_path = Path(model_path)
        self.piper_bin = piper_bin or shutil.which("piper") or "piper"
        self.sample_rate = sample_rate

    def supports_language(self, language: str) -> bool:
        return True

    async def synthesize(self, text: str, language: str) -> bytes:
        return await asyncio.to_thread(self._synthesize_blocking, text, language)

    def _synthesize_blocking(self, text: str, language: str) -> bytes:
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
    cache: bool = True,
    cache_dir: Path | None = DEFAULT_TTS_CACHE_DIR,
) -> TextToSpeech:
    """Prefer Piper if configured, else Windows SAPI, else tone stub. Cached by default."""
    if piper_model_path:
        model = Path(piper_model_path)
        if model.exists():
            logger.info("Using Piper TTS model at %s", model)
            inner: TextToSpeech = PiperTextToSpeech(model_path=str(model), piper_bin=piper_bin)
        else:
            logger.warning("Piper model path set but missing (%s)", model)
            inner = _fallback_tts()
    else:
        inner = _fallback_tts()

    if cache:
        return CachedTextToSpeech(inner, cache_dir=cache_dir)
    return inner


def _fallback_tts() -> TextToSpeech:
    if sys.platform.startswith("win"):
        logger.warning(
            "Using Windows SAPI TTS fallback (English-capable). "
            "Cold synth is ~2–3s via PowerShell; prompts are cached after first render. "
            "Set IVR_PIPER_MODEL_PATH for multilingual local Piper voices."
        )
        return WindowsSapiTextToSpeech()

    logger.warning(
        "No speech TTS configured — using tone stub. "
        "Callers will hear beeps, not words. Install Piper or use Windows SAPI."
    )
    return ToneTextToSpeech()


async def warm_language_selection_prompts(tts: TextToSpeech) -> int:
    """Pre-synthesize static language-selection prompts the backend can speak."""
    from core.language.countries import load_prompts

    warmed = 0
    for language, text in load_prompts().items():
        if not tts.supports_language(language):
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
