"""Unit + contract checks for energy VAD."""

from services.ivr.audio import chunk_mulaw, generate_silence_mulaw, generate_tone_mulaw, pcm16_rms
from services.ivr.vad import EnergyVad, VadConfig


def test_vad_ignores_short_noise_blip():
    vad = EnergyVad(VadConfig(rms_threshold=500, speech_start_ms=120, speech_end_ms=400))
    # 40ms of tone is below speech_start_ms
    tone = generate_tone_mulaw(duration_ms=20, amplitude=0.5)
    events = []
    for _ in range(2):
        events.extend(vad.process_mulaw(tone))
    assert events == []
    assert vad.in_speech is False


def test_vad_detects_speech_start_then_end_with_buffer():
    vad = EnergyVad(VadConfig(rms_threshold=500, speech_start_ms=40, speech_end_ms=60))
    events = []

    tone = generate_tone_mulaw(duration_ms=20, amplitude=0.5)
    for _ in range(8):
        events.extend(vad.process_mulaw(tone))

    assert any(event.kind == "speech_start" for event in events)
    assert vad.in_speech is True

    silence = generate_silence_mulaw(duration_ms=20)
    end_events = []
    for _ in range(8):
        end_events.extend(vad.process_mulaw(silence))

    ends = [event for event in end_events if event.kind == "speech_end"]
    assert len(ends) == 1
    assert len(ends[0].audio_pcm16) > 0
    assert pcm16_rms(ends[0].audio_pcm16) > 500
    assert vad.in_speech is False


def test_vad_reset_clears_state():
    vad = EnergyVad(VadConfig(rms_threshold=500, speech_start_ms=40, speech_end_ms=60))
    tone = generate_tone_mulaw(duration_ms=20, amplitude=0.5)
    for _ in range(8):
        vad.process_mulaw(tone)
    assert vad.in_speech is True
    vad.reset()
    assert vad.in_speech is False


def test_vad_on_chunked_stream_matches_twilio_frame_size():
    vad = EnergyVad(VadConfig(rms_threshold=500, speech_start_ms=100, speech_end_ms=200))
    # silence -> speech -> silence, in 20ms Twilio-sized frames
    stream = (
        generate_silence_mulaw(200)
        + generate_tone_mulaw(400, amplitude=0.45)
        + generate_silence_mulaw(400)
    )
    events = []
    for chunk in chunk_mulaw(stream, chunk_ms=20):
        assert len(chunk) <= 160
        events.extend(vad.process_mulaw(chunk))

    kinds = [event.kind for event in events]
    assert kinds.count("speech_start") == 1
    assert kinds.count("speech_end") == 1
    speech_end = next(event for event in events if event.kind == "speech_end")
    # Captured audio should cover most of the 400ms tone (plus trailing silence hangover).
    assert len(speech_end.audio_pcm16) >= int(0.3 * 8000) * 2
