"""Ensure repo root is importable and IVR harness settings are test-friendly."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Must run before app/core.settings imports in test modules.
os.environ.setdefault("IVR_SILENCE_TIMEOUT_S", "0.3")
os.environ.setdefault("IVR_PLAYBACK_REALTIME", "false")
os.environ.setdefault("IVR_USE_SPEECHBRAIN_LID", "false")
os.environ.setdefault("IVR_LID_FORCE_LANGUAGE", "en")
os.environ.setdefault("IVR_MIN_LID_CONFIDENCE", "0.1")
os.environ.setdefault("IVR_STT_BACKEND", "scripted")
os.environ.setdefault("INTENT_EMBEDDER", "fake")

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_EXAMPLE = _REPO_ROOT / "data" / "callers.example.json"


@pytest.fixture(autouse=True)
def isolated_caller_store(tmp_path, monkeypatch):
    """Keep stub SQLite under tmp so pytest never writes data/callers.sqlite."""
    from core.cards.store import build_caller_store

    store = build_caller_store(
        sqlite_path=tmp_path / "callers.sqlite",
        example_path=_EXAMPLE,
        local_path=tmp_path / "missing.json",
    )
    monkeypatch.setattr("app.api.ivr.get_caller_store", lambda: store)
    monkeypatch.setattr("app.deps.get_caller_store", lambda: store)
    monkeypatch.setattr(
        "services.ivr.intent_turns.IntentTurnEngine.card",
        lambda self: self.store.lookup(self.phone_number),
    )
    yield store
    store.close()
