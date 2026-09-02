"""Text → dense vector. Pytest uses a hash embedder; demo/Render may load BGE-M3."""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"\w+", re.UNICODE)
HASH_DIM = 256
_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "file",
        "for",
        "me",
        "now",
        "on",
        "please",
        "tell",
        "the",
        "to",
        "want",
    }
)


class TextEmbedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return shape (n, d) float32 vectors (not necessarily unit length)."""
        ...


def _normalize(text: str) -> str:
    tokens = [tok for tok in _TOKEN.findall(text.lower()) if tok not in _STOP]
    return " ".join(tokens)


class HashTokenEmbedder:
    """Offline stand-in: hashed tokens + full-string slot. No model download."""

    def __init__(self, dim: int = HASH_DIM) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        rows = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            norm = _normalize(text)
            if not norm:
                continue
            digest = hashlib.sha256(norm.encode("utf-8")).digest()
            rows[i, digest[0] % self.dim] += 4.0
            for token in norm.split():
                token_digest = hashlib.sha256(token.encode("utf-8")).digest()
                rows[i, token_digest[0] % self.dim] += 1.0
        return rows


class BgeM3Embedder:
    """In-process BAAI/bge-m3 via sentence-transformers. Lazy-loaded."""

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        self.model_name = model_name
        self._model = None

    def warm(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)

    def embed(self, texts: list[str]) -> np.ndarray:
        self.warm()
        assert self._model is not None
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)
