from fastapi import APIRouter

from core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {"status": "healthy", "project": settings.PROJECT_NAME}
