"""Unit + contract checks for language-ID backends."""

from __future__ import annotations

import pytest

from services.ivr.audio import generate_tone_mulaw, mulaw_to_pcm16, resample_pcm16
from services.ivr.lid import (
    FixedLanguageIdentifier,
    SpeechBrainLanguageIdentifier,
    build_default_lid,
    normalize_voxlingua_label,
    prefer_major_language_from_topk,
    speechbrain_available,
)


@pytest.mark.asyncio
async def test_fixed_lid_returns_configured_language():
    lid = FixedLanguageIdentifier(language="he", confidence=0.91)
    pcm = mulaw_to_pcm16(generate_tone_mulaw(300, amplitude=0.4))
    result = await lid.identify(pcm)
    assert result is not None
    assert result.language == "he"
    assert result.confidence == 0.91
    assert result.backend == "fixed"
    assert result.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_fixed_lid_rejects_empty_audio():
    lid = FixedLanguageIdentifier(language="en")
    assert await lid.identify(b"") is None


@pytest.mark.asyncio
async def test_fixed_lid_confidence_is_usable_for_selection_threshold():
    """Regression: confidence 0.0 caused live speech path to never select a language."""
    lid = FixedLanguageIdentifier(language="en", confidence=0.99)
    pcm = mulaw_to_pcm16(generate_tone_mulaw(200, amplitude=0.4))
    result = await lid.identify(pcm)
    assert result is not None
    assert result.confidence >= 0.15


def test_normalize_voxlingua_label():
    assert normalize_voxlingua_label("en: English") == "en"
    assert normalize_voxlingua_label("heb") == "he"
    assert normalize_voxlingua_label("ES") == "es"
    assert normalize_voxlingua_label("") == ""


def test_prefer_major_language_remaps_breton_to_french():
    """Live barge-in: SpeechBrain ranked br 0.516 over fr 0.291 for spoken French."""
    lang, conf, remapped = prefer_major_language_from_topk(
        ["br", "fr", "nn", "iw", "de"],
        [0.516, 0.2911, 0.1064, 0.028, 0.0201],
    )
    assert lang == "fr"
    assert remapped == "br"
    assert conf == pytest.approx(0.2911)


def test_prefer_major_language_keeps_clear_english():
    lang, conf, remapped = prefer_major_language_from_topk(
        ["en", "el", "mi", "cy", "de"],
        [0.9585, 0.0135, 0.0118, 0.0081, 0.0018],
    )
    assert lang == "en"
    assert remapped is None
    assert conf == pytest.approx(0.9585)


def test_resample_pcm16_doubles_length_8k_to_16k():
    pcm_8k = mulaw_to_pcm16(generate_tone_mulaw(100, amplitude=0.4))
    pcm_16k = resample_pcm16(pcm_8k, 8000, 16000)
    assert len(pcm_16k) == pytest.approx(len(pcm_8k) * 2, abs=4)


def test_build_default_lid_force_language():
    lid = build_default_lid(prefer_speechbrain=False, force_language="fr")
    assert isinstance(lid, FixedLanguageIdentifier)
    assert lid.language == "fr"


def test_build_default_lid_ignores_force_when_speechbrain_enabled(monkeypatch):
    real = SpeechBrainLanguageIdentifier

    monkeypatch.setattr(
        "services.ivr.lid.SpeechBrainLanguageIdentifier",
        lambda *args, **kwargs: real(classifier=object()),
    )
    lid = build_default_lid(prefer_speechbrain=True, force_language="en")
    assert isinstance(lid, real)
    assert getattr(lid, "backend", None) == "speechbrain"


def test_build_default_lid_falls_back_without_speechbrain():
    if speechbrain_available():
        pytest.skip("SpeechBrain installed; fallback path not exercised")
    lid = build_default_lid(prefer_speechbrain=True, force_language=None)
    assert isinstance(lid, FixedLanguageIdentifier)
    assert lid.language == "en"
    assert lid.confidence >= 0.15


def test_build_default_lid_falls_back_when_speechbrain_load_fails(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated SpeechBrain model load failure")

    monkeypatch.setattr(
        "services.ivr.lid.SpeechBrainLanguageIdentifier",
        _boom,
    )
    lid = build_default_lid(prefer_speechbrain=True, force_language=None)
    assert isinstance(lid, FixedLanguageIdentifier)
    assert lid.language == "en"
    assert lid.backend == "fixed-fallback"
    assert lid.confidence >= 0.15


@pytest.mark.asyncio
async def test_speechbrain_lid_with_fake_classifier_on_pcm():
    """Contract test with a stub classifier (needs torch tensors, not the HF model)."""
    torch = pytest.importorskip("torch")

    class FakeClassifier:
        def classify_batch(self, audio):  # noqa: ANN001
            assert audio.ndim == 2
            assert audio.shape[0] == 1
            assert audio.shape[1] >= 1000  # resampled 8 kHz → 16 kHz
            probs = torch.tensor([[0.05, 0.9, 0.05]])
            score = torch.tensor([0.9])
            index = torch.tensor([1])
            text_lab = ["he: Hebrew"]
            return probs, score, index, text_lab

    lid = SpeechBrainLanguageIdentifier(classifier=FakeClassifier())
    pcm_8k = mulaw_to_pcm16(generate_tone_mulaw(400, amplitude=0.4))
    result = await lid.identify(pcm_8k, sample_rate=8000)
    assert result is not None
    assert result.language == "he"
    assert result.confidence == pytest.approx(0.9)
    assert result.backend == "speechbrain"
    assert result.latency_ms >= 0.0


@pytest.mark.asyncio
@pytest.mark.skipif(not speechbrain_available(), reason="speechbrain/torch not installed")
async def test_speechbrain_real_model_on_english_speech_sample():
    """Optional offline check: real VoxLingua107 on SAPI English (slow; may download)."""
    import importlib.util
    from pathlib import Path

    try:
        lid = SpeechBrainLanguageIdentifier()
    except Exception as exc:
        pytest.skip(f"SpeechBrain model load failed on this platform: {exc}")

    path = Path(__file__).resolve().parents[1] / "manual" / "manual_verify_audio.py"
    spec = importlib.util.spec_from_file_location("manual_verify_audio", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    out = Path("scratch") / "lid_pytest_en.wav"
    mulaw = module.speech_through_mulaw_pipeline(
        "Hello, this is an English language identification sample.",
        out,
    )
    pcm = mulaw_to_pcm16(mulaw)
    result = await lid.identify(pcm)
    assert result is not None
    assert result.language == "en"
    assert result.confidence >= 0.15
    assert result.backend == "speechbrain"
