from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "Multi-Lingual AI Support Engine"
    DEBUG: bool = True

    # Telephony & External Webhooks
    TWILIO_ACCOUNT_SID: str = "mock_sid"
    TWILIO_AUTH_TOKEN: str = "mock_token"

    # AI Engine Config
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    DEFAULT_EMBEDDING_MODEL: str = "bge-m3"
    DEFAULT_LLM_MODEL: str = "llama3.1:8b"

    # IVR TTS: one voice per language. Never speak French with an English voice.
    IVR_PIPER_MODEL_PATH: str | None = None
    IVR_PIPER_BIN: str | None = None
    IVR_PIPER_VOICES: str | None = None
    IVR_PIPER_VOICE_DIR: str | None = None
    # None = on when not Windows (Render). True/false to force. Needs outbound HTTPS.
    IVR_USE_EDGE_TTS: bool | None = None

    # IVR language ID — prefer SpeechBrain when installed; fixed LID remains fallback
    IVR_LID_FORCE_LANGUAGE: str | None = None
    IVR_USE_SPEECHBRAIN_LID: bool = True
    IVR_SPEECHBRAIN_MODEL: str = "speechbrain/lang-id-voxlingua107-ecapa"
    IVR_MIN_LID_CONFIDENCE: float = 0.15

    # IVR language selection runtime
    IVR_SILENCE_TIMEOUT_S: float = 5.0
    # False = burst frames to Twilio (it buffers). True often causes choppy gaps
    # because event-loop sleep + WS latency exceeds 20ms per frame.
    IVR_PLAYBACK_REALTIME: bool = False
    IVR_VAD_RMS_THRESHOLD: float = 250.0

    # Comma-separated stub transcripts for live smoke (e.g. "balance,goodbye").
    # Ignored when IVR_STT_BACKEND=sapi.
    IVR_STT_SCRIPT: str | None = None
    # scripted (default, CI) | sapi (Windows grammar) | whisper (local faster-whisper)
    IVR_STT_BACKEND: str = "scripted"
    IVR_WHISPER_MODEL: str = "base"

    # Shared stub caller/card store (IVR, SMS, Email). PIN only via local overlay / env.
    CALLERS_SQLITE: str | None = None
    CALLERS_EXAMPLE_JSON: str | None = None
    CALLERS_LOCAL_JSON: str | None = None
    CALLER_OVERRIDE_JSON: str | None = None

    # Shared intent router (IVR, SMS, Email). fake = hash embedder (CI); bge = in-process BGE-M3.
    INTENT_EMBEDDER: str = "fake"
    INTENT_BGE_MODEL: str = "BAAI/bge-m3"
    INTENT_MIN_SCORE: float = 0.28
    INTENT_MARGIN: float = 0.04

    @field_validator("IVR_LID_FORCE_LANGUAGE", mode="before")
    @classmethod
    def _empty_force_language(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if text.lower() in ("", "none", "null", "false"):
            return None
        return text


settings = Settings()
