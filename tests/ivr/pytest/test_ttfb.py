"""Unit checks for the IVR TTFB harness (fake clock; no vendors)."""

from __future__ import annotations

import asyncio

import pytest

from services.ivr.ttfb import (
    CANNED_TTFB_BUDGET_MS,
    LLM_TTFB_BUDGET_MS,
    ReplyKind,
    TtfbHarness,
)


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_canned_ttfb_from_speech_end_to_first_audio_byte():
    clock = FakeClock()
    harness = TtfbHarness(clock=clock)

    harness.mark_speech_end()
    clock.advance(0.12)
    sample = harness.mark_first_audio_byte()

    assert sample is not None
    assert sample.reply_kind is ReplyKind.CANNED
    assert sample.ttfb_ms == pytest.approx(120.0)
    assert sample.within_budget is True
    assert harness.turn_open is False


def test_first_audio_without_speech_end_is_ignored():
    harness = TtfbHarness(clock=FakeClock())
    assert harness.mark_first_audio_byte() is None
    assert harness.samples == []


def test_second_first_audio_on_same_turn_is_ignored():
    clock = FakeClock()
    harness = TtfbHarness(clock=clock)
    harness.mark_speech_end()
    clock.advance(0.05)
    first = harness.mark_first_audio_byte()
    clock.advance(1.0)
    second = harness.mark_first_audio_byte()

    assert first is not None
    assert second is None
    assert len(harness.samples) == 1
    assert harness.samples[0].ttfb_ms == pytest.approx(50.0)


def test_speech_end_while_turn_open_restarts_clock():
    clock = FakeClock()
    harness = TtfbHarness(clock=clock)
    harness.mark_speech_end()
    clock.advance(0.4)
    harness.mark_speech_end()
    clock.advance(0.05)
    sample = harness.mark_first_audio_byte()

    assert sample is not None
    assert sample.ttfb_ms == pytest.approx(50.0)
    assert len(harness.samples) == 1


def test_reset_turn_drops_open_clock():
    clock = FakeClock()
    harness = TtfbHarness(clock=clock)
    harness.mark_speech_end()
    harness.reset_turn()
    clock.advance(0.2)
    assert harness.mark_first_audio_byte() is None
    assert harness.samples == []


def test_llm_budget_is_five_seconds_not_half_second():
    clock = FakeClock()
    harness = TtfbHarness(clock=clock)
    harness.mark_speech_end()
    clock.advance(2.0)
    sample = harness.mark_first_audio_byte(reply_kind=ReplyKind.LLM)

    assert sample is not None
    assert sample.ttfb_ms == pytest.approx(2000.0)
    assert sample.within_budget is True
    assert LLM_TTFB_BUDGET_MS == 5000.0
    assert CANNED_TTFB_BUDGET_MS == 500.0


def test_llm_over_five_seconds_is_outside_budget():
    clock = FakeClock()
    harness = TtfbHarness(clock=clock)
    harness.mark_speech_end()
    clock.advance(5.01)
    sample = harness.mark_first_audio_byte(reply_kind=ReplyKind.LLM)
    assert sample is not None
    assert sample.within_budget is False


def test_canned_slo_uses_median_not_every_sample():
    clock = FakeClock()
    harness = TtfbHarness(clock=clock)

    delays = (0.10, 0.20, 0.60)
    for delay in delays:
        harness.mark_speech_end()
        clock.advance(delay)
        harness.mark_first_audio_byte()

    assert harness.typical_canned_ttfb_ms() == pytest.approx(200.0)
    assert harness.canned_typical_within_budget() is True
    assert harness.canned_samples()[2].within_budget is False


def test_canned_slo_fails_when_median_exceeds_budget():
    clock = FakeClock()
    harness = TtfbHarness(clock=clock)
    for delay in (0.60, 0.70, 0.80):
        harness.mark_speech_end()
        clock.advance(delay)
        harness.mark_first_audio_byte()

    assert harness.canned_typical_within_budget() is False


def test_empty_harness_is_not_within_budget():
    assert TtfbHarness(clock=FakeClock()).canned_typical_within_budget() is False


@pytest.mark.asyncio
async def test_async_event_loop_delay_is_measured():
    harness = TtfbHarness()
    harness.mark_speech_end()
    await asyncio.sleep(0.05)
    sample = harness.mark_first_audio_byte()

    assert sample is not None
    assert 40.0 <= sample.ttfb_ms <= 400.0
    assert sample.within_budget is True
