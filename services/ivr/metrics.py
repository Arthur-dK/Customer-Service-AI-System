"""Metrics captured during IVR language selection (for logs and test assertions)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SelectionMethod = Literal["speech", "dtmf", "speech_barge_in"]


@dataclass
class LanguageSelectionMetrics:
    country_code: str | None = None
    country_known: bool = False
    prompt_language: str | None = None
    menu_languages: list[str] = field(default_factory=list)

    selected_language: str | None = None
    selection_method: SelectionMethod | None = None
    dtmf_digit: str | None = None

    silence_timeouts: int = 0
    english_reprompts: int = 0
    dtmf_fallback_entered: bool = False
    barge_in_during_dtmf: bool = False

    speech_utterances: int = 0
    lid_backend: str | None = None
    lid_language: str | None = None
    lid_confidence: float | None = None
    lid_latency_ms: float | None = None

    tts_calls: int = 0
    tts_synth_ms_total: float = 0.0
    time_to_first_speech_ms: float | None = None
    total_selection_ms: float | None = None

    outcome: str = "pending"  # pending | selected | abandoned

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
