"""Closed-set yes/no confirm: multilingual lexicon, no translator."""

from core.intents import ConfirmInterpreter


def test_english_yes_no_and_unclear():
    item = ConfirmInterpreter()
    assert item.interpret("yes please").answer is True
    assert item.interpret("no thanks").answer is False
    assert item.interpret("maybe later").answer is None
    assert item.interpret("yes and no").answer is None
    assert item.interpret("si vous voulez").answer is None


def test_product_and_other_language_yes_no():
    item = ConfirmInterpreter()
    assert item.interpret("oui").answer is True
    assert item.interpret("non").answer is False
    assert item.interpret("כן").answer is True
    assert item.interpret("לא").answer is False
    assert item.interpret("نعم من فضلك").answer is True
    assert item.interpret("لا").answer is False
    assert item.interpret("ja").answer is True
    assert item.interpret("nein").answer is False
    assert item.interpret("はい").answer is True
    assert item.interpret("いいえ").answer is False
    assert item.interpret("sí").answer is True
    assert item.interpret("是的").answer is True
    assert item.interpret("不是").answer is False
