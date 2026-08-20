"""Unit + contract checks for Twilio μ-law audio helpers."""

from pathlib import Path

import numpy as np

from services.ivr.audio import (
    TWILIO_SAMPLE_RATE,
    chunk_mulaw,
    generate_silence_mulaw,
    generate_tone_mulaw,
    mulaw_duration_ms,
    mulaw_to_float32,
    mulaw_to_pcm16,
    pcm16_rms,
    pcm16_to_mulaw,
    write_mulaw_as_wav,
)


def test_mulaw_byte_length_matches_twilio_8khz_duration():
    tone = generate_tone_mulaw(duration_ms=1000)
    assert len(tone) == TWILIO_SAMPLE_RATE
    assert abs(mulaw_duration_ms(tone) - 1000.0) < 1.0


def test_silence_is_near_zero_energy():
    silence = generate_silence_mulaw(duration_ms=200)
    pcm = mulaw_to_pcm16(silence)
    assert pcm16_rms(pcm) < 50.0


def test_tone_has_audible_energy():
    tone = generate_tone_mulaw(duration_ms=200, frequency_hz=440, amplitude=0.4)
    pcm = mulaw_to_pcm16(tone)
    assert pcm16_rms(pcm) > 1000.0


def test_mulaw_pcm_roundtrip_preserves_signal_shape():
    original = generate_tone_mulaw(duration_ms=100, frequency_hz=440, amplitude=0.5)
    pcm = mulaw_to_pcm16(original)
    again = pcm16_to_mulaw(pcm)
    assert len(again) == len(original)

    a = mulaw_to_float32(original)
    b = mulaw_to_float32(again)
    # μ-law is lossy; require strong correlation, not bit-identity.
    corr = float(np.corrcoef(a, b)[0, 1])
    assert corr > 0.95


def test_chunk_mulaw_uses_20ms_twilio_frames():
    audio = generate_silence_mulaw(duration_ms=100)
    chunks = chunk_mulaw(audio, chunk_ms=20)
    assert len(chunks) == 5
    assert all(len(chunk) == 160 for chunk in chunks)  # 8000 * 0.02


def test_write_mulaw_as_wav_is_playable_pcm(tmp_path: Path):
    tone = generate_tone_mulaw(duration_ms=250, frequency_hz=880, amplitude=0.35)
    wav_path = write_mulaw_as_wav(tone, tmp_path / "tone.wav")
    assert wav_path.exists()
    assert wav_path.stat().st_size > 44  # header + samples
