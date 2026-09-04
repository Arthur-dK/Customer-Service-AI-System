"""In-memory last-turn results for fake Twilio / pytest harnesses."""

from __future__ import annotations

from services.ivr.intent_turns import IntentTurnResult
from services.ivr.turn_engine import TurnResult

TurnRecord = IntentTurnResult | TurnResult

_last_turns: list[TurnRecord] = []


def set_last_turns(turns: list[TurnRecord] | None) -> None:
    global _last_turns
    _last_turns = list(turns or [])


def get_last_turns() -> list[TurnRecord]:
    return list(_last_turns)


def clear_last_turns() -> None:
    set_last_turns([])
