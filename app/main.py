from contextlib import asynccontextmanager, suppress
import asyncio
import logging
import sys

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


def _rss_mb() -> str:
    """Linux RSS for Render logs. Windows local boot skips this."""
    if sys.platform == "win32":
        return "n/a"
    try:
        import resource

        # Linux ru_maxrss is KiB; macOS is bytes.
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mb = raw / 1024.0 if sys.platform != "darwin" else raw / (1024.0 * 1024.0)
        return f"{mb:.0f}MB"
    except Exception:
        return "n/a"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log = logging.getLogger(__name__)
    log.info(
        "TTS spoken languages=%s rss=%s",
        list_spoken_languages(get_tts()),
        _rss_mb(),
    )

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

    async def _boot_warmup() -> None:
        # One model at a time. Parallel LID + Whisper + BGE spikes RAM and
        # gets the process killed on Render starter/standard instances.
        log.info("IVR boot warmup start rss=%s", _rss_mb())
        await _seed_callers()
        log.info("IVR boot after caller seed rss=%s", _rss_mb())
        await _warm_lid()
        log.info("IVR boot after LID rss=%s", _rss_mb())
        await _warm_stt()
        log.info("IVR boot after STT rss=%s", _rss_mb())
        await _warm_intent()
        log.info("IVR boot after intent rss=%s", _rss_mb())
        await _warm_audio()
        log.info("IVR boot warmup done rss=%s", _rss_mb())

    # Do not block /health on Edge TTS or Hugging Face (Render health checks).
    warmup_task = asyncio.create_task(_boot_warmup())
    yield
    warmup_task.cancel()
    with suppress(asyncio.CancelledError):
        await warmup_task


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
