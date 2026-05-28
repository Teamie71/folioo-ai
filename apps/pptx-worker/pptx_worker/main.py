"""PPTX 시각화 워커 FastAPI 애플리케이션."""

from typing import Any

from fastapi import FastAPI
from starlette.responses import PlainTextResponse

from pptx_worker.api import router as api_router
from pptx_worker.metrics import PROMETHEUS_CONTENT_TYPE, get_worker_metrics
from pptx_worker.runtime import get_worker_runtime

APP_VERSION = "0.1.0"


async def get_health() -> dict[str, Any]:
    """워커 헬스체크 응답 생성."""
    runtime = get_worker_runtime()
    health = await runtime.snapshot()
    health["version"] = APP_VERSION
    return health


async def get_metrics() -> str:
    """Prometheus text exposition 메트릭을 생성한다."""
    runtime = get_worker_runtime()
    snapshot = await runtime.snapshot()
    return get_worker_metrics().render_prometheus(
        worker_jobs_processed_total=int(snapshot["lifetime_processed"]),
        worker_ready_for_recycle=bool(snapshot["ready_for_recycle"]),
        worker_concurrent_active=int(snapshot["concurrent_active"]),
    )


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

    @app.get(
        "/metrics",
        response_class=PlainTextResponse,
        status_code=200,
        summary="워커 Prometheus 메트릭",
    )
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(
            await get_metrics(),
            media_type=PROMETHEUS_CONTENT_TYPE,
        )

    app.include_router(api_router)
    return app


app = create_app()


__all__ = ["app", "create_app", "get_health", "get_metrics"]
