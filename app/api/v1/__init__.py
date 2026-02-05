"""API v1 패키지"""

from fastapi import APIRouter

from .interview import router as interview_router

router = APIRouter(prefix="/v1")
router.include_router(interview_router)

__all__ = ["router"]
