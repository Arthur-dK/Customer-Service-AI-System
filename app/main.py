import json
import base64
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response
from core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="Multi-lingual automated IVR, SMS, and Email support platform."
)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "project": settings.PROJECT_NAME}

@app.post("/voice/incoming")
async def voice_webhook(request: Request):
    """
    Twilio Voice Webhook endpoint.
    Returns TwiML directing Twilio to initiate a bi-directional Websocket Media Stream.
    """
    host = request.headers.get("host", "localhost:8000")
    ws_protocol = "wss" if "ngrok" in host or "https" in str(request.url) else "ws"
    ws_url = f"{ws_protocol}://{host}/media-stream"

    # TODO: Maybe implement company name
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Connecting to Customer Support</Say>
    <Connect>
        <Stream url="{ws_url}" />
    </Connect>
</Response>"""

    return Response(content=twiml_response, media_type="application/xml")


@app.websocket("/media-stream")
async def twilio_media_stream(websocket: WebSocket):
    """Bi-directional Twilio Media Stream WebSocket Handler for 8kH mu-law audio """
    await websocket.accept()

    inbound_audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
    outbound_audio_queue: asyncio.Queue[str] = asyncio.Queue()

    stream_sid: str | None = None

    async def recieve_from_twilio():
        nonlocal stream_sid
        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                event_type = message.get("event")

                if event_type == "connected":
                    print(f"INFO: Twilio Media Stream event = CONNECTED")

                elif event_type == "start":
                    stream_sid = message["start"]["streamSid"]
                    print(f"INFO: Twilio Media Stream event = START, streamSid = {stream_sid}")

                elif event_type == "media":
                    payload_b64 = message["media"]["payload"]
                    raw_audio_bytes = base64.b64decode(payload_b64)
                    await inbound_audio_queue.put(raw_audio_bytes)

                elif event_type == "stop":
                    print(f"INFO: Twilio Media Stream event = STOP, streamSid = {stream_sid}")
                    break
                
        except WebSocketDisconnect:
            print("INFO: Twilio Media Stream WebSocket disconnected")
        except Exception as e:
            print(f"ERROR: Exception in Twilio Media Stream WebSocket: {e}")

    async def send_to_twilio():
        nonlocal stream_sid
        try:
            while True:
                b64_audio_chunk = await outbound_audio_queue.get()
                if stream_sid and b64_audio_chunk:
                    media_message = {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {
                            "payload": b64_audio_chunk
                        }
                    }
                    await websocket.send_text(json.dumps(media_message))
                outbound_audio_queue.task_done()
        except asyncio.CancelledError:
            print("INFO: send_to_twilio task cancelled")
            pass
        except Exception as e:
            print(f"ERROR: Exception in send_to_twilio: {e}")

    recieve_task = asyncio.create_task(recieve_from_twilio())
    send_task = asyncio.create_task(send_to_twilio())

    try:
        await recieve_task
    finally:
        send_task.cancel()
        await websocket.close()
        print("INFO: Twilio Media Stream WebSocket closed")