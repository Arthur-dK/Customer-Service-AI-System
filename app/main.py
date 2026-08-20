from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI

from app.api import email, health, ivr, sms
from app.deps import get_lid, get_tts
from core.config import settings
from services.ivr.tts import warm_language_selection_prompts

logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log = logging.getLogger(__name__)
    # Pay Windows SAPI/PowerShell cost once at boot so the first live prompt is instant.
    try:
        await warm_language_selection_prompts(get_tts())
    except Exception:
        log.exception("TTS prompt warmup failed; first call may be slow")

    # Load SpeechBrain (or fixed LID) once at boot — first-call get_lid() was ~11s on a live call.
    try:
        lid = await asyncio.to_thread(get_lid)
        log.info("IVR LID warmed backend=%s", type(lid).__name__)
    except Exception:
        log.exception("IVR LID warmup failed; first call may be slow")
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="Multi-lingual automated IVR, SMS, and Email support platform.",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(ivr.router)
app.include_router(sms.router)
app.include_router(email.router)
