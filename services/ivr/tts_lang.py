"""ISO language tags for TTS routing (call language → matching voice)."""

from __future__ import annotations

import json
from pathlib import Path

# LID / catalog aliases → ISO 639-1 used by voice files and SAPI cultures.
_ALIASES = {
    "cmn": "zh",
    "zho": "zh",
    "chi": "zh",
    "yue": "zh",
    "iw": "he",
    "in": "id",
    "fil": "tl",
    "nb": "no",
    "nn": "no",
}


def normalize_language(language: str) -> str:
    """Return a lowercase ISO 639-1 (or short) code: ``fr-FR`` / ``fr_FR`` → ``fr``."""
    raw = (language or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    primary = raw.split("-", 1)[0]
    return _ALIASES.get(primary, primary)


def language_from_piper_path(path: Path | str) -> str:
    """Infer language from a Piper filename such as ``fr_FR-siwis-medium.onnx``."""
    stem = Path(path).stem
    locale = stem.split("-", 1)[0]
    lang = normalize_language(locale)
    if 2 <= len(lang) <= 3 and lang.isalpha():
        return lang
    return "en"


def parse_piper_voice_map(raw: str | None) -> dict[str, str]:
    """Parse ``IVR_PIPER_VOICES`` JSON object: ``{"fr": "C:/voices/fr.onnx"}``."""
    if not raw or not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("IVR_PIPER_VOICES must be a JSON object of language → model path")
    out: dict[str, str] = {}
    for key, value in data.items():
        lang = normalize_language(str(key))
        if lang and value:
            out[lang] = str(value)
    return out


def piper_models_in_dir(voice_dir: Path) -> dict[str, Path]:
    """Map language → first ``*.onnx`` whose filename starts with that locale."""
    found: dict[str, Path] = {}
    if not voice_dir.is_dir():
        return found
    for model in sorted(voice_dir.glob("*.onnx")):
        lang = language_from_piper_path(model)
        if lang and lang not in found:
            found[lang] = model
    return found
