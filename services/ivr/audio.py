"""μ-law / PCM helpers for Twilio Media Streams (8 kHz mono)."""

from __future__ import annotations

import math
import struct
import wave
from array import array
from pathlib import Path

import numpy as np

TWILIO_SAMPLE_RATE = 8000
MULAW_MAX = 0x1FFF
MULAW_BIAS = 0x84

try:
    import audioop as _audioop  # type: ignore[import-not-found]
except ModuleNotFoundError:  # Python 3.13+
    _audioop = None


def mulaw_to_pcm16(mulaw_bytes: bytes) -> bytes:
    if _audioop is not None:
        return _audioop.ulaw2lin(mulaw_bytes, 2)
    return _mulaw_to_pcm16_pure(mulaw_bytes)


def pcm16_to_mulaw(pcm_bytes: bytes) -> bytes:
    if _audioop is not None:
        return _audioop.lin2ulaw(pcm_bytes, 2)
    return _pcm16_to_mulaw_pure(pcm_bytes)


def mulaw_to_float32(mulaw_bytes: bytes) -> np.ndarray:
    pcm = np.frombuffer(mulaw_to_pcm16(mulaw_bytes), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def float32_to_mulaw(samples: np.ndarray, sample_rate: int = TWILIO_SAMPLE_RATE) -> bytes:
    if sample_rate != TWILIO_SAMPLE_RATE:
        samples = _resample_linear(samples, sample_rate, TWILIO_SAMPLE_RATE)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    return pcm16_to_mulaw(pcm.tobytes())


def pcm16_rms(pcm_bytes: bytes) -> float:
    if not pcm_bytes:
        return 0.0
    samples = array("h")
    samples.frombytes(pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % 2)])
    if not samples:
        return 0.0
    acc = 0.0
    for sample in samples:
        acc += float(sample) * float(sample)
    return math.sqrt(acc / len(samples))


def mulaw_duration_ms(mulaw_bytes: bytes, sample_rate: int = TWILIO_SAMPLE_RATE) -> float:
    if sample_rate <= 0:
        return 0.0
    return 1000.0 * len(mulaw_bytes) / float(sample_rate)


def generate_silence_mulaw(duration_ms: int, sample_rate: int = TWILIO_SAMPLE_RATE) -> bytes:
    frames = max(1, int(sample_rate * duration_ms / 1000))
    return b"\xff" * frames


def generate_tone_mulaw(
    duration_ms: int,
    frequency_hz: float = 440.0,
    amplitude: float = 0.2,
    sample_rate: int = TWILIO_SAMPLE_RATE,
) -> bytes:
    frames = max(1, int(sample_rate * duration_ms / 1000))
    t = np.arange(frames, dtype=np.float32) / float(sample_rate)
    wave_samples = (amplitude * np.sin(2.0 * np.pi * frequency_hz * t)).astype(np.float32)
    return float32_to_mulaw(wave_samples, sample_rate=sample_rate)


def chunk_mulaw(mulaw_bytes: bytes, chunk_ms: int = 20, sample_rate: int = TWILIO_SAMPLE_RATE) -> list[bytes]:
    frame_size = max(1, int(sample_rate * chunk_ms / 1000))
    return [mulaw_bytes[i : i + frame_size] for i in range(0, len(mulaw_bytes), frame_size)]


def write_mulaw_as_wav(mulaw_bytes: bytes, path: str | Path, sample_rate: int = TWILIO_SAMPLE_RATE) -> Path:
    """Decode μ-law to 16-bit PCM WAV so it can be played in a normal audio player."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = mulaw_to_pcm16(mulaw_bytes)
    with wave.open(str(out), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return out


def _resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or samples.size == 0:
        return samples
    duration = samples.size / float(src_rate)
    dst_len = max(1, int(duration * dst_rate))
    x_old = np.linspace(0.0, 1.0, num=samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=dst_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def resample_pcm16(pcm_bytes: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-resample int16 PCM between sample rates (e.g. Twilio 8 kHz → LID 16 kHz)."""
    if src_rate == dst_rate or not pcm_bytes:
        return pcm_bytes
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    resampled = _resample_linear(samples, src_rate, dst_rate)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def _mulaw_to_pcm16_pure(mulaw_bytes: bytes) -> bytes:
    out = bytearray(len(mulaw_bytes) * 2)
    for index, value in enumerate(mulaw_bytes):
        mu = ~value & 0xFF
        sign = mu & 0x80
        exponent = (mu >> 4) & 0x07
        mantissa = mu & 0x0F
        sample = ((mantissa << 3) + MULAW_BIAS) << exponent
        sample -= MULAW_BIAS
        if sign:
            sample = -sample
        struct.pack_into("<h", out, index * 2, sample)
    return bytes(out)


def _pcm16_to_mulaw_pure(pcm_bytes: bytes) -> bytes:
    out = bytearray(len(pcm_bytes) // 2)
    for index in range(0, len(pcm_bytes) - 1, 2):
        sample = struct.unpack_from("<h", pcm_bytes, index)[0]
        sign = 0x80 if sample < 0 else 0x00
        if sample < 0:
            sample = -sample
        sample = min(sample + MULAW_BIAS, MULAW_MAX)
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not (sample & mask):
            mask >>= 1
            exponent -= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        out[index // 2] = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return bytes(out)
