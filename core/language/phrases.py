"""Stable IVR phrase IDs and catalog text (menus, errors, placeholder tasks)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from core.language.countries import DEFAULT_PROMPT_LANGUAGE

_DATA_PATH = Path(__file__).resolve().parent / "phrases.json"

# Static lines that play often. Dynamic DTMF menus stay outside this catalog.
LANGUAGE_SELECT = "language_select"
DID_NOT_CATCH = "did_not_catch"
MAIN_MENU = "main_menu"
PLACEHOLDER_BALANCE = "placeholder_balance"
PLACEHOLDER_PIN = "placeholder_pin"
PLACEHOLDER_BLOCKED = "placeholder_blocked"
GOODBYE = "goodbye"


class UnknownPhraseError(KeyError):
    """Phrase id is not in the catalog, or has no text for the requested language."""


@dataclass(frozen=True)
class PhraseCatalog:
    warmup_languages: tuple[str, ...]
    phrases: Mapping[str, Mapping[str, str]]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self.phrases.keys())

    def resolve_language(self, phrase_id: str, language: str) -> str:
        """Requested language if the catalog has it, else English, else first available."""
        texts = self.phrases.get(phrase_id)
        if texts is None:
            raise UnknownPhraseError(phrase_id)
        lang = language.lower()
        if lang in texts:
            return lang
        if DEFAULT_PROMPT_LANGUAGE in texts:
            return DEFAULT_PROMPT_LANGUAGE
        return next(iter(texts))

    def has(self, phrase_id: str, language: str) -> bool:
        texts = self.phrases.get(phrase_id)
        if texts is None:
            return False
        return language.lower() in texts

    def text(self, phrase_id: str, language: str, *, strict: bool = False) -> str:
        texts = self.phrases.get(phrase_id)
        if texts is None:
            raise UnknownPhraseError(phrase_id)
        lang = language.lower() if strict else self.resolve_language(phrase_id, language)
        if lang not in texts:
            raise UnknownPhraseError(f"{phrase_id}:{lang}")
        return texts[lang]


@lru_cache(maxsize=1)
def load_phrase_catalog() -> PhraseCatalog:
    with _DATA_PATH.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    phrases = {
        phrase_id: {lang.lower(): text for lang, text in translations.items()}
        for phrase_id, translations in raw["phrases"].items()
    }
    warmup = tuple(str(code).lower() for code in raw.get("warmup_languages", ("en",)))
    return PhraseCatalog(warmup_languages=warmup, phrases=phrases)
