from fastapi import FastAPI

from app.api import email, health, ivr, sms
from core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="Multi-lingual automated IVR, SMS, and Email support platform.",
)

app.include_router(health.router)
app.include_router(ivr.router)
app.include_router(sms.router)
app.include_router(email.router)
