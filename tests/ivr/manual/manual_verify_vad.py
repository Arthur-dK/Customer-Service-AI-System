"""
Manual offline verification for Phase 3 energy VAD.

Builds a short spoken word surrounded by long silence, runs EnergyVad on 20ms
frames, and writes before/after WAVs so the trim is obvious by ear.

Usage (from repo root):
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_vad.py
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_vad.py --play
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ivr.audio import (  # noqa: E402
    TWILIO_SAMPLE_RATE,
    chunk_mulaw,
    generate_silence_mulaw,
    mulaw_duration_ms,
    pcm16_rms,
    write_mulaw_as_wav,
)
from services.ivr.vad import EnergyVad, VadConfig  # noqa: E402

_audio_verify_path = Path(__file__).resolve().parent / "manual_verify_audio.py"
_spec = importlib.util.spec_from_file_location("manual_verify_audio", _audio_verify_path)
assert _spec and _spec.loader
_audio_verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_audio_verify)
speech_through_mulaw_pipeline = _audio_verify.speech_through_mulaw_pipeline

OUT_DIR = ROOT / "scratch" / "vad_verify"
# Short word + long silence makes the trim unmistakable.
DEFAULT_PHRASE = "Hello"
LEADING_SILENCE_MS = 3000
TRAILING_SILENCE_MS = 3000


def _write_pcm16_wav(pcm16: bytes, path: Path, sample_rate: int = TWILIO_SAMPLE_RATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16)
    return path


def _pcm_duration_ms(pcm16: bytes, sample_rate: int = TWILIO_SAMPLE_RATE) -> float:
    return 1000.0 * (len(pcm16) / 2) / float(sample_rate)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manually verify IVR energy VAD.")
    parser.add_argument(
        "--play",
        action="store_true",
        help="Play padded input first, then the trimmed capture (Windows).",
    )
    parser.add_argument("--phrase", default=DEFAULT_PHRASE)
    parser.add_argument("--rms-threshold", type=float, default=500.0)
    parser.add_argument("--lead-ms", type=int, default=LEADING_SILENCE_MS)
    parser.add_argument("--trail-ms", type=int, default=TRAILING_SILENCE_MS)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    original_wav = OUT_DIR / "source_speech_high_quality.wav"
    speech_mulaw = speech_through_mulaw_pipeline(args.phrase, original_wav)
    padded = (
        generate_silence_mulaw(args.lead_ms)
        + speech_mulaw
        + generate_silence_mulaw(args.trail_ms)
    )
    padded_wav = write_mulaw_as_wav(padded, OUT_DIR / "padded_with_long_silence.wav")

    vad = EnergyVad(
        VadConfig(
            rms_threshold=args.rms_threshold,
            speech_start_ms=120,
            speech_end_ms=400,
        )
    )

    timeline: list[tuple[float, str, int]] = []
    captured: list[bytes] = []
    elapsed_ms = 0.0

    for chunk in chunk_mulaw(padded, chunk_ms=20):
        for event in vad.process_mulaw(chunk):
            timeline.append((elapsed_ms, event.kind, len(event.audio_pcm16)))
            if event.kind == "speech_end":
                captured.append(event.audio_pcm16)
        elapsed_ms += mulaw_duration_ms(chunk)

    padded_ms = mulaw_duration_ms(padded)
    speech_only_ms = mulaw_duration_ms(speech_mulaw)
    captured_ms = _pcm_duration_ms(captured[0]) if captured else 0.0

    print("Phase 3 VAD verification (manual)")
    print(f"  phrase              = {args.phrase!r}")
    print(f"  lead_silence_ms     = {args.lead_ms}")
    print(f"  trail_silence_ms    = {args.trail_ms}")
    print(f"  rms_threshold       = {args.rms_threshold}")
    print(f"  padded_duration_ms  = {padded_ms:.1f}   <-- play this: long quiet, short word")
    print(f"  speech_only_ms      = {speech_only_ms:.1f}")
    print(f"  captured_duration_ms= {captured_ms:.1f}   <-- play this: mostly just the word")
    if padded_ms > 0:
        print(f"  removed_by_vad_ms   = {padded_ms - captured_ms:.1f}")
        print(f"  kept_fraction       = {captured_ms / padded_ms:.1%}")
    print(f"  events              = {len(timeline)}")
    for t_ms, kind, nbytes in timeline:
        print(f"    t={t_ms:7.1f} ms  {kind:<12}  pcm_bytes={nbytes}")

    checks = {
        "has_speech_start": any(kind == "speech_start" for _, kind, _ in timeline),
        "has_speech_end": any(kind == "speech_end" for _, kind, _ in timeline),
        "single_utterance": len(captured) == 1,
        "captured_has_energy": bool(captured) and pcm16_rms(captured[0]) > 500.0,
        "captured_much_shorter": bool(captured) and captured_ms < padded_ms * 0.5,
        "padding_is_long": padded_ms >= 5000.0,
    }

    captured_wav = None
    if captured:
        captured_wav = _write_pcm16_wav(captured[0], OUT_DIR / "vad_captured_utterance.wav")
        print(f"  wrote               = {padded_wav}")
        print(f"  wrote               = {captured_wav}")
    print(f"  wrote               = {original_wav}")

    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"  check[{name}] = {'PASS' if ok else 'FAIL'}")

    if args.play and captured_wav is not None and sys.platform.startswith("win"):
        subprocess.run(["cmd", "/c", "start", "", str(padded_wav)], check=False)
        print("  playing padded file (expect ~3s silence, then Hello, then ~3s silence)...")
        time.sleep(min(8.0, padded_ms / 1000.0 + 0.5))
        subprocess.run(["cmd", "/c", "start", "", str(captured_wav)], check=False)
        print("  playing trimmed capture (expect mostly just 'Hello')...")

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1

    print(
        "All offline checks passed. Compare padded_with_long_silence.wav vs "
        "vad_captured_utterance.wav — the duration difference should be obvious."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
