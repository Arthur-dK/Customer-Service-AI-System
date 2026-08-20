from core.language import (
    DEFAULT_FALLBACK_LANGUAGES,
    build_dtmf_menu_prompt,
    extract_country_code,
    language_from_dtmf_digit,
    language_selection_prompt,
    languages_for_country,
    resolve_caller_locale,
)


def test_extract_country_code_israel():
    assert extract_country_code("+972501234567") == "IL"


def test_extract_country_code_uk_landline():
    assert extract_country_code("+442071838750") == "GB"


def test_extract_country_code_invalid():
    assert extract_country_code(None) is None
    assert extract_country_code("") is None
    assert extract_country_code("not-a-number") is None


def test_languages_for_israel_are_static_shortlist():
    languages = languages_for_country("IL")
    assert languages[0] == "he"
    assert "en" in languages
    assert "ar" in languages


def test_languages_for_unknown_country_use_fallback():
    assert languages_for_country(None) == DEFAULT_FALLBACK_LANGUAGES
    assert languages_for_country("ZZ") == DEFAULT_FALLBACK_LANGUAGES


def test_resolve_known_country_uses_top_language_for_prompt():
    locale = resolve_caller_locale("+972501234567")
    assert locale.country_known is True
    assert locale.country_code == "IL"
    assert locale.prompt_language == "he"
    assert locale.languages[0] == "he"
    assert locale.e164 == "+972501234567"


def test_resolve_gb_number():
    locale = resolve_caller_locale("+442071838750")
    assert locale.country_known is True
    assert locale.country_code == "GB"
    assert locale.prompt_language == "en"
    assert locale.languages[0] == "en"


def test_resolve_unknown_number_falls_back_to_english_prompt():
    locale = resolve_caller_locale(None)
    assert locale.country_known is False
    assert locale.prompt_language == "en"
    assert locale.languages == DEFAULT_FALLBACK_LANGUAGES


def test_language_selection_prompt_falls_back_to_english():
    assert "language" in language_selection_prompt("en").lower()
    assert language_selection_prompt("xx-unknown") == language_selection_prompt("en")


def test_dtmf_menu_and_digit_mapping():
    languages = ("he", "ar", "en")
    menu = build_dtmf_menu_prompt(languages, spoken_in="en")
    assert "press 1" in menu.lower()
    assert "Hebrew" in menu
    assert language_from_dtmf_digit("1", languages) == "he"
    assert language_from_dtmf_digit("3", languages) == "en"
    assert language_from_dtmf_digit("9", languages) is None
    assert language_from_dtmf_digit("0", languages) is None
