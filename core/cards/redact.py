"""Log redaction: last-4 of a phone number; never print full E.164 or PINs."""

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


def contains_digit_run(text: str, *, minimum: int = 4) -> bool:
    return re.search(rf"\d{{{minimum},}}", text) is not None


def log_contains_secret(text: str, *, full_e164: str | None = None, pin: str | None = None) -> bool:
    """True when logs leaked a full number or an explicit PIN string."""
    if full_e164 and full_e164 in text:
        return True
    if pin and pin in text:
        return True
    return False
