"""Country and language lookup helpers for IVR language selection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException

from core.language.constants import DEFAULT_FALLBACK_LANGUAGES, DEFAULT_PROMPT_LANGUAGE

_DATA_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class CallerLocale:
    """Resolved caller geography and language menu."""

    e164: str | None
    country_code: str | None
    languages: tuple[str, ...]
    prompt_language: str
    country_known: bool


@lru_cache(maxsize=1)
def _load_json(name: str) -> dict:
    with (_DATA_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def load_country_languages() -> dict[str, list[str]]:
    return _load_json("country_languages.json")


def load_prompts() -> dict[str, str]:
    return _load_json("prompts.json")


def load_language_names() -> dict[str, str]:
    return _load_json("language_names.json")


def extract_country_code(phone_number: str | None) -> str | None:
    """Return ISO 3166-1 alpha-2 country code from an E.164 (or similar) number."""
    if not phone_number or not str(phone_number).strip():
        return None

    raw = str(phone_number).strip()
    try:
        parsed = phonenumbers.parse(raw, None)
    except NumberParseException:
        if raw.startswith("+"):
            return None
        try:
            parsed = phonenumbers.parse(f"+{raw.lstrip('00')}", None)
        except NumberParseException:
            return None

    region = phonenumbers.region_code_for_number(parsed)
    return region.upper() if region else None


def languages_for_country(country_code: str | None) -> tuple[str, ...]:
    """Return the maintained language shortlist for a country, or the global default."""
    if not country_code:
        return DEFAULT_FALLBACK_LANGUAGES

    languages = load_country_languages().get(country_code.upper())
    if not languages:
        return DEFAULT_FALLBACK_LANGUAGES
    return tuple(languages)


def resolve_caller_locale(phone_number: str | None) -> CallerLocale:
    """Parse the caller number and build the language-selection menu context."""
    e164: str | None = None
    country_code = extract_country_code(phone_number)

    if phone_number and str(phone_number).strip():
        raw = str(phone_number).strip()
        try:
            parsed = phonenumbers.parse(
                raw if raw.startswith("+") else f"+{raw.lstrip('00')}",
                None,
            )
            if phonenumbers.is_possible_number(parsed):
                e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except NumberParseException:
            e164 = raw

    country_known = country_code is not None and country_code in load_country_languages()
    languages = languages_for_country(country_code if country_known else None)
    prompt_language = languages[0] if country_known else DEFAULT_PROMPT_LANGUAGE

    return CallerLocale(
        e164=e164,
        country_code=country_code,
        languages=languages,
        prompt_language=prompt_language,
        country_known=country_known,
    )


def language_selection_prompt(language_code: str) -> str:
    prompts = load_prompts()
    return prompts.get(language_code, prompts[DEFAULT_PROMPT_LANGUAGE])


def language_display_name(language_code: str) -> str:
    names = load_language_names()
    return names.get(language_code, language_code)


def build_dtmf_menu_prompt(languages: tuple[str, ...] | list[str], spoken_in: str = "en") -> str:
    """Build a numbered DTMF menu. Language display names stay in English for clarity."""
    intro = {
        "en": "If you prefer to use the keypad, press the number for your language.",
        "es": "Si prefiere usar el teclado, pulse el número de su idioma.",
        "fr": "Si vous préférez utiliser le clavier, appuyez sur le numéro de votre langue.",
        "he": "אם תרצה להשתמש במקלדת, הקש את המספר של השפה שלך.",
        "ar": "إذا كنت تفضل استخدام لوحة المفاتيح، اضغط على رقم لغتك.",
    }.get(spoken_in, "If you prefer to use the keypad, press the number for your language.")

    parts = [intro]
    for index, code in enumerate(languages[:9], start=1):
        parts.append(f"For {language_display_name(code)}, press {index}.")
    return " ".join(parts)


def language_from_dtmf_digit(digit: str, languages: tuple[str, ...] | list[str]) -> str | None:
    if not digit or not digit.isdigit():
        return None
    choice = int(digit)
    if choice < 1 or choice > len(languages) or choice > 9:
        return None
    return languages[choice - 1]
