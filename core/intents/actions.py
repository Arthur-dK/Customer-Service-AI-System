"""Closed set of card actions for the semantic router."""

from __future__ import annotations

from enum import StrEnum


class CardAction(StrEnum):
    GET_BALANCE = "get_balance"
    GET_PIN = "get_pin"
    GET_CARD = "get_card"
    GET_CARD_STATEMENT = "get_card_statement"
    BLOCK_CARD = "block_card"
    UNBLOCK_CARD = "unblock_card"


ALL_ACTIONS: tuple[CardAction, ...] = tuple(CardAction)
