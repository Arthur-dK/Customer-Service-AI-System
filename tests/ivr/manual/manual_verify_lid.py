"""
Manual offline wiring check for IVR language ID (Phases 5 / 10).

Proves Fixed LID or SpeechBrain load + a usable result on English/fixture audio.
Multilingual acceptance is a live phone call: speak French (or another language)
yourself — see docs/features/FEAT-02.md (Phase 10 live SpeechBrain).

Usage (from repo root):
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_lid.py
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_lid.py --force-language he
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_lid.py --try-speechbrain
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ivr.audio import (  # noqa: E402
    generate_silence_mulaw,
    generate_tone_mulaw,
    mulaw_to_pcm16,
    pcm16_rms,
)
from services.ivr.lid import (  # noqa: E402
    FixedLanguageIdentifier,
    SpeechBrainLanguageIdentifier,
    build_default_lid,
    speechbrain_available,
)

OUT_DIR = ROOT / "scratch" / "lid_verify"
DEFAULT_PHRASE = "Please say the name of the language you would like to use."


def _load_speech_helper():
    path = Path(__file__).resolve().parent / "manual_verify_audio.py"
    spec = importlib.util.spec_from_file_location("manual_verify_audio", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.speech_through_mulaw_pipeline


async def _run(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.source == "tone":
        pcm = mulaw_to_pcm16(generate_tone_mulaw(800, amplitude=0.4))
        source_desc = "synthetic_tone_800ms"
    else:
        speech_helper = _load_speech_helper()
        original = OUT_DIR / "lid_source_speech_high_quality.wav"
        mulaw = speech_helper(args.phrase, original)
        pcm = mulaw_to_pcm16(generate_silence_mulaw(100) + mulaw + generate_silence_mulaw(100))
        source_desc = f"speech:{args.phrase!r}"

    if args.try_speechbrain:
        if not speechbrain_available():
            print(
                "SpeechBrain/torch not importable. Install with:\n"
                "  .\\venv\\Scripts\\python.exe -m pip install -r requirements-ivr-lid.txt"
            )
            return 2
        try:
            lid = SpeechBrainLanguageIdentifier()
            backend_choice = "speechbrain"
        except Exception as exc:
            print(f"SpeechBrain model load failed ({exc})")
            print("Omit --try-speechbrain to test Fixed LID only.")
            return 2
    elif args.force_language:
        lid = FixedLanguageIdentifier(language=args.force_language)
        backend_choice = "fixed-forced"
    else:
        lid = build_default_lid(
            prefer_speechbrain=False,
            force_language=None,
        )
        backend_choice = "build_default_fixed"

    result = await lid.identify(pcm)

    print("LID verification (manual / offline wiring)")
    print(f"  source           = {source_desc}")
    print(f"  pcm_bytes        = {len(pcm)}")
    print(f"  pcm_rms          = {pcm16_rms(pcm):.2f}")
    print(f"  backend_choice   = {backend_choice}")
    print(f"  speechbrain_pip  = {speechbrain_available()}")
    if result is None:
        print("  result           = None")
        print("FAILED: no language returned")
        return 1

    print(f"  language         = {result.language}")
    print(f"  confidence       = {result.confidence:.3f}")
    print(f"  latency_ms       = {result.latency_ms:.1f}")
    print(f"  result_backend   = {result.backend}")

    checks = {
        "has_language": bool(result.language),
        "confidence_usable": result.confidence >= 0.15,
        "has_audio_energy": pcm16_rms(pcm) > 100.0,
    }
    if backend_choice == "speechbrain":
        checks["backend_is_speechbrain"] = result.backend == "speechbrain"

    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"  check[{name}] = {'PASS' if ok else 'FAIL'}")

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1

    if backend_choice == "speechbrain":
        print("Offline SpeechBrain wiring OK.")
        print(
            "Multilingual proof: dial the IVR and speak French (or another language) "
            "on the call — docs/features/FEAT-02.md (Phase 10 live SpeechBrain)"
        )
    else:
        print(
            "Fixed LID checks passed. For SpeechBrain wiring run with --try-speechbrain; "
            "for multilingual proof use a live call (see docs)."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline LID wiring check (live speech is the multilingual test)."
    )
    parser.add_argument("--phrase", default=DEFAULT_PHRASE)
    parser.add_argument("--force-language", default=None, help="Fixed LID language override")
    parser.add_argument(
        "--source",
        choices=("speech", "tone"),
        default="speech",
        help="Audio fixture: English SAPI phrase or synthetic tone",
    )
    parser.add_argument(
        "--try-speechbrain",
        action="store_true",
        help="Load SpeechBrain VoxLingua107 if installed (wiring only)",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
