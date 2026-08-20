"""
Manual Phase 8 check: webhook TwiML includes from/country_code parameters.

Usage (from repo root):
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_webhook.py
  .\\venv\\Scripts\\python.exe tests\\ivr\\manual\\manual_verify_webhook.py --from-number +442071838750
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify voice webhook TwiML parameters.")
    parser.add_argument("--from-number", default="+972501234567")
    args = parser.parse_args()

    client = TestClient(app)
    response = client.post(
        "/voice/incoming",
        headers={"host": "localhost:8000"},
        data={"From": args.from_number} if args.from_number else {},
    )

    print("Phase 8 webhook verification (manual)")
    print(f"  from_number = {args.from_number!r}")
    print(f"  status      = {response.status_code}")
    print("  twiml:")
    print(response.text)

    checks = {
        "http_200": response.status_code == 200,
        "has_stream": "<Stream url=" in response.text,
        "has_from_param": 'name="from"' in response.text,
    }
    if args.from_number.startswith("+972"):
        checks["has_il_country"] = 'name="country_code" value="IL"' in response.text
    elif args.from_number.startswith("+4420"):
        checks["has_gb_country"] = 'name="country_code" value="GB"' in response.text

    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"  check[{name}] = {'PASS' if ok else 'FAIL'}")

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1

    print("All offline webhook checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
