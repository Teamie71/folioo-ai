"""PPTX 워커 API 라우터를 재노출하는 모듈."""

from fastapi import APIRouter

from .tasks import router as tasks_router

router = APIRouter()
router.include_router(tasks_router)

__all__ = ["router"]
