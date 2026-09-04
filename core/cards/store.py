"""Git-safe stub card/caller store shared by IVR, SMS, and Email.

Channel adapters live under ``services/ivr``, ``services/sms``, and
``services/email``. Card identity and stub issuer data live here in ``core``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from core.cards.e164 import normalize_e164
from core.cards.models import StubCard

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXAMPLE_JSON = REPO_ROOT / "data" / "callers.example.json"
DEFAULT_LOCAL_JSON = REPO_ROOT / "data" / "callers.local.json"
# Common misspelling of the gitignored overlay; do not commit this file either.
ALT_LOCAL_JSON = REPO_ROOT / "data" / "caller.local.json"
DEFAULT_SQLITE = REPO_ROOT / "data" / "callers.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    card_id TEXT PRIMARY KEY,
    last4 TEXT NOT NULL,
    balance_text TEXT NOT NULL,
    currency TEXT NOT NULL,
    blocked INTEGER NOT NULL,
    statement TEXT NOT NULL,
    pin TEXT
);
CREATE TABLE IF NOT EXISTS phones (
    e164 TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    FOREIGN KEY (card_id) REFERENCES cards(card_id)
);
"""


class CallerStore:
    def __init__(self, sqlite_path: Path) -> None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = sqlite_path
        self._conn = sqlite3.connect(str(sqlite_path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def lookup(self, phone_number: str | None) -> StubCard | None:
        e164 = normalize_e164(phone_number)
        if e164 is None:
            return None
        row = self._conn.execute(
            """
            SELECT c.card_id, c.last4, c.balance_text, c.currency, c.blocked,
                   c.statement, c.pin
            FROM phones p
            JOIN cards c ON c.card_id = p.card_id
            WHERE p.e164 = ?
            """,
            (e164,),
        ).fetchone()
        if row is None:
            return None
        phones = tuple(
            r[0]
            for r in self._conn.execute(
                "SELECT e164 FROM phones WHERE card_id = ? ORDER BY e164",
                (row[0],),
            )
        )
        return StubCard(
            card_id=row[0],
            last4=row[1],
            balance_text=row[2],
            currency=row[3],
            blocked=bool(row[4]),
            statement=row[5],
            phones=phones,
            has_pin=bool(row[6]),
        )

    def set_blocked(self, card_id: str, blocked: bool) -> None:
        self._conn.execute(
            "UPDATE cards SET blocked = ? WHERE card_id = ?",
            (1 if blocked else 0, card_id),
        )
        self._conn.commit()

    def upsert_card(self, payload: dict[str, Any], *, allow_pin: bool) -> None:
        card_id = str(payload["card_id"])
        existing = self._conn.execute(
            "SELECT last4, balance_text, currency, blocked, statement, pin FROM cards WHERE card_id = ?",
            (card_id,),
        ).fetchone()
        last4 = str(payload.get("last4") or (existing[0] if existing else "0000"))
        balance_text = str(
            payload.get("balance_text") or (existing[1] if existing else "zero")
        )
        currency = str(payload.get("currency") or (existing[2] if existing else "USD"))
        if "blocked" in payload:
            blocked = 1 if payload["blocked"] else 0
        else:
            blocked = int(existing[3]) if existing else 0
        statement = str(payload.get("statement") or (existing[4] if existing else ""))
        pin: str | None
        if allow_pin and "pin" in payload and payload["pin"] is not None:
            pin = str(payload["pin"])
        elif existing:
            pin = existing[5]
        else:
            pin = None
        self._conn.execute(
            """
            INSERT INTO cards (card_id, last4, balance_text, currency, blocked, statement, pin)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(card_id) DO UPDATE SET
                last4 = excluded.last4,
                balance_text = excluded.balance_text,
                currency = excluded.currency,
                blocked = excluded.blocked,
                statement = excluded.statement,
                pin = excluded.pin
            """,
            (card_id, last4, balance_text, currency, blocked, statement, pin),
        )
        for raw in payload.get("phones") or ():
            e164 = normalize_e164(str(raw))
            if e164 is None:
                continue
            self._conn.execute(
                "INSERT OR REPLACE INTO phones (e164, card_id) VALUES (?, ?)",
                (e164, card_id),
            )
        self._conn.commit()


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"cards": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_text(raw: str | None) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        return {"cards": []}
    return json.loads(raw)


def _resolve_local_overlay_path(local_path: Path | None) -> Path:
    path = local_path or DEFAULT_LOCAL_JSON
    if path.is_file():
        return path
    if path == DEFAULT_LOCAL_JSON and ALT_LOCAL_JSON.is_file():
        logger.warning(
            "Loading overlay %s; rename it to %s (the name the store and gitignore use).",
            ALT_LOCAL_JSON,
            DEFAULT_LOCAL_JSON,
        )
        return ALT_LOCAL_JSON
    return path


def build_caller_store(
    *,
    sqlite_path: Path | None = None,
    example_path: Path | None = None,
    local_path: Path | None = None,
    override_json: str | None = None,
) -> CallerStore:
    store = CallerStore(sqlite_path or DEFAULT_SQLITE)
    example = _load_json_file(example_path or DEFAULT_EXAMPLE_JSON)
    for card in example.get("cards") or []:
        if "pin" in card:
            raise ValueError("committed caller seed must not include pin")
        store.upsert_card(card, allow_pin=False)
    local = _load_json_file(_resolve_local_overlay_path(local_path))
    for card in local.get("cards") or []:
        store.upsert_card(card, allow_pin=True)
    for card in _load_json_text(override_json).get("cards") or []:
        store.upsert_card(card, allow_pin=True)
    return store


def build_caller_store_from_settings(settings: Any | None = None) -> CallerStore:
    """Use ``core.config.settings`` paths when present."""
    if settings is None:
        from core.config import settings as app_settings

        settings = app_settings
    sqlite = Path(settings.CALLERS_SQLITE) if settings.CALLERS_SQLITE else None
    example = (
        Path(settings.CALLERS_EXAMPLE_JSON) if settings.CALLERS_EXAMPLE_JSON else None
    )
    local = Path(settings.CALLERS_LOCAL_JSON) if settings.CALLERS_LOCAL_JSON else None
    return build_caller_store(
        sqlite_path=sqlite,
        example_path=example,
        local_path=local,
        override_json=settings.CALLER_OVERRIDE_JSON,
    )
