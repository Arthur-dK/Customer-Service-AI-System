"""Stub caller/card store: E.164 allowlist, overlay PIN, no PII in example JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.cards import last4_phone, normalize_e164
from core.cards.store import build_caller_store

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "callers.example.json"


def test_example_seed_has_no_pin_and_only_test_numbers():
    raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    for card in raw["cards"]:
        assert "pin" not in card
        for phone in card["phones"]:
            assert phone.startswith("+1555555"), phone


def test_normalize_withheld_and_empty():
    assert normalize_e164(None) is None
    assert normalize_e164("") is None
    assert normalize_e164("anonymous") is None
    assert normalize_e164("restricted") is None
    assert normalize_e164("+15555550100") == "+15555550100"


def test_last4_phone_for_logs():
    assert last4_phone("+15555550100") == "0100"
    assert last4_phone(None) == "----"
    assert last4_phone("12") == "----"


def test_lookup_hit_miss_and_multi_phone(tmp_path):
    sqlite = tmp_path / "callers.sqlite"
    store = build_caller_store(
        sqlite_path=sqlite,
        example_path=EXAMPLE,
        local_path=tmp_path / "missing.json",
    )
    card = store.lookup("+15555550100")
    assert card is not None
    assert card.last4 == "4242"
    assert card.has_pin is False
    other = store.lookup("+15555550101")
    assert other is not None
    assert other.card_id == card.card_id
    assert sorted(card.phones) == ["+15555550100", "+15555550101"]
    assert store.lookup("+15555550999") is None
    assert store.lookup("anonymous") is None
    store.close()


def test_local_overlay_adds_pin_and_extra_phone(tmp_path):
    sqlite = tmp_path / "callers.sqlite"
    local = tmp_path / "callers.local.json"
    local.write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "card_id": "stub-card-demo",
                        "pin": "9999",
                        "phones": ["+15555550102"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = build_caller_store(sqlite_path=sqlite, example_path=EXAMPLE, local_path=local)
    assert store.lookup("+15555550100") is not None
    extra = store.lookup("+15555550102")
    assert extra is not None
    assert extra.has_pin is True
    assert extra.last4 == "4242"
    store.close()


def test_local_overlay_accepts_singular_filename(tmp_path, monkeypatch):
    from core.cards import store as store_mod

    canonical = tmp_path / "callers.local.json"
    alt = tmp_path / "caller.local.json"
    alt.write_text(
        json.dumps(
            {"cards": [{"card_id": "stub-card-demo", "phones": ["+15555550109"]}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(store_mod, "DEFAULT_LOCAL_JSON", canonical)
    monkeypatch.setattr(store_mod, "ALT_LOCAL_JSON", alt)
    store = build_caller_store(
        sqlite_path=tmp_path / "callers.sqlite",
        example_path=EXAMPLE,
        local_path=None,
    )
    assert store.lookup("+15555550109") is not None
    store.close()


def test_env_override_json_merges(tmp_path):
    store = build_caller_store(
        sqlite_path=tmp_path / "db.sqlite",
        example_path=EXAMPLE,
        local_path=tmp_path / "none.json",
        override_json=json.dumps(
            {"cards": [{"card_id": "stub-card-demo", "phones": ["+15555550103"]}]}
        ),
    )
    assert store.lookup("+15555550103") is not None
    store.close()


def test_committed_seed_rejects_pin_field(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "card_id": "x",
                        "last4": "1111",
                        "balance_text": "n",
                        "currency": "USD",
                        "blocked": False,
                        "statement": "",
                        "phones": ["+15555550100"],
                        "pin": "0000",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pin"):
        build_caller_store(
            sqlite_path=tmp_path / "db.sqlite",
            example_path=bad,
            local_path=tmp_path / "none.json",
        )


def test_overlay_keeps_example_blocked_false(tmp_path):
    local = tmp_path / "callers.local.json"
    local.write_text(
        json.dumps({"cards": [{"card_id": "stub-card-demo", "pin": "9999"}]}),
        encoding="utf-8",
    )
    store = build_caller_store(
        sqlite_path=tmp_path / "db.sqlite",
        example_path=EXAMPLE,
        local_path=local,
    )
    card = store.lookup("+15555550100")
    assert card is not None
    assert card.blocked is False
    assert card.has_pin is True
    store.close()


def test_set_blocked(tmp_path):
    store = build_caller_store(
        sqlite_path=tmp_path / "db.sqlite",
        example_path=EXAMPLE,
        local_path=tmp_path / "none.json",
    )
    card = store.lookup("+15555550100")
    assert card is not None
    store.set_blocked(card.card_id, True)
    assert store.lookup("+15555550100").blocked is True
    store.close()
