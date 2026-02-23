"""API v1 패키지"""

from fastapi import APIRouter

from .interview import router as interview_router
from .portfolio import router as portfolio_router

router = APIRouter(prefix="/v1")
router.include_router(interview_router)
router.include_router(portfolio_router)

__all__ = ["router"]
