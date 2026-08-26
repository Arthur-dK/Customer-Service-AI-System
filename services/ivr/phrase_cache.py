"""In-memory (and optional disk) audio buffers for catalog phrase IDs.

Hot path is ``get_ready``: dict lookup only. TTS runs during warmup or first fill.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.language.phrases import PhraseCatalog, UnknownPhraseError, load_phrase_catalog
from services.ivr.tts import TextToSpeech

logger = logging.getLogger(__name__)

DEFAULT_PHRASE_CACHE_DIR = Path(".cache") / "ivr-phrases"


class PhraseNotReadyError(KeyError):
    """Requested phrase audio is not in memory; calling TTS on the hot path is forbidden."""


class PhraseAudioCache:
    """Ready μ-law buffers keyed by (phrase_id, language)."""

    def __init__(
        self,
        tts: TextToSpeech,
        catalog: PhraseCatalog | None = None,
        cache_dir: Path | None = DEFAULT_PHRASE_CACHE_DIR,
    ) -> None:
        self.tts = tts
        self.catalog = catalog or load_phrase_catalog()
        self._memory: dict[tuple[str, str], bytes] = {}
        self._cache_dir = cache_dir
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def get_ready(self, phrase_id: str, language: str) -> bytes:
        """Return cached μ-law. Never synthesizes."""
        key = self._key(phrase_id, language)
        audio = self._memory.get(key)
        if audio is None:
            raise PhraseNotReadyError(f"{phrase_id}:{key[1]}")
        return audio

    def is_ready(self, phrase_id: str, language: str) -> bool:
        return self._key(phrase_id, language) in self._memory

    async def get(self, phrase_id: str, language: str) -> bytes:
        """Memory, then disk, then TTS. Use warmup / first fill, not the 0.5s path."""
        key = self._key(phrase_id, language)
        cached = self._memory.get(key)
        if cached is not None:
            return cached

        disk_path = self._disk_path(key)
        if disk_path is not None and disk_path.exists():
            audio = disk_path.read_bytes()
            self._memory[key] = audio
            logger.info(
                "Phrase cache hit (disk) id=%s lang=%s bytes=%s",
                phrase_id,
                key[1],
                len(audio),
            )
            return audio

        text = self.catalog.text(phrase_id, language)
        audio = await self.tts.synthesize(text, language)
        self._store(key, audio)
        logger.info("Phrase cache store id=%s lang=%s bytes=%s", phrase_id, key[1], len(audio))
        return audio

    async def warmup(self, languages: tuple[str, ...] | None = None) -> int:
        """Pre-render catalog lines the TTS backend can speak."""
        langs = languages if languages is not None else self.catalog.warmup_languages
        warmed = 0
        for phrase_id in self.catalog.ids:
            for language in langs:
                if not self.catalog.has(phrase_id, language):
                    continue
                if not self.tts.supports_language(language):
                    continue
                await self.get(phrase_id, language)
                warmed += 1
        logger.info("Warmed %s IVR phrase buffer(s)", warmed)
        return warmed

    def _store(self, key: tuple[str, str], audio: bytes) -> None:
        self._memory[key] = audio
        disk_path = self._disk_path(key)
        if disk_path is not None:
            disk_path.write_bytes(audio)

    def _key(self, phrase_id: str, language: str) -> tuple[str, str]:
        if phrase_id not in self.catalog.phrases:
            raise UnknownPhraseError(phrase_id)
        return (phrase_id, language.lower())

    def _disk_path(self, key: tuple[str, str]) -> Path | None:
        if self._cache_dir is None:
            return None
        phrase_id, language = key
        return self._cache_dir / f"{phrase_id}.{language}.mulaw"
