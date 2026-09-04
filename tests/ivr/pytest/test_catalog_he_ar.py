"""FEAT-04 catalog: English/Hebrew/Arabic copy; PIN lines have no digits."""

from __future__ import annotations

import re

from core.language.phrases import (
    CARD_BLOCKED,
    CARD_UNBLOCKED,
    CONFIRM_BLOCK,
    CONFIRM_UNBLOCK,
    DID_NOT_CATCH,
    DTMF_ACTIONS,
    DTMF_CONFIRM,
    GET_BALANCE,
    GET_CARD,
    GET_CARD_STATEMENT,
    LANGUAGE_SELECT,
    MAIN_MENU,
    PIN_VIA_SMS,
    PLACEHOLDER_PIN,
    UNKNOWN_CALLER,
    load_phrase_catalog,
)
from services.ivr.edge_tts import EDGE_VOICES, EdgeTextToSpeech

_PRODUCT_LANGS = ("en", "he", "ar")
_FEAT04_IDS = (
    LANGUAGE_SELECT,
    DID_NOT_CATCH,
    MAIN_MENU,
    UNKNOWN_CALLER,
    PIN_VIA_SMS,
    GET_BALANCE,
    GET_CARD,
    GET_CARD_STATEMENT,
    CONFIRM_BLOCK,
    CONFIRM_UNBLOCK,
    CARD_BLOCKED,
    CARD_UNBLOCKED,
    DTMF_ACTIONS,
    DTMF_CONFIRM,
)
_PIN_IDS = (PIN_VIA_SMS, PLACEHOLDER_PIN)
_DIGIT_RUN = re.compile(r"\d{2,}")
_ANY_DIGIT = re.compile(r"[0-9\u0660-\u0669\u06F0-\u06F9]")


def test_warmup_languages_are_product_voice_loop():
    catalog = load_phrase_catalog()
    assert catalog.warmup_languages == _PRODUCT_LANGS


def test_feat04_ids_have_en_he_ar_and_differ():
    catalog = load_phrase_catalog()
    for phrase_id in _FEAT04_IDS:
        texts = {lang: catalog.text(phrase_id, lang, strict=True) for lang in _PRODUCT_LANGS}
        assert all(texts[lang].strip() for lang in _PRODUCT_LANGS), phrase_id
        assert texts["en"] != texts["he"], phrase_id
        assert texts["en"] != texts["ar"], phrase_id
        assert texts["he"] != texts["ar"], phrase_id


def test_pin_phrases_have_no_digit_characters():
    catalog = load_phrase_catalog()
    for phrase_id in _PIN_IDS:
        for lang, text in catalog.phrases[phrase_id].items():
            assert _DIGIT_RUN.search(text) is None, (phrase_id, lang, text)
            assert _ANY_DIGIT.search(text) is None, (phrase_id, lang, text)
            assert "{" not in text, (phrase_id, lang)


def test_slot_templates_keep_placeholders():
    catalog = load_phrase_catalog()
    for lang in _PRODUCT_LANGS:
        assert "{balance_text}" in catalog.text(GET_BALANCE, lang, strict=True)
        assert "{last4}" in catalog.text(GET_CARD, lang, strict=True)
        assert "{statement}" in catalog.text(GET_CARD_STATEMENT, lang, strict=True)


def test_edge_tts_can_name_hebrew_and_arabic_voices():
    tts = EdgeTextToSpeech()
    assert tts.supports_language("he") is True
    assert tts.supports_language("ar") is True
    assert tts.supports_language("en") is True
    assert EDGE_VOICES["he"].startswith("he-")
    assert EDGE_VOICES["ar"].startswith("ar-")
