"""Phase 8: Render image extras and boot seed stay offline in pytest."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_render_requirements_include_whisper_and_bge():
    text = (ROOT / "requirements-render.txt").read_text(encoding="utf-8")
    extras = (ROOT / "requirements-ivr-intent.txt").read_text(encoding="utf-8")
    assert "requirements-ivr-intent.txt" in text
    assert "faster-whisper" in extras
    assert "sentence-transformers" in extras


def test_dockerfile_installs_intent_extras():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements-ivr-intent.txt" in text
    assert "faster_whisper" in text
    assert "sentence_transformers" in text
    assert "IVR_STT_BACKEND=whisper" in text
    assert "INTENT_EMBEDDER=bge" in text


def test_boot_seed_builds_example_store(tmp_path, monkeypatch):
    from core.cards.store import build_caller_store_from_settings
    from core.config import Settings

    sqlite = tmp_path / "callers.sqlite"
    settings = Settings(
        CALLERS_SQLITE=str(sqlite),
        CALLERS_EXAMPLE_JSON=str(ROOT / "data" / "callers.example.json"),
        CALLERS_LOCAL_JSON=str(tmp_path / "missing.json"),
        CALLER_OVERRIDE_JSON=None,
    )
    store = build_caller_store_from_settings(settings)
    card = store.lookup("+15555550100")
    assert card is not None
    assert card.last4 == "4242"
    store.close()
