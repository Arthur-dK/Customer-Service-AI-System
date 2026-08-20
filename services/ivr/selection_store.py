"""In-memory last language-selection result (for harness tests / debugging)."""

from __future__ import annotations

from services.ivr.language_selection import LanguageSelectionResult

_last_result: LanguageSelectionResult | None = None


def set_last_language_selection(result: LanguageSelectionResult | None) -> None:
    global _last_result
    _last_result = result


def get_last_language_selection() -> LanguageSelectionResult | None:
    return _last_result


def clear_last_language_selection() -> None:
    set_last_language_selection(None)
