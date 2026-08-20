"""Energy-based voice activity detection for 8 kHz μ-law / PCM streams."""

from __future__ import annotations

from dataclasses import dataclass

from services.ivr.audio import TWILIO_SAMPLE_RATE, mulaw_to_pcm16, pcm16_rms


@dataclass
class VadConfig:
    """Tunable thresholds for telephony energy VAD."""

    rms_threshold: float = 500.0
    speech_start_ms: int = 120
    speech_end_ms: int = 400
    sample_rate: int = TWILIO_SAMPLE_RATE


@dataclass
class VadEvent:
    kind: str  # "speech_start" | "speech_end"
    audio_pcm16: bytes = b""


class EnergyVad:
    """
    Simple RMS VAD for Twilio 8 kHz frames.

    Feed successive media chunks; emits speech_start after sustained energy,
    then speech_end (with buffered PCM) after sustained silence.
    """

    def __init__(self, config: VadConfig | None = None) -> None:
        self.config = config or VadConfig()
        self._in_speech = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._buffer = bytearray()

    def reset(self) -> None:
        self._in_speech = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._buffer.clear()

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def process_mulaw(self, mulaw_chunk: bytes) -> list[VadEvent]:
        duration_ms = self._duration_ms(len(mulaw_chunk), bytes_per_sample=1)
        return self.process_pcm16(mulaw_to_pcm16(mulaw_chunk), duration_ms=duration_ms)

    def process_pcm16(self, pcm_chunk: bytes, duration_ms: int | None = None) -> list[VadEvent]:
        if not pcm_chunk:
            return []

        frame_ms = (
            duration_ms
            if duration_ms is not None
            else self._duration_ms(len(pcm_chunk), bytes_per_sample=2)
        )
        rms = pcm16_rms(pcm_chunk)
        events: list[VadEvent] = []

        if rms >= self.config.rms_threshold:
            self._silence_ms = 0
            self._speech_ms += frame_ms
            self._buffer.extend(pcm_chunk)
            if not self._in_speech and self._speech_ms >= self.config.speech_start_ms:
                self._in_speech = True
                events.append(VadEvent(kind="speech_start"))
        else:
            if self._in_speech:
                self._buffer.extend(pcm_chunk)
                self._silence_ms += frame_ms
                if self._silence_ms >= self.config.speech_end_ms:
                    events.append(VadEvent(kind="speech_end", audio_pcm16=bytes(self._buffer)))
                    self.reset()
            else:
                self._speech_ms = 0
                self._buffer.clear()

        return events

    @staticmethod
    def _duration_ms(num_bytes: int, bytes_per_sample: int) -> int:
        samples = max(1, num_bytes // max(1, bytes_per_sample))
        return max(1, int(1000 * samples / TWILIO_SAMPLE_RATE))
