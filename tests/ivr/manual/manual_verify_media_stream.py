"""
Manual Phase 7 harness: fake Twilio WebSocket against the real FastAPI app.

Usage (from repo root):
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_media_stream.py
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_media_stream.py --mode speech
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from services.ivr.audio import chunk_mulaw, generate_silence_mulaw, generate_tone_mulaw  # noqa: E402
from services.ivr.lid import FixedLanguageIdentifier  # noqa: E402
from services.ivr.selection_store import (  # noqa: E402
    clear_last_language_selection,
    get_last_language_selection,
)
from services.ivr.tts import ToneTextToSpeech  # noqa: E402
from tests.ivr.manual.fake_twilio_stream import (  # noqa: E402
    connected_message,
    dtmf_message,
    media_message,
    parse_outbound,
    start_message,
    stop_message,
)


def _install_fast_backends() -> None:
    import app.api.ivr as ivr_api

    ivr_api.get_tts = lambda: ToneTextToSpeech(ms_per_char=5, min_ms=40, max_ms=80)  # type: ignore
    ivr_api.get_lid = lambda: FixedLanguageIdentifier(language="en", confidence=0.99)  # type: ignore
    ivr_api.settings.IVR_SILENCE_TIMEOUT_S = 0.3
    ivr_api.settings.IVR_PLAYBACK_REALTIME = False
    ivr_api.settings.IVR_MIN_LID_CONFIDENCE = 0.1


def _collect_media(websocket, min_frames: int = 1, timeout_s: float = 2.0) -> int:
    count = 0
    deadline = time.time() + timeout_s
    while time.time() < deadline and count < min_frames:
        time.sleep(0.01)
        try:
            raw = websocket.receive_text()
        except Exception:
            break
        parsed = parse_outbound(raw)
        if parsed.get("event") == "media":
            count += 1
            deadline = max(deadline, time.time() + 0.2)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Fake Twilio media-stream language harness.")
    parser.add_argument("--mode", choices=("dtmf", "speech"), default="dtmf")
    parser.add_argument("--from-number", default="+442071838750")
    args = parser.parse_args()

    _install_fast_backends()
    clear_last_language_selection()

    client = TestClient(app)
    with client.websocket_connect("/media-stream") as websocket:
        websocket.send_text(connected_message())
        websocket.send_text(start_message(from_number=args.from_number))

        media_frames = _collect_media(websocket, min_frames=1)
        print(f"outbound_media_frames = {media_frames}")

        if args.mode == "dtmf":
            websocket.send_text(dtmf_message("1"))
        else:
            tone = generate_tone_mulaw(duration_ms=20, amplitude=0.6)
            for _ in range(25):
                websocket.send_text(media_message(tone))
            for chunk in chunk_mulaw(generate_silence_mulaw(500), chunk_ms=20):
                websocket.send_text(media_message(chunk))

        result = None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            result = get_last_language_selection()
            if result is not None:
                break
            time.sleep(0.05)

        websocket.send_text(stop_message())

    print("Phase 7 media-stream harness (manual)")
    print(f"  mode              = {args.mode}")
    print(f"  from_number       = {args.from_number}")
    if result is None:
        print("  result            = None")
        print("FAILED: no language selected")
        return 1

    print(f"  selected_language = {result.language}")
    print(f"  selection_method  = {result.method}")
    print(f"  outcome           = {result.metrics.outcome}")
    print(f"  country_code      = {result.metrics.country_code}")
    print(f"  tts_calls         = {result.metrics.tts_calls}")
    print(f"  dtmf_digit        = {result.metrics.dtmf_digit}")

    checks = {
        "got_outbound_media": media_frames >= 1,
        "outcome_selected": result.metrics.outcome == "selected",
        "has_language": bool(result.language),
        "tts_ran": result.metrics.tts_calls >= 1,
    }
    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"  check[{name}] = {'PASS' if ok else 'FAIL'}")

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1

    print("All offline harness checks passed (no real phone call).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
