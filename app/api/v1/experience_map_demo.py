"""Swagger에서 확인하는 경험 맵 로컬 데모 API."""

from fastapi import APIRouter

from features.experience_map.demo import run_demo

router = APIRouter(prefix="/experience-map/demo", tags=["experience-map-demo"])


@router.post("/run", summary="메인 서버 없는 경험 맵 데모 실행")
async def run_experience_map_demo() -> dict:
    """결정적 데모 SSE event와 in-memory 맵 변경을 Swagger 응답으로 반환한다."""
    events, demo_map = await run_demo()
    return {
        "events": events,
        "map": demo_map,
        "notice": "데모 결과는 메모리에만 존재하며 실제 DB·메인 서버에는 반영되지 않습니다.",
    }


__all__ = ["router"]
