"""
Manual offline verification for Phase 4 TTS.

Synthesizes a spoken IVR prompt to 8 kHz mulaw, writes a playable WAV, and
optionally re-runs under a Windows SelectorEventLoop (uvicorn-like).

Usage (from repo root):
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_tts.py
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_tts.py --play
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_tts.py --backend tone --play
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ivr.audio import (  # noqa: E402
    mulaw_duration_ms,
    mulaw_to_pcm16,
    pcm16_rms,
    write_mulaw_as_wav,
)
from services.ivr.tts import (  # noqa: E402
    ToneTextToSpeech,
    WindowsSapiTextToSpeech,
    build_default_tts,
)

OUT_DIR = ROOT / "scratch" / "tts_verify"

# Change the text below to something you want to hear spoken. The default is a short
# prompt that should be audible and intelligible on all backends.
DEFAULT_TEXT = "This is a test of the IVR text-to-speech system. You should hear spoken words (SAPI) or a tone (tone backend)."


async def _synthesize(backend: str, text: str, language: str) -> tuple[str, bytes]:
    if backend == "tone":
        tts = ToneTextToSpeech()
        name = "ToneTextToSpeech"
    elif backend == "sapi":
        tts = WindowsSapiTextToSpeech()
        name = "WindowsSapiTextToSpeech"
    elif backend == "default":
        tts = build_default_tts()
        name = type(tts).__name__
    else:
        raise ValueError(f"Unknown backend: {backend}")
    audio = await tts.synthesize(text, language)
    return name, audio


def main() -> int:
    parser = argparse.ArgumentParser(description="Manually verify IVR TTS backends.")
    parser.add_argument("--play", action="store_true", help="Open the WAV (Windows).")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--backend",
        choices=("default", "sapi", "tone"),
        default="default",
        help="Which TTS backend to exercise.",
    )
    parser.add_argument(
        "--selector-loop",
        action="store_true",
        help="Force Windows SelectorEventLoop (uvicorn-like regression check).",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.selector_loop and sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        print("Using WindowsSelectorEventLoopPolicy")

    try:
        name, audio = asyncio.run(_synthesize(args.backend, args.text, args.language))
    except Exception as exc:
        print(f"FAILED: synthesize raised {type(exc).__name__}: {exc}")
        return 1

    wav_path = write_mulaw_as_wav(audio, OUT_DIR / f"tts_{args.backend}_{args.language}.wav")
    rms = pcm16_rms(mulaw_to_pcm16(audio))
    duration_ms = mulaw_duration_ms(audio)

    print("Phase 4 TTS verification (manual)")
    print(f"  backend        = {name}")
    print(f"  language       = {args.language}")
    print(f"  text           = {args.text!r}")
    print(f"  duration_ms    = {duration_ms:.1f}")
    print(f"  rms            = {rms:.2f}")
    print(f"  mulaw_bytes    = {len(audio)}")
    print(f"  wrote          = {wav_path}")

    checks = {
        "non_empty": len(audio) > 0,
        "duration_over_500ms": duration_ms > 500.0,
        "has_energy": rms > 500.0,
        "wav_exists": wav_path.exists(),
    }
    failed = [n for n, ok in checks.items() if not ok]
    for n, ok in checks.items():
        print(f"  check[{n}] = {'PASS' if ok else 'FAIL'}")

    if args.play and sys.platform.startswith("win"):
        import subprocess

        subprocess.run(["cmd", "/c", "start", "", str(wav_path)], check=False)
        print("  opened TTS WAV in default player")

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1

    print("All offline checks passed. You should hear spoken words (SAPI) or a tone (tone backend).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
