from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Multi-Lingual AI Support Engine"
    DEBUG: bool = True
    
    # Telephony & External Webhooks
    TWILIO_ACCOUNT_SID: str = "mock_sid"
    TWILIO_AUTH_TOKEN: str = "mock_token"
    
    # AI Engine Config
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    DEFAULT_EMBEDDING_MODEL: str = "bge-m3"
    DEFAULT_LLM_MODEL: str = "llama3.1:8b"
    
    class Config:
        env_file = ".env"

settings = Settings()