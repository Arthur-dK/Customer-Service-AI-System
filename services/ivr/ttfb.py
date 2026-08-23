"""Time-To-First-Audio-Byte (TTFB) timer for IVR reply turns.

Clock start: caller speech has ended (energy VAD `speech_end` — volume of the
talker's voice has dropped for the configured hangover).

Clock stop: the system hands the first reply μ-law bytes to the outbound
sender (enqueue / first media frame). This is not handset acoustic delay.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from time import perf_counter

# Canned menus / errors / placeholder task prompts (this feature).
CANNED_TTFB_BUDGET_MS = 500.0
# Future LLM-generated sentences only — not used on the hot path yet.
LLM_TTFB_BUDGET_MS = 5000.0


class ReplyKind(str, Enum):
    CANNED = "canned"
    LLM = "llm"


@dataclass(frozen=True)
class TtfbSample:
    ttfb_ms: float
    reply_kind: ReplyKind
    within_budget: bool


@dataclass
class TtfbHarness:
    """Async-safe per-call harness. Mark methods are sync and cheap on the event loop.

    One harness instance follows a call. Call ``mark_speech_end`` then
    ``mark_first_audio_byte`` for each listen→reply turn. Extra first-audio
    marks on the same turn are ignored. Speech-end while a turn is already
    open restarts the clock (new utterance).
    """

    clock: Callable[[], float] = perf_counter
    canned_budget_ms: float = CANNED_TTFB_BUDGET_MS
    llm_budget_ms: float = LLM_TTFB_BUDGET_MS
    samples: list[TtfbSample] = field(default_factory=list)
    _started_at: float | None = field(default=None, repr=False)

    def mark_speech_end(self) -> None:
        """Start (or restart) the turn clock when the caller stops talking."""
        self._started_at = self.clock()

    def mark_first_audio_byte(self, *, reply_kind: ReplyKind = ReplyKind.CANNED) -> TtfbSample | None:
        """Stop the clock when the first reply audio byte is sent outbound.

        Returns the sample, or None if speech-end was never marked.
        """
        if self._started_at is None:
            return None
        elapsed_ms = (self.clock() - self._started_at) * 1000.0
        budget = self.llm_budget_ms if reply_kind is ReplyKind.LLM else self.canned_budget_ms
        sample = TtfbSample(
            ttfb_ms=elapsed_ms,
            reply_kind=reply_kind,
            within_budget=elapsed_ms <= budget,
        )
        self.samples.append(sample)
        self._started_at = None
        return sample

    def reset_turn(self) -> None:
        """Drop an in-flight start without recording a sample (barge-in / hangup)."""
        self._started_at = None

    @property
    def turn_open(self) -> bool:
        return self._started_at is not None

    def canned_samples(self) -> list[TtfbSample]:
        return [s for s in self.samples if s.reply_kind is ReplyKind.CANNED]

    def typical_canned_ttfb_ms(self) -> float | None:
        """Median canned TTFB — SLO uses 'usually' not every single run."""
        canned = [s.ttfb_ms for s in self.canned_samples()]
        if not canned:
            return None
        return float(statistics.median(canned))

    def canned_typical_within_budget(self) -> bool:
        typical = self.typical_canned_ttfb_ms()
        if typical is None:
            return False
        return typical <= self.canned_budget_ms
