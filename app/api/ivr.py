import asyncio
import base64
import json
import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.deps import (
    get_lid,
    get_phrase_cache,
    get_streaming_stt,
    get_streaming_tts,
    get_tts,
    get_vad_config,
)
from core.cards import last4_phone
from core.config import settings
from core.language import resolve_caller_locale
from services.ivr.language_selection import CLEAR_AUDIO_SENTINEL, LanguageSelector
from services.ivr.selection_store import set_last_language_selection
from services.ivr.turn_engine import PlaceholderTurnEngine
from services.ivr.turn_store import set_last_turns
from services.ivr.ttfb import TtfbHarness
from services.ivr.twiml import build_media_stream_connect_twiml
from services.ivr.vad import EnergyVad

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ivr"])


@router.post("/voice/incoming")
async def voice_webhook(request: Request):
    """
    Twilio Voice webhook.
    Parses the caller number, attaches locale metadata, and opens a media stream.
    """
    form = await request.form()
    caller_from = form.get("From") or form.get("Caller") or None
    if caller_from is not None:
        caller_from = str(caller_from)

    locale = resolve_caller_locale(caller_from)
    host = request.headers.get("host", "localhost:8000")
    ws_protocol = "wss" if "ngrok" in host or request.url.scheme == "https" else "ws"
    ws_url = f"{ws_protocol}://{host}/media-stream"

    twiml_response = build_media_stream_connect_twiml(
        ws_url,
        caller_from=locale.e164 or caller_from,
        country_code=locale.country_code if locale.country_known else None,
    )
    logger.info(
        "incoming_call from_last4=%s country=%s known=%s languages=%s",
        last4_phone(locale.e164 or caller_from),
        locale.country_code,
        locale.country_known,
        list(locale.languages),
    )
    return Response(content=twiml_response, media_type="application/xml")


@router.websocket("/media-stream")
async def twilio_media_stream(websocket: WebSocket):
    """Bi-directional Twilio Media Stream: language selection, then placeholder tasks."""
    await websocket.accept()

    inbound_audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
    outbound_audio_queue: asyncio.Queue[str] = asyncio.Queue()
    dtmf_queue: asyncio.Queue[str] = asyncio.Queue()
    stop_event = asyncio.Event()
    start_event = asyncio.Event()

    stream_sid: str | None = None
    phone_number: str | None = None

    async def receive_from_twilio():
        nonlocal stream_sid, phone_number
        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                event_type = message.get("event")

                if event_type == "connected":
                    logger.info("Twilio Media Stream CONNECTED")

                elif event_type == "start":
                    start = message.get("start", {})
                    stream_sid = start.get("streamSid") or message.get("streamSid")
                    custom = start.get("customParameters") or {}
                    phone_number = custom.get("from") or custom.get("From")
                    start_event.set()
                    logger.info(
                        "Twilio Media Stream START streamSid=%s from_last4=%s",
                        stream_sid,
                        last4_phone(phone_number),
                    )

                elif event_type == "media":
                    payload_b64 = message["media"]["payload"]
                    raw_audio_bytes = base64.b64decode(payload_b64)
                    await inbound_audio_queue.put(raw_audio_bytes)

                elif event_type == "dtmf":
                    digit = (message.get("dtmf") or {}).get("digit")
                    if digit:
                        await dtmf_queue.put(str(digit))
                        logger.info("Twilio DTMF digit=%s", digit)

                elif event_type == "stop":
                    logger.info("Twilio Media Stream STOP streamSid=%s", stream_sid)
                    stop_event.set()
                    break

        except WebSocketDisconnect:
            logger.info("Twilio Media Stream WebSocket disconnected")
            stop_event.set()
        except Exception:
            logger.exception("Twilio Media Stream receive error")
            stop_event.set()

    async def send_to_twilio():
        nonlocal stream_sid
        try:
            while not stop_event.is_set():
                try:
                    b64_audio_chunk = await asyncio.wait_for(outbound_audio_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if stream_sid and b64_audio_chunk:
                    if b64_audio_chunk == CLEAR_AUDIO_SENTINEL:
                        await websocket.send_text(
                            json.dumps({"event": "clear", "streamSid": stream_sid})
                        )
                        logger.info("Twilio Media Stream CLEAR streamSid=%s", stream_sid)
                    else:
                        media_message = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": b64_audio_chunk},
                        }
                        await websocket.send_text(json.dumps(media_message))
                outbound_audio_queue.task_done()
        except asyncio.CancelledError:
            logger.info("send_to_twilio cancelled")
            raise
        except Exception:
            logger.exception("send_to_twilio error")
            stop_event.set()

    async def run_selection():
        try:
            await asyncio.wait_for(start_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("media stream start event not received before language selection")

        set_last_language_selection(None)
        set_last_turns([])
        try:
            selector = LanguageSelector(
                tts=get_tts(),
                lid=get_lid(),
                silence_timeout_s=settings.IVR_SILENCE_TIMEOUT_S,
                min_lid_confidence=settings.IVR_MIN_LID_CONFIDENCE,
                vad_config=get_vad_config(),
                playback_realtime=settings.IVR_PLAYBACK_REALTIME,
            )
            result = await selector.run(
                phone_number=phone_number,
                inbound_audio=inbound_audio_queue,
                outbound_audio=outbound_audio_queue,
                dtmf_digits=dtmf_queue,
                stop_event=stop_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("language_selection crashed")
            return

        set_last_language_selection(result)
        if result is not None:
            logger.info(
                "language_selected language=%s method=%s metrics=%s",
                result.language,
                result.method,
                result.metrics.to_dict(),
            )
            while not inbound_audio_queue.empty():
                inbound_audio_queue.get_nowait()
            try:
                engine = PlaceholderTurnEngine(
                    language=result.language,
                    cache=get_phrase_cache(),
                    stt=get_streaming_stt(),
                    ttfb=TtfbHarness(),
                    vad=EnergyVad(get_vad_config()),
                    fallback_tts=get_streaming_tts(),
                )
                await engine.run_on_queues(
                    inbound_audio=inbound_audio_queue,
                    outbound_audio=outbound_audio_queue,
                    stop_event=stop_event,
                    play_menu=True,
                    on_turn=set_last_turns,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("placeholder_turns crashed")
        else:
            logger.warning("language_selection ended without a language")

    receive_task = asyncio.create_task(receive_from_twilio())
    send_task = asyncio.create_task(send_to_twilio())
    selection_task = asyncio.create_task(run_selection())

    try:
        await receive_task
    finally:
        stop_event.set()
        selection_task.cancel()
        send_task.cancel()
        for task in (selection_task, send_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await websocket.close()
        logger.info("Twilio Media Stream WebSocket closed")
