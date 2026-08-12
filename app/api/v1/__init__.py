"""API v1 패키지"""

from fastapi import APIRouter

from features.experience_map.config import get_settings

from .correction import router as correction_router
from .interview import router as interview_router
from .portfolio import router as portfolio_router

router = APIRouter(prefix="/v1")
router.include_router(correction_router)
router.include_router(interview_router)
router.include_router(portfolio_router)

# 경험정리는 시나리오 검증(3.23) 전까지 노출하지 않는다. flag 가 꺼져 있으면
# 라우트 자체를 등록하지 않아 404 가 된다.
if get_settings().enabled:
    from .experience_map import router as experience_map_router

    router.include_router(experience_map_router)

__all__ = ["router"]
