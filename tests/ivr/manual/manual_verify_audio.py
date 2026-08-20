"""
Manual offline verification for Phase 2 μ-law audio helpers.

Not collected by pytest. Run it yourself to listen to real speech after it
has been encoded to Twilio μ-law and decoded back to WAV.

Usage (from repo root):
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_audio.py
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_audio.py --play
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ivr.audio import (  # noqa: E402
    TWILIO_SAMPLE_RATE,
    chunk_mulaw,
    generate_silence_mulaw,
    generate_tone_mulaw,
    mulaw_duration_ms,
    mulaw_to_pcm16,
    pcm16_rms,
    pcm16_to_mulaw,
    write_mulaw_as_wav,
)

OUT_DIR = ROOT / "scratch" / "audio_verify"
DEFAULT_PHRASE = "Please say the name of the language you would like to use."


def _synthesize_speech_wav(text: str, wav_path: Path) -> None:
    """Use Windows SAPI to produce a normal spoken WAV (verification only)."""
    if not sys.platform.startswith("win"):
        raise RuntimeError("Speech sample generation is currently Windows-only (SAPI).")

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
        cwd=str(wav_path.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not wav_path.exists():
        raise RuntimeError(
            f"SAPI speech synthesis failed: {completed.stderr or completed.stdout}"
        )


def _read_wav_pcm16(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"Expected 16-bit PCM WAV, got sample width {width}")
    if channels == 1:
        return frames, rate

    samples = np.frombuffer(frames, dtype=np.int16)
    mono = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return mono.tobytes(), rate


def _resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    duration = samples.size / float(src_rate)
    dst_len = max(1, int(duration * dst_rate))
    x_old = np.linspace(0.0, 1.0, num=samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=dst_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.int16).tobytes()


def speech_through_mulaw_pipeline(text: str, original_wav_out: Path) -> bytes:
    """Speak text, save original WAV, encode to 8 kHz μ-law via our helpers."""
    with tempfile.TemporaryDirectory(prefix="ivr-speech-verify-") as tmp:
        raw_wav = Path(tmp) / "sapi.wav"
        _synthesize_speech_wav(text, raw_wav)
        # Keep a copy of the untouched SAPI output (typically 22.05/44.1 kHz PCM).
        original_wav_out.parent.mkdir(parents=True, exist_ok=True)
        original_wav_out.write_bytes(raw_wav.read_bytes())
        pcm, rate = _read_wav_pcm16(raw_wav)
        pcm_8k = _resample_pcm16(pcm, rate, TWILIO_SAMPLE_RATE)
        return pcm16_to_mulaw(pcm_8k)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manually verify IVR μ-law helpers with spoken audio."
    )
    parser.add_argument(
        "--play",
        action="store_true",
        help="Open the original high-quality WAV in the OS default player (Windows).",
    )
    parser.add_argument(
        "--phrase",
        default=DEFAULT_PHRASE,
        help="Spoken phrase used for the human-voice check.",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    silence = generate_silence_mulaw(500)
    tone = generate_tone_mulaw(1000, frequency_hz=440, amplitude=0.35)
    original_wav = OUT_DIR / "speech_original_high_quality.wav"
    speech = speech_through_mulaw_pipeline(args.phrase, original_wav)
    chunks = chunk_mulaw(speech, chunk_ms=20)

    silence_wav = write_mulaw_as_wav(silence, OUT_DIR / "silence_500ms.wav")
    tone_wav = write_mulaw_as_wav(tone, OUT_DIR / "tone_440hz_1s.wav")
    speech_wav = write_mulaw_as_wav(speech, OUT_DIR / "speech_after_mulaw_roundtrip.wav")

    silence_rms = pcm16_rms(mulaw_to_pcm16(silence))
    tone_rms = pcm16_rms(mulaw_to_pcm16(tone))
    speech_rms = pcm16_rms(mulaw_to_pcm16(speech))
    speech_ms = mulaw_duration_ms(speech)

    print("Phase 2 audio verification (manual)")
    print(f"  sample_rate_hz     = {TWILIO_SAMPLE_RATE}")
    print(f"  phrase             = {args.phrase!r}")
    print(f"  silence_rms        = {silence_rms:.2f} (expect near 0)")
    print(f"  tone_rms           = {tone_rms:.2f}")
    print(f"  speech_duration    = {speech_ms:.1f} ms")
    print(f"  speech_rms         = {speech_rms:.2f} (expect >> silence)")
    print(f"  speech_20ms_chunks = {len(chunks)}")
    print(f"  wrote              = {silence_wav}")
    print(f"  wrote              = {tone_wav}")
    print(f"  wrote              = {original_wav}  <-- listen to this for normal quality")
    print(f"  wrote              = {speech_wav}  <-- telephone quality after mulaw")

    checks = {
        "silence_low_energy": silence_rms < 50.0,
        "tone_high_energy": tone_rms > 1000.0,
        "speech_high_energy": speech_rms > 500.0,
        "speech_long_enough": speech_ms > 500.0,
        "wav_files_exist": (
            silence_wav.exists()
            and tone_wav.exists()
            and speech_wav.exists()
            and original_wav.exists()
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"  check[{name}] = {'PASS' if ok else 'FAIL'}")

    if args.play:
        if sys.platform.startswith("win"):
            subprocess.run(["cmd", "/c", "start", "", str(original_wav)], check=False)
            print("  opened original high-quality WAV in default player")
        else:
            print("  --play is only wired for Windows in this script")

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1

    print(
        "Compare speech_original_high_quality.wav (normal) vs "
        "speech_after_mulaw_roundtrip.wav (expected phone quality)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
