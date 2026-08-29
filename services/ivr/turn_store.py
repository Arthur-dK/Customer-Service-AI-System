"""In-memory placeholder-turn results for fake Twilio / pytest harnesses."""

from __future__ import annotations

from services.ivr.turn_engine import TurnResult

_last_turns: list[TurnResult] = []


def set_last_turns(turns: list[TurnResult] | None) -> None:
    global _last_turns
    _last_turns = list(turns or [])


def get_last_turns() -> list[TurnResult]:
    return list(_last_turns)


def clear_last_turns() -> None:
    set_last_turns([])
