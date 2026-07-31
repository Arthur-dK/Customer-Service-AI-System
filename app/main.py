from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="Multi-lingual automated IVR, SMS, and Email support platform."
)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "project": settings.PROJECT_NAME}

@app.websocket("/media-stream")
async def twilio_media_stream(websocket: WebSocket):
    """Twilio Media Stream WebSocket Handler Stub"""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            # Raw mu-law audio frame processing pipeline will be attached here
    except WebSocketDisconnect:
        pass