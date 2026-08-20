"""Ensure repo root is importable and IVR harness settings are test-friendly."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Must run before app/core.settings imports in test modules.
os.environ.setdefault("IVR_SILENCE_TIMEOUT_S", "0.3")
os.environ.setdefault("IVR_PLAYBACK_REALTIME", "false")
os.environ.setdefault("IVR_USE_SPEECHBRAIN_LID", "false")
os.environ.setdefault("IVR_LID_FORCE_LANGUAGE", "en")
os.environ.setdefault("IVR_MIN_LID_CONFIDENCE", "0.1")
os.environ.setdefault("IVR_VAD_RMS_THRESHOLD", "500")

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
