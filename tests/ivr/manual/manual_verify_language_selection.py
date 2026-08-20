"""
Manual offline verification for Phase 6 language-selection state machine.

Drives fake audio/DTMF queues (no Twilio). Prints selection result + metrics.

Usage (from repo root):
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_language_selection.py
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_language_selection.py --scenario dtmf
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_language_selection.py --scenario barge_in
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_language_selection.py --scenario unknown_dtmf
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ivr.audio import chunk_mulaw, generate_silence_mulaw, generate_tone_mulaw  # noqa: E402
from services.ivr.language_selection import LanguageSelector  # noqa: E402
from services.ivr.lid import FixedLanguageIdentifier  # noqa: E402
from services.ivr.tts import ToneTextToSpeech  # noqa: E402
from services.ivr.vad import VadConfig  # noqa: E402


async def _wait_for_outbound(outbound: asyncio.Queue[str], min_chunks: int = 1) -> None:
    while outbound.qsize() < min_chunks:
        await asyncio.sleep(0.01)


async def _clear_outbound(outbound: asyncio.Queue[str]) -> None:
    while not outbound.empty():
        outbound.get_nowait()


async def _feed_speech(inbound: asyncio.Queue[bytes]) -> None:
    tone = generate_tone_mulaw(duration_ms=20, amplitude=0.6)
    for _ in range(20):
        await inbound.put(tone)
    for chunk in chunk_mulaw(generate_silence_mulaw(500), chunk_ms=20):
        await inbound.put(chunk)


def _selector(lid_language: str) -> LanguageSelector:
    return LanguageSelector(
        tts=ToneTextToSpeech(ms_per_char=5, min_ms=40, max_ms=80),
        lid=FixedLanguageIdentifier(language=lid_language, confidence=0.95),
        silence_timeout_s=0.25,
        min_lid_confidence=0.1,
        vad_config=VadConfig(rms_threshold=500, speech_start_ms=40, speech_end_ms=60),
        playback_realtime=False,
        max_dtmf_rounds=3,
    )


async def _run_scenario(scenario: str) -> int:
    inbound: asyncio.Queue[bytes] = asyncio.Queue()
    outbound: asyncio.Queue[str] = asyncio.Queue()
    dtmf: asyncio.Queue[str] = asyncio.Queue()

    if scenario == "speech":
        phone = "+972501234567"
        lid_lang = "he"

        async def feed():
            await _wait_for_outbound(outbound)
            await asyncio.sleep(0.05)
            await _feed_speech(inbound)

        expect_method = "speech"
        expect_lang = "he"
    elif scenario == "dtmf":
        phone = "+972501234567"
        lid_lang = "en"

        async def feed():
            await _wait_for_outbound(outbound)
            await _clear_outbound(outbound)
            await asyncio.sleep(0.35)
            await _wait_for_outbound(outbound)
            await dtmf.put("2")

        expect_method = "dtmf"
        expect_lang = "ar"
    elif scenario == "barge_in":
        phone = "+972501234567"
        lid_lang = "en"

        async def feed():
            await _wait_for_outbound(outbound)
            await _clear_outbound(outbound)
            await asyncio.sleep(0.35)
            await _wait_for_outbound(outbound)
            await _feed_speech(inbound)

        expect_method = "speech_barge_in"
        expect_lang = "en"
    elif scenario == "unknown_dtmf":
        phone = None
        lid_lang = "fr"

        async def feed():
            await _wait_for_outbound(outbound)
            await _clear_outbound(outbound)
            await asyncio.sleep(0.35)
            await _wait_for_outbound(outbound)
            await _clear_outbound(outbound)
            await asyncio.sleep(0.35)
            await _wait_for_outbound(outbound)
            await dtmf.put("1")

        expect_method = "dtmf"
        expect_lang = "en"
    else:
        raise ValueError(f"Unknown scenario: {scenario}")

    selector = _selector(lid_lang)
    feeder = asyncio.create_task(feed())
    result = await selector.run(
        phone_number=phone,
        inbound_audio=inbound,
        outbound_audio=outbound,
        dtmf_digits=dtmf,
    )
    await feeder

    print("Phase 6 language-selection verification (manual)")
    print(f"  scenario           = {scenario}")
    print(f"  phone_number       = {phone!r}")
    if result is None:
        print("  result             = None")
        print("FAILED: no language selected")
        return 1

    print(f"  selected_language  = {result.language}")
    print(f"  selection_method   = {result.method}")
    print(f"  metrics            = {json.dumps(result.metrics.to_dict(), indent=2)}")

    checks = {
        "outcome_selected": result.metrics.outcome == "selected",
        "method_matches": result.method == expect_method,
        "language_matches": result.language == expect_lang,
        "tts_ran": result.metrics.tts_calls >= 1,
    }
    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"  check[{name}] = {'PASS' if ok else 'FAIL'}")

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1

    print("All offline checks passed for this scenario.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manually verify language selection state machine.")
    parser.add_argument(
        "--scenario",
        choices=("speech", "dtmf", "barge_in", "unknown_dtmf"),
        default="speech",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all scenarios sequentially.",
    )
    args = parser.parse_args()

    if args.all:
        code = 0
        for scenario in ("speech", "dtmf", "barge_in", "unknown_dtmf"):
            print("=" * 60)
            scenario_code = asyncio.run(_run_scenario(scenario))
            code = code or scenario_code
        return code

    return asyncio.run(_run_scenario(args.scenario))


if __name__ == "__main__":
    raise SystemExit(main())
