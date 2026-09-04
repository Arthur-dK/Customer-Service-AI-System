"""Yes/no for destructive confirm. Not the six-action card router.

A static multilingual lexicon is cheaper than translating into English: confirm
is one or two tokens, needs no extra model, and stays on the 500 ms canned path.
Unknown wording still fails closed and DTMF can take over after two unclear turns.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_WORDS_PATH = Path(__file__).resolve().parent / "confirm_words.json"
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _load_word_sets() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    raw = json.loads(_WORDS_PATH.read_text(encoding="utf-8"))
    yes = frozenset(raw["yes"])
    no = frozenset(raw["no"])
    fillers = frozenset(raw["fillers"])
    overlap = yes & no
    if overlap:
        raise ValueError(f"confirm yes/no lists overlap: {sorted(overlap)}")
    return yes, no, fillers


_YES, _NO, _FILLERS = _load_word_sets()


@dataclass(frozen=True)
class ConfirmResult:
    answer: bool | None


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFC", text).casefold()
    normalized = normalized.replace("'", "").replace("’", "")
    return {match for match in _TOKEN.findall(normalized) if match}


class ConfirmInterpreter:
    """Closed yes/no lexicon (many languages). Does not call a translator or embedder."""

    def interpret(self, text: str) -> ConfirmResult:
        tokens = _tokens(text)
        content = tokens - _FILLERS
        if not content:
            return ConfirmResult(None)
        has_yes = bool(content & _YES)
        has_no = bool(content & _NO)
        if has_yes and has_no:
            return ConfirmResult(None)
        if has_yes and content <= _YES:
            return ConfirmResult(True)
        if has_no and content <= _NO:
            return ConfirmResult(False)
        return ConfirmResult(None)
