from contextlib import asynccontextmanager, suppress
import asyncio
import logging

from fastapi import FastAPI

from app.api import email, health, ivr, sms
from app.deps import (
    get_caller_store,
    get_intent_router,
    get_lid,
    get_phrase_cache,
    get_streaming_stt,
    get_tts,
)
from core.config import settings
from services.ivr.streaming_stt import parse_stt_script
from services.ivr.tts import list_spoken_languages, warm_language_selection_prompts

logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log = logging.getLogger(__name__)
    log.info("TTS spoken languages=%s", list_spoken_languages(get_tts()))

    script = parse_stt_script(settings.IVR_STT_SCRIPT)
    stt = get_streaming_stt()
    log.info(
        "IVR STT backend=%s script=%s",
        type(stt).__name__,
        script or "(none)",
    )

    async def _warm_audio() -> None:
        try:
            await warm_language_selection_prompts(get_tts())
        except Exception:
            log.exception("TTS prompt warmup failed; first call may be slow")
        try:
            warmed = await get_phrase_cache().warmup()
            log.info("IVR phrase cache warmed count=%s", warmed)
        except Exception:
            log.exception("IVR phrase cache warmup failed; canned lines may synth on first use")

    async def _warm_lid() -> None:
        try:
            lid = await asyncio.to_thread(get_lid)
            log.info(
                "IVR LID warmed class=%s backend=%s",
                type(lid).__name__,
                getattr(lid, "backend", type(lid).__name__),
            )
        except Exception:
            log.exception("IVR LID warmup failed; first call may be slow")

    async def _warm_stt() -> None:
        try:
            stt = get_streaming_stt()
            warm = getattr(stt, "warm", None)
            if warm is None:
                return
            await asyncio.to_thread(warm)
            log.info("IVR STT warmed class=%s", type(stt).__name__)
        except Exception:
            log.exception("IVR STT warmup failed; first utterance may be slow")

    async def _seed_callers() -> None:
        try:
            store = await asyncio.to_thread(get_caller_store)
            demo = store.lookup("+15555550100")
            log.info(
                "caller store seeded demo_card=%s",
                None if demo is None else demo.card_id,
            )
        except Exception:
            log.exception("caller store seed failed; lookups may miss until fixed")

    async def _warm_intent() -> None:
        try:
            router = await asyncio.to_thread(get_intent_router)
            log.info("IVR intent router warmed class=%s", type(router.embedder).__name__)
        except Exception:
            log.exception("IVR intent router warmup failed; first route may be slow")

    # Do not block /health on Edge TTS or Hugging Face (Render health checks).
    warmup_tasks = (
        asyncio.create_task(_warm_audio()),
        asyncio.create_task(_warm_lid()),
        asyncio.create_task(_warm_stt()),
        asyncio.create_task(_seed_callers()),
        asyncio.create_task(_warm_intent()),
    )
    yield
    for task in warmup_tasks:
        task.cancel()
    for task in warmup_tasks:
        with suppress(asyncio.CancelledError):
            await task


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
