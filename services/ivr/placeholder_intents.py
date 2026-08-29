"""Map stub transcripts to canned phrase IDs (no LLM, no card APIs)."""

from __future__ import annotations

import re

from core.language.phrases import (
    DID_NOT_CATCH,
    GOODBYE,
    PLACEHOLDER_BALANCE,
    PLACEHOLDER_BLOCKED,
    PLACEHOLDER_PIN,
)

# First match wins. Keywords cover English + French catalog lines.
_INTENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (PLACEHOLDER_BALANCE, ("balance", "solde")),
    (PLACEHOLDER_PIN, ("pin", "code pin")),
    (PLACEHOLDER_BLOCKED, ("block", "bloquer")),
    (GOODBYE, ("goodbye", "au revoir", "bye")),
)


def map_placeholder_intent(transcript: str) -> str:
    """Return a catalog phrase id for a final transcript."""
    normalized = _normalize(transcript)
    if not normalized:
        return DID_NOT_CATCH
    padded = f" {normalized} "
    for phrase_id, keywords in _INTENT_KEYWORDS:
        for keyword in keywords:
            if f" {keyword} " in padded:
                return phrase_id
    return DID_NOT_CATCH


def grammar_phrases(language: str) -> tuple[str, ...]:
    """Small command list for local speech recognition (not full dictation)."""
    lang = (language or "en").lower().replace("_", "-").split("-", 1)[0]
    if lang == "fr":
        return (
            "solde",
            "code PIN",
            "PIN",
            "bloquer la carte",
            "bloquer",
            "au revoir",
        )
    return (
        "balance",
        "PIN",
        "pin",
        "block card",
        "block",
        "goodbye",
        "bye",
    )


def _normalize(text: str) -> str:
    lowered = text.lower()
    cleaned = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE)
    return " ".join(cleaned.split())
