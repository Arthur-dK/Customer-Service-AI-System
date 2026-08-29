"""
Readiness check for IVR live-call profiles.

Does not place a call. Default checks Phase 10 (SpeechBrain). Use --phase 7
for FEAT-03 canned-reply smoke, --phase 7b for TTS voice matching, --phase 8
for live grammar STT, or --phase 9 for the fixed-LID profile.

Usage (from repo root):
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_smoke.py --phase 7
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_smoke.py --phase 7b
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_smoke.py --phase 8
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_smoke.py --phase 9
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.language.phrases import MAIN_MENU, PLACEHOLDER_BALANCE, load_phrase_catalog  # noqa: E402
from core.config import settings  # noqa: E402
from services.ivr.lid import (  # noqa: E402
    FixedLanguageIdentifier,
    SpeechBrainLanguageIdentifier,
    build_default_lid,
    speechbrain_available,
)
from services.ivr.streaming_stt import parse_stt_script  # noqa: E402
from services.ivr.tts import (  # noqa: E402
    CachedTextToSpeech,
    RoutedTextToSpeech,
    ToneTextToSpeech,
    build_default_tts,
    list_spoken_languages,
)


def _ok(label: str, condition: bool, detail: str = "") -> bool:
    mark = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    return condition


def _tts_backend_name(tts: object) -> str:
    if isinstance(tts, CachedTextToSpeech):
        return f"Cached({_tts_backend_name(tts.inner)})"
    if isinstance(tts, RoutedTextToSpeech):
        inner = ", ".join(type(backend).__name__ for backend in tts.backends)
        return f"Routed({inner})"
    return type(tts).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description="IVR live-call profile readiness")
    parser.add_argument("--phase", choices=("7", "7b", "8", "9", "10"), default="10")
    args = parser.parse_args()
    phase_label = args.phase
    phase = 7 if phase_label in ("7", "7b") else int(phase_label)

    print(f"Phase {phase_label} live profile readiness\n")

    tts = build_default_tts(
        piper_model_path=settings.IVR_PIPER_MODEL_PATH,
        piper_bin=settings.IVR_PIPER_BIN,
        piper_voices=settings.IVR_PIPER_VOICES,
        piper_voice_dir=settings.IVR_PIPER_VOICE_DIR,
    )
    lid = build_default_lid(
        prefer_speechbrain=settings.IVR_USE_SPEECHBRAIN_LID,
        force_language=settings.IVR_LID_FORCE_LANGUAGE,
        speechbrain_model=settings.IVR_SPEECHBRAIN_MODEL,
    )

    checks: list[bool] = []
    print("Settings")
    if phase == 9:
        checks.append(
            _ok(
                "SpeechBrain LID disabled",
                settings.IVR_USE_SPEECHBRAIN_LID is False,
                f"IVR_USE_SPEECHBRAIN_LID={settings.IVR_USE_SPEECHBRAIN_LID}",
            )
        )
        checks.append(
            _ok(
                "Fixed LID language set",
                bool(settings.IVR_LID_FORCE_LANGUAGE),
                f"IVR_LID_FORCE_LANGUAGE={settings.IVR_LID_FORCE_LANGUAGE!r}",
            )
        )
    elif phase == 10:
        checks.append(
            _ok(
                "SpeechBrain LID preferred",
                settings.IVR_USE_SPEECHBRAIN_LID is True,
                f"IVR_USE_SPEECHBRAIN_LID={settings.IVR_USE_SPEECHBRAIN_LID}",
            )
        )
        checks.append(
            _ok(
                "No fixed-language override",
                not settings.IVR_LID_FORCE_LANGUAGE,
                f"IVR_LID_FORCE_LANGUAGE={settings.IVR_LID_FORCE_LANGUAGE!r}",
            )
        )
        checks.append(
            _ok(
                "speechbrain+torch importable",
                speechbrain_available(),
                "install requirements-ivr-lid.txt" if not speechbrain_available() else "ok",
            )
        )
    else:
        catalog = load_phrase_catalog()
        checks.append(
            _ok(
                "English canned menu + balance",
                catalog.has(MAIN_MENU, "en") and catalog.has(PLACEHOLDER_BALANCE, "en"),
            )
        )
        checks.append(
            _ok(
                "French canned menu + balance (2nd language)",
                catalog.has(MAIN_MENU, "fr") and catalog.has(PLACEHOLDER_BALANCE, "fr"),
            )
        )
        script = parse_stt_script(settings.IVR_STT_SCRIPT)
        if phase == 8:
            checks.append(
                _ok(
                    "IVR_STT_BACKEND=sapi (hears balance/PIN/block/goodbye)",
                    (settings.IVR_STT_BACKEND or "").strip().lower() == "sapi",
                    f"IVR_STT_BACKEND={settings.IVR_STT_BACKEND!r}",
                )
            )
            checks.append(
                _ok(
                    "Windows host for SAPI grammar STT",
                    sys.platform.startswith("win"),
                    sys.platform,
                )
            )
        elif script:
            checks.append(_ok("IVR_STT_SCRIPT queued for live turns", True, ",".join(script)))
        else:
            print(
                "  [WARN] IVR_STT_SCRIPT empty — after the menu, speech plays "
                "'did not catch that' (still a canned TTFB check). "
                "Set IVR_STT_SCRIPT=balance to hear the fake-balance line, "
                "or IVR_STT_BACKEND=sapi for Phase 8."
            )

    checks.append(
        _ok(
            "Burst playback (smooth on Twilio)",
            settings.IVR_PLAYBACK_REALTIME is False,
            f"IVR_PLAYBACK_REALTIME={settings.IVR_PLAYBACK_REALTIME}",
        )
    )
    checks.append(
        _ok(
            "DEBUG logging on",
            settings.DEBUG is True,
            f"DEBUG={settings.DEBUG}",
        )
    )

    print("\nBackends")
    tts_name = _tts_backend_name(tts)
    inner = tts.inner if isinstance(tts, CachedTextToSpeech) else tts
    spoken = list_spoken_languages(tts)
    if isinstance(inner, ToneTextToSpeech):
        checks.append(
            _ok(
                "TTS is spoken (not tone stub)",
                False,
                f"{tts_name} — callers will hear beeps only",
            )
        )
    else:
        checks.append(
            _ok("TTS is spoken (not tone stub)", True, f"{tts_name} languages={spoken}")
        )
    if phase_label == "7b" and "*" not in spoken and "fr" not in spoken:
        print(
            "  [WARN] No French voice — selecting French plays English until you add "
            "a Windows French speech pack or Piper fr_*.onnx"
        )

    lid_name = type(lid).__name__
    if phase == 9:
        checks.append(_ok("LID is fixed override", isinstance(lid, FixedLanguageIdentifier), lid_name))
    elif phase == 10:
        checks.append(
            _ok(
                "LID is SpeechBrain",
                isinstance(lid, SpeechBrainLanguageIdentifier),
                lid_name
                + (
                    " — falling back until deps install"
                    if isinstance(lid, FixedLanguageIdentifier)
                    else ""
                ),
            )
        )
    else:
        checks.append(_ok("LID backend loaded", True, lid_name))

    twilio_configured = (
        settings.TWILIO_ACCOUNT_SID not in ("", "mock_sid")
        and settings.TWILIO_AUTH_TOKEN not in ("", "mock_token")
    )
    print("\nTwilio credentials (needed for the live number, not for this script)")
    if twilio_configured:
        print("  [PASS] TWILIO_* look configured — present")
    else:
        print("  [WARN] TWILIO_* still mock/empty — set real values in .env before dialing")

    print("\nLive checklist (you verify on the call)")
    if phase == 8:
        print("  1. Trial 'press any key' if trial account — expected")
        print("  2. Hear language-selection prompt, then select (DTMF or speech)")
        print("  3. Log: language_selected ... then hear the task menu")
        print("  4. Say 'balance' (or 'solde' in French), then pause")
        print("  5. Hear the fake-balance line. Log: grammar_stt ... text='balance'")
        print("  6. ttfb_ms may be over 500 (includes Windows recognition) — expected")
        print("  7. Hangup -> STOP, no crash spam")
        print("\nRunbook: docs/features/FEAT-03.md (Phase 8 live grammar STT)")
    elif phase == 7:
        print("  1. Trial 'press any key' if trial account — expected")
        print("  2. Hear language-selection prompt, then select (DTMF or speech)")
        print("  3. Log: language_selected ... then hear the task menu")
        print("  4. Speak, then pause - hear canned reply (balance if IVR_STT_SCRIPT=balance,")
        print("     else 'did not catch that'). Should start quickly after you stop talking.")
        print("  5. Log: placeholder_turn ... ttfb_ms=... (official clock; ear delay can be higher)")
        print("  6. Optional 2nd call: French if TTS spoken languages includes fr")
        print("  7. Hangup -> STOP, no crash spam")
        if phase_label == "7b":
            print("  8. French selection: real French speech OR clear English — never English-accented French")
            print("\nRunbook: docs/features/FEAT-03.md (Phase 7b TTS voice matching)")
        else:
            print("\nRunbook: docs/features/FEAT-03.md (Phase 7 live smoke)")
    elif phase == 9:
        print("  1. Trial 'press any key' if trial account — expected")
        print("  2. Log: Playing prompt … bytes=… (bytes > 0)")
        print("  3. Hear spoken prompt (not silence / not only beeps)")
        print("  4. Speak or press digit -> language_selected / outcome=selected")
        print("  5. Hangup -> STOP, no crash spam")
        print("\nRunbook: docs/features/FEAT-02.md (Phase 9 live smoke)")
    else:
        print("  1. Prompt plays (English TTS is fine for the prompt only)")
        print("  2. YOU speak French (or another language) on the handset — not SAPI")
        print("  3. Log: language_selected language=<spoken> with SpeechBrain (not fixed)")
        print("  4. Optional: DTMF still selects if speech fails")
        print("  5. Hangup -> STOP, no crash spam")
        print("\nDefinitive runbook: docs/features/FEAT-02.md (Phase 10 live SpeechBrain)")

    if all(checks):
        print(f"\nPhase {phase_label} app profile ready. Configure Twilio + ngrok, then dial.")
        return 0

    print("\nNot ready: fix FAIL items, then re-run this script.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
