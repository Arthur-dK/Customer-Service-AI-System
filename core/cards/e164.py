"""E.164 helpers for stub caller lookup (same parser family as locale)."""

from __future__ import annotations

import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException

_WITHHELD = frozenset({"", "anonymous", "restricted", "unavailable", "unknown"})


def normalize_e164(phone_number: str | None) -> str | None:
    """Return canonical +E.164 or None when missing, withheld, or unparseable."""
    if phone_number is None:
        return None
    raw = str(phone_number).strip()
    if not raw or raw.lower() in _WITHHELD:
        return None
    try:
        parsed = phonenumbers.parse(
            raw if raw.startswith("+") else f"+{raw.lstrip('00')}",
            None,
        )
    except NumberParseException:
        return None
    if not phonenumbers.is_possible_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
