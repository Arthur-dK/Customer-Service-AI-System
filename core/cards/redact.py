"""Last-four digits of a phone number for logs (never print full E.164)."""

from __future__ import annotations

import re


def last4_phone(phone_number: str | None) -> str:
    """Last four digits for logs, or ``----`` when unknown."""
    if not phone_number:
        return "----"
    digits = re.sub(r"\D", "", str(phone_number))
    if len(digits) < 4:
        return "----"
    return digits[-4:]
