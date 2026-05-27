"""PPTX 워커 API wrapper."""

from fastapi import APIRouter

from .tasks import router as tasks_router

router = APIRouter()
router.include_router(tasks_router)

__all__ = ["router"]
