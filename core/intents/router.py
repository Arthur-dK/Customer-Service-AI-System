"""Cosine prototype router onto a closed card-action set (no keywords)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from core.intents.actions import ALL_ACTIONS, CardAction
from core.intents.embedder import HashTokenEmbedder, TextEmbedder

_PROTOTYPES_PATH = Path(__file__).resolve().parent / "prototypes.json"

DEFAULT_MIN_SCORE = 0.28
DEFAULT_MARGIN = 0.04


@dataclass(frozen=True)
class RouteResult:
    action: CardAction | None
    score: float
    second_score: float
    margin: float
    rejected: bool


def load_prototypes(path: Path | None = None) -> dict[str, dict[str, list[str]]]:
    data = json.loads((path or _PROTOTYPES_PATH).read_text(encoding="utf-8"))
    return data["actions"]


def _cosine_rows(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query.astype(np.float64)
    m = matrix.astype(np.float64)
    qn = np.linalg.norm(q)
    if qn == 0.0:
        return np.zeros(m.shape[0], dtype=np.float64)
    mn = np.linalg.norm(m, axis=1)
    mn = np.where(mn == 0.0, 1.0, mn)
    return (m @ q) / (mn * qn)


class IntentRouter:
    def __init__(
        self,
        *,
        embedder: TextEmbedder | None = None,
        prototypes: dict[str, dict[str, list[str]]] | None = None,
        min_score: float = DEFAULT_MIN_SCORE,
        margin: float = DEFAULT_MARGIN,
    ) -> None:
        self.embedder = embedder or HashTokenEmbedder()
        self.prototypes = prototypes or load_prototypes()
        self.min_score = min_score
        self.margin = margin
        self._labels: list[CardAction] = []
        self._matrix: np.ndarray | None = None

    def warm(self) -> None:
        labels: list[CardAction] = []
        phrases: list[str] = []
        for action in ALL_ACTIONS:
            per_lang = self.prototypes.get(action.value) or {}
            for examples in per_lang.values():
                for phrase in examples:
                    labels.append(action)
                    phrases.append(phrase)
        if not phrases:
            raise ValueError("intent prototypes are empty")
        self._labels = labels
        self._matrix = self.embedder.embed(phrases)

    def route(self, text: str) -> RouteResult:
        if self._matrix is None:
            self.warm()
        assert self._matrix is not None
        query = self.embedder.embed([text])[0]
        sims = _cosine_rows(query, self._matrix)
        best_per_action: dict[CardAction, float] = {action: -1.0 for action in ALL_ACTIONS}
        for label, score in zip(self._labels, sims, strict=True):
            if score > best_per_action[label]:
                best_per_action[label] = float(score)
        ranked = sorted(best_per_action.items(), key=lambda item: item[1], reverse=True)
        top_action, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else -1.0
        gap = top_score - second_score
        rejected = top_score < self.min_score or gap < self.margin
        return RouteResult(
            action=None if rejected else top_action,
            score=top_score,
            second_score=second_score,
            margin=gap,
            rejected=rejected,
        )


def build_intent_router(
    *,
    embedder_kind: str = "fake",
    model_name: str = "BAAI/bge-m3",
    min_score: float = DEFAULT_MIN_SCORE,
    margin: float = DEFAULT_MARGIN,
) -> IntentRouter:
    kind = embedder_kind.strip().lower()
    if kind in ("bge", "bge-m3", "bge_m3"):
        from core.intents.embedder import BgeM3Embedder

        embedder: TextEmbedder = BgeM3Embedder(model_name=model_name)
    else:
        embedder = HashTokenEmbedder()
    return IntentRouter(embedder=embedder, min_score=min_score, margin=margin)


def build_intent_router_from_settings(settings: Any | None = None) -> IntentRouter:
    if settings is None:
        from core.config import settings as app_settings

        settings = app_settings
    return build_intent_router(
        embedder_kind=settings.INTENT_EMBEDDER,
        model_name=settings.INTENT_BGE_MODEL,
        min_score=settings.INTENT_MIN_SCORE,
        margin=settings.INTENT_MARGIN,
    )
