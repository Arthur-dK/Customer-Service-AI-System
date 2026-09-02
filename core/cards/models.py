from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StubCard:
    card_id: str
    last4: str
    balance_text: str
    currency: str
    blocked: bool
    statement: str
    phones: tuple[str, ...]
    has_pin: bool
