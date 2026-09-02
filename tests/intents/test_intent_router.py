"""Semantic card-action router: paraphrases in EN/HE/AR, reject if ambiguous."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.intents import CardAction, HashTokenEmbedder, IntentRouter, build_intent_router
from core.intents.embedder import HASH_DIM
from core.intents.router import load_prototypes

PROTOTYPES = Path(__file__).resolve().parents[2] / "core" / "intents" / "prototypes.json"


@pytest.fixture
def router() -> IntentRouter:
    item = IntentRouter(embedder=HashTokenEmbedder())
    item.warm()
    return item


def test_prototypes_have_two_examples_per_action_per_language():
    actions = load_prototypes()
    expected = {member.value for member in CardAction}
    assert set(actions) == expected
    for action, per_lang in actions.items():
        for lang in ("en", "he", "ar"):
            phrases = per_lang[lang]
            assert len(phrases) == 2, f"{action} {lang}"
            assert all(phrase.strip() for phrase in phrases)


def test_hash_embedder_does_not_import_sentence_transformers():
    import sys

    sys.modules.pop("sentence_transformers", None)
    embedder = HashTokenEmbedder()
    vectors = embedder.embed(["what is my balance"])
    assert vectors.shape == (1, HASH_DIM)
    assert "sentence_transformers" not in sys.modules


def test_routes_english_hebrew_arabic_prototypes(router: IntentRouter):
    actions = json.loads(PROTOTYPES.read_text(encoding="utf-8"))["actions"]
    for action_id, per_lang in actions.items():
        expected = CardAction(action_id)
        for lang, phrases in per_lang.items():
            for phrase in phrases:
                result = router.route(phrase)
                assert result.rejected is False, (action_id, lang, phrase, result)
                assert result.action == expected


def test_routes_varied_english_phrasing(router: IntentRouter):
    assert router.route("please tell me what is my balance").action == CardAction.GET_BALANCE
    assert router.route("i need my pin number now").action == CardAction.GET_PIN
    assert router.route("which card do i have on file").action == CardAction.GET_CARD
    assert router.route("i want the monthly statement please").action == CardAction.GET_CARD_STATEMENT
    assert router.route("please freeze my card").action == CardAction.BLOCK_CARD
    assert router.route("please unfreeze my card").action == CardAction.UNBLOCK_CARD


def test_rejects_unknown_and_ambiguous(router: IntentRouter):
    unknown = router.route("xyzzy hummingbird weather")
    assert unknown.rejected is True
    assert unknown.action is None
    both = router.route("block the card and what is my balance")
    assert both.rejected is True
    assert both.action is None


def test_build_intent_router_fake_never_loads_bge():
    import sys

    sys.modules.pop("sentence_transformers", None)
    built = build_intent_router(embedder_kind="fake")
    built.warm()
    assert built.route("what is my balance").action == CardAction.GET_BALANCE
    assert "sentence_transformers" not in sys.modules


@pytest.mark.slow
def test_optional_real_bge_m3_routes_english_balance():
    pytest.importorskip("sentence_transformers")
    router = build_intent_router(embedder_kind="bge")
    result = router.route("how much money is left on the card")
    assert result.rejected is False
    assert result.action == CardAction.GET_BALANCE
