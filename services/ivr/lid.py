"""Dedicated language identification backends for spoken audio."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from services.ivr.audio import TWILIO_SAMPLE_RATE, resample_pcm16

logger = logging.getLogger(__name__)

SPEECHBRAIN_TARGET_RATE = 16000
DEFAULT_SPEECHBRAIN_MODEL = "speechbrain/lang-id-voxlingua107-ecapa"


@dataclass(frozen=True)
class LanguageIdResult:
    language: str
    confidence: float
    latency_ms: float
    backend: str
    remapped_from: str | None = None


# Prefer these when VoxLingua107 ranks a low-resource lookalike first (e.g. br≈fr).
MAJOR_LID_LANGUAGES = frozenset(
    {
        "en",
        "fr",
        "es",
        "de",
        "it",
        "pt",
        "nl",
        "pl",
        "ru",
        "uk",
        "tr",
        "ar",
        "he",
        "hi",
        "ur",
        "bn",
        "pa",
        "zh",
        "ja",
        "ko",
        "sv",
        "da",
        "no",
        "fi",
        "cs",
        "ro",
        "hu",
        "el",
        "th",
        "vi",
        "id",
        "ms",
        "sw",
    }
)


def prefer_major_language_from_topk(
    top_labels: list[str],
    top_probs: list[float],
    *,
    min_alt_prob: float = 0.12,
    min_alt_ratio: float = 0.35,
) -> tuple[str, float, str | None]:
    """
    If the top language is low-resource but a major language is close in the
    top-k, prefer the major language. Returns (lang, confidence, remapped_from).
    """
    if not top_labels or not top_probs:
        return "", 0.0, None
    top_lang = top_labels[0]
    top_prob = float(top_probs[0])
    if top_lang in MAJOR_LID_LANGUAGES:
        return top_lang, top_prob, None
    for lang, prob in zip(top_labels[1:], top_probs[1:]):
        prob_f = float(prob)
        if (
            lang in MAJOR_LID_LANGUAGES
            and prob_f >= min_alt_prob
            and top_prob > 0
            and (prob_f / top_prob) >= min_alt_ratio
        ):
            return lang, prob_f, top_lang
    return top_lang, top_prob, None


class LanguageIdentifier(Protocol):
    async def identify(
        self,
        pcm16_audio: bytes,
        sample_rate: int = TWILIO_SAMPLE_RATE,
    ) -> LanguageIdResult | None:
        """Return ISO 639-1 language code for the utterance, or None if unknown."""


class FixedLanguageIdentifier:
    """
    Dev/test LID that returns a configured language when audio is present.

    Kept as the hard fallback when SpeechBrain is unavailable or forced off.
    """

    def __init__(
        self,
        language: str = "en",
        confidence: float = 0.99,
        backend: str = "fixed",
    ) -> None:
        self.language = language
        self.confidence = confidence
        self.backend = backend

    async def identify(
        self,
        pcm16_audio: bytes,
        sample_rate: int = TWILIO_SAMPLE_RATE,
    ) -> LanguageIdResult | None:
        started = time.perf_counter()
        await asyncio.sleep(0)
        if not pcm16_audio:
            return None
        return LanguageIdResult(
            language=self.language,
            confidence=self.confidence,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            backend=self.backend,
        )


def _patch_speechbrain_windows_lazy_imports() -> None:
    """
    SpeechBrain LazyModule skips imports triggered by inspect.py only when the
    frame path ends with '/inspect.py'. On Windows the path uses backslashes
    ('\\inspect.py'), so pydoc.locate / hyperpyyaml accidentally imports
    optional integrations (k2, etc.) and model load fails.

    Patch ensure_module to treat any '*inspect.py' basename as a non-import.
    """
    try:
        from speechbrain.utils import importutils as iu
    except ImportError:
        return

    if getattr(iu.LazyModule.ensure_module, "_ivr_windows_patch", False):
        return

    def ensure_module(self, stacklevel: int):  # noqa: ANN001
        importer_frame = None
        try:
            importer_frame = iu.inspect.getframeinfo(sys._getframe(stacklevel + 1))
        except AttributeError:
            warnings.warn(
                "Failed to inspect frame for SpeechBrain lazy import guard.",
                stacklevel=2,
            )

        if importer_frame is not None and Path(importer_frame.filename).name == "inspect.py":
            raise AttributeError()

        if self.lazy_module is None:
            try:
                if self.package is None:
                    self.lazy_module = importlib.import_module(self.target)
                else:
                    self.lazy_module = importlib.import_module(
                        f".{self.target}", self.package
                    )
            except Exception as exc:
                raise ImportError(f"Lazy import of {repr(self)} failed") from exc
        return self.lazy_module

    ensure_module._ivr_windows_patch = True  # type: ignore[attr-defined]
    iu.LazyModule.ensure_module = ensure_module  # type: ignore[method-assign]

    def deprecated_ensure_module(self, stacklevel: int):  # noqa: ANN001
        should_warn = self.lazy_module is None
        module = iu.LazyModule.ensure_module(self, stacklevel + 1)
        if should_warn:
            self._redirection_warn()
        return module

    deprecated_ensure_module._ivr_windows_patch = True  # type: ignore[attr-defined]
    iu.DeprecatedModuleRedirect.ensure_module = deprecated_ensure_module  # type: ignore[method-assign]


class SpeechBrainLanguageIdentifier:
    """
    Dedicated audio language-ID via SpeechBrain (VoxLingua107 ECAPA by default).

    Requires speechbrain + torch (see requirements-ivr-lid.txt). Construction fails
    clearly if unavailable so callers can fall back to FixedLanguageIdentifier.
    """

    def __init__(
        self,
        model_source: str = DEFAULT_SPEECHBRAIN_MODEL,
        device: str = "cpu",
        classifier: Any | None = None,
    ) -> None:
        if classifier is not None:
            self._classifier = classifier
        else:
            _patch_speechbrain_windows_lazy_imports()
            try:
                from speechbrain.inference.classifiers import EncoderClassifier  # type: ignore
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "speechbrain is not installed. Install optional IVR LID deps "
                    "(requirements-ivr-lid.txt or requirements-render.txt)."
                ) from exc

            savedir = _speechbrain_savedir(model_source)
            try:
                savedir.mkdir(parents=True, exist_ok=True)
                self._classifier = EncoderClassifier.from_hparams(
                    source=model_source,
                    savedir=str(savedir),
                    run_opts={"device": device},
                )
            except Exception as exc:  # pragma: no cover - platform/model load failures
                raise RuntimeError(
                    f"Failed to load SpeechBrain LID model '{model_source}' "
                    f"(savedir={savedir}): {exc}."
                ) from exc
        self.model_source = model_source
        self.device = device
        self.backend = "speechbrain"

    async def identify(
        self,
        pcm16_audio: bytes,
        sample_rate: int = TWILIO_SAMPLE_RATE,
    ) -> LanguageIdResult | None:
        if not pcm16_audio:
            return None

        started = time.perf_counter()
        pcm16 = (
            pcm16_audio
            if sample_rate == SPEECHBRAIN_TARGET_RATE
            else resample_pcm16(pcm16_audio, sample_rate, SPEECHBRAIN_TARGET_RATE)
        )
        waveform = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0

        def _run() -> tuple[str, float, str | None, list[str], list[float], str]:
            import torch

            audio = torch.from_numpy(waveform).unsqueeze(0)
            out_prob, score, _index, text_lab = self._classifier.classify_batch(audio)
            label = text_lab[0] if text_lab else ""
            language = normalize_voxlingua_label(str(label))
            confidence = _confidence_from_classifier_outputs(out_prob, score)

            tensor = out_prob if torch.is_tensor(out_prob) else torch.as_tensor(out_prob)
            probs = torch.exp(tensor) if float(tensor.min()) < 0.0 else tensor
            probs = probs.detach().cpu().flatten()
            topk = torch.topk(probs, k=min(5, probs.numel()))
            try:
                label_encoder = getattr(self._classifier, "hparams", None)
                lab = getattr(label_encoder, "label_encoder", None) if label_encoder else None
                if lab is not None and hasattr(lab, "decode_torch"):
                    decoded = lab.decode_torch(topk.indices)
                    top_labels = [normalize_voxlingua_label(str(x)) for x in decoded]
                else:
                    # Stubs / missing encoder: keep classify_batch text label as top-1.
                    rest = [str(int(i)) for i in topk.indices.tolist()[1:]]
                    top_labels = ([language] if language else []) + rest
            except Exception:
                rest = [str(int(i)) for i in topk.indices.tolist()[1:]]
                top_labels = ([language] if language else []) + rest
            top_probs = [float(p) for p in topk.values.tolist()]
            if not top_labels:
                top_labels = [language]
                top_probs = [confidence]

            chosen, chosen_conf, remapped_from = prefer_major_language_from_topk(
                top_labels, top_probs
            )
            if not chosen:
                chosen, chosen_conf, remapped_from = language, confidence, None

            return chosen, chosen_conf, remapped_from, top_labels, top_probs, str(label)

        language, confidence, remapped_from, _top_labels, _top_probs, _raw = await asyncio.to_thread(
            _run
        )
        if not language:
            return None
        if remapped_from:
            logger.info(
                "LID remapped low-resource %s -> %s (confidence=%.3f)",
                remapped_from,
                language,
                confidence,
            )
        return LanguageIdResult(
            language=language,
            confidence=confidence,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            backend="speechbrain",
            remapped_from=remapped_from,
        )


def _speechbrain_savedir(model_source: str) -> Path:
    root = Path(os.environ.get("HF_HOME") or (Path(".cache") / "huggingface"))
    return root / "ivr-lid" / model_source.replace("/", "--")


def build_default_lid(
    prefer_speechbrain: bool = True,
    force_language: str | None = None,
    speechbrain_model: str = DEFAULT_SPEECHBRAIN_MODEL,
) -> LanguageIdentifier:
    force = (force_language or "").strip() or None
    if force and not prefer_speechbrain:
        logger.info("Using fixed LID language override: %s", force)
        return FixedLanguageIdentifier(language=force)

    if force and prefer_speechbrain:
        logger.warning(
            "Ignoring IVR_LID_FORCE_LANGUAGE=%s because SpeechBrain LID is enabled",
            force,
        )

    if prefer_speechbrain:
        try:
            lid = SpeechBrainLanguageIdentifier(model_source=speechbrain_model)
            logger.info("Using SpeechBrain LID model=%s", speechbrain_model)
            return lid
        except Exception:  # pragma: no cover - optional dependency path
            logger.exception(
                "SpeechBrain LID failed to load; spoken language will be treated as English "
                "until the model is available"
            )

    logger.warning(
        "Using fixed English LID fallback. Spoken audio is not identified. "
        "On Render install requirements-render.txt (or the Docker image) with Python 3.12."
    )
    return FixedLanguageIdentifier(language="en", confidence=0.99, backend="fixed-fallback")


def speechbrain_available() -> bool:
    try:
        import speechbrain  # noqa: F401
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def _confidence_from_classifier_outputs(out_prob: Any, score: Any) -> float:
    """
    SpeechBrain VoxLingua returns log-probabilities in `out_prob` (and max log-prob
    in `score`). Convert to a [0, 1] confidence for selection thresholds.
    """
    import torch

    if out_prob is not None:
        tensor = out_prob if torch.is_tensor(out_prob) else torch.as_tensor(out_prob)
        if tensor.numel() == 0:
            return 0.0
        if float(tensor.min()) < 0.0:
            tensor = torch.exp(tensor)
        return float(tensor.max())

    if score is not None:
        value = score[0] if torch.is_tensor(score) else score
        value_f = float(value)
        return float(torch.exp(torch.tensor(value_f))) if value_f < 0.0 else value_f

    return 0.0


def normalize_voxlingua_label(label: str) -> str:
    """Map SpeechBrain labels like 'en: English' to ISO 639-1."""
    raw = label.strip().lower()
    if not raw:
        return ""
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    iso3_to_iso1 = {
        "eng": "en",
        "spa": "es",
        "fra": "fr",
        "deu": "de",
        "heb": "he",
        "arb": "ar",
        "ara": "ar",
        "cmn": "zh",
        "zho": "zh",
        "hin": "hi",
        "por": "pt",
        "rus": "ru",
        "ita": "it",
        "nld": "nl",
        "tur": "tr",
        "ukr": "uk",
        "urd": "ur",
        "ben": "bn",
        "swa": "sw",
    }
    if len(raw) == 3 and raw in iso3_to_iso1:
        return iso3_to_iso1[raw]
    if len(raw) >= 2:
        return raw[:2]
    return raw
