"""Shared FastAPI / IVR dependencies."""

from functools import lru_cache

from core.config import settings
from services.ivr.lid import LanguageIdentifier, build_default_lid
from services.ivr.phrase_cache import PhraseAudioCache
from services.ivr.streaming_stt import StreamingSpeechToText, build_default_streaming_stt, parse_stt_script
from services.ivr.streaming_tts import StreamingTextToSpeech, build_default_streaming_tts
from services.ivr.tts import TextToSpeech, build_default_tts
from services.ivr.vad import VadConfig


@lru_cache(maxsize=1)
def get_tts() -> TextToSpeech:
    return build_default_tts(
        piper_model_path=settings.IVR_PIPER_MODEL_PATH,
        piper_bin=settings.IVR_PIPER_BIN,
        piper_voices=settings.IVR_PIPER_VOICES,
        piper_voice_dir=settings.IVR_PIPER_VOICE_DIR,
        use_edge=settings.IVR_USE_EDGE_TTS,
    )


@lru_cache(maxsize=1)
def get_lid() -> LanguageIdentifier:
    return build_default_lid(
        prefer_speechbrain=settings.IVR_USE_SPEECHBRAIN_LID,
        force_language=settings.IVR_LID_FORCE_LANGUAGE,
        speechbrain_model=settings.IVR_SPEECHBRAIN_MODEL,
    )


def get_vad_config() -> VadConfig:
    return VadConfig(rms_threshold=settings.IVR_VAD_RMS_THRESHOLD)


@lru_cache(maxsize=1)
def get_phrase_cache() -> PhraseAudioCache:
    return PhraseAudioCache(get_tts())


@lru_cache(maxsize=1)
def get_streaming_tts() -> StreamingTextToSpeech:
    return build_default_streaming_tts(get_tts())


def get_streaming_stt() -> StreamingSpeechToText:
    return build_default_streaming_stt(
        finals=parse_stt_script(settings.IVR_STT_SCRIPT),
        backend=settings.IVR_STT_BACKEND,
    )
