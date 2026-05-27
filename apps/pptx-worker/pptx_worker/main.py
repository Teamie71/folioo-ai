"""PPTX 시각화 워커 FastAPI 애플리케이션."""

from typing import Any

from fastapi import FastAPI

from pptx_worker.api import router as api_router
from pptx_worker.runtime import get_worker_runtime

APP_VERSION = "0.1.0"


async def get_health() -> dict[str, Any]:
    """워커 헬스체크 응답 생성."""
    runtime = get_worker_runtime()
    health = await runtime.snapshot()
    health["version"] = APP_VERSION
    return health


def create_app() -> FastAPI:
    """PPTX 워커 FastAPI 애플리케이션 생성."""
    app = FastAPI(
        title="Folioo PPTX Worker",
        description="Cloud Tasks push 기반 PPTX 시각화 워커",
        version=APP_VERSION,
    )

    @app.get(
        "/health",
        response_model=dict[str, Any],
        status_code=200,
        summary="워커 헬스체크",
    )
    async def health() -> dict[str, Any]:
        return await get_health()

    app.include_router(api_router)
    return app


app = create_app()


__all__ = ["app", "create_app", "get_health"]
