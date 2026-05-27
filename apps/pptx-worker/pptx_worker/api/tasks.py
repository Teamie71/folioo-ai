"""Cloud Tasks HTTP Push 핸들러."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from fastapi import APIRouter, BackgroundTasks, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import JSONResponse

from common.clients.base_client import MainServerError
from features.visualization.main_client import VisualizationMainClient
from features.visualization.service import (
    FatalError,
    GenerateVisualizationTask,
    RegenerateVisualizationTask,
    RetryableError,
    VisualizationTaskService,
    get_visualization_task_service,
)
from pptx_worker.runtime import WorkerRuntime, get_worker_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks/visualizations", tags=["cloud-tasks"])


class MainClient(Protocol):
    """핸들러가 사용하는 메인 백엔드 클라이언트 프로토콜."""

    async def get_job_context(self, job_id: str) -> dict[str, Any]:
        """Job 컨텍스트를 조회한다."""
        ...

    async def get_slide_context(self, job_id: str, slide_id: str) -> dict[str, Any]:
        """슬라이드 컨텍스트를 조회한다."""
        ...

    async def send_job_event(
        self,
        job_id: str,
        *,
        event: str,
        idempotency_key: str,
        occurred_at: str,
        schema_version: int = 1,
        pipeline_stage: str | None = None,
        gcs_pptx_key: str | None = None,
        summary: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        """Job 레벨 이벤트를 콜백한다."""
        ...

    async def send_slide_event(
        self,
        job_id: str,
        slide_id: str,
        *,
        event: str,
        slide_order: int,
        idempotency_key: str,
        occurred_at: str,
        schema_version: int = 1,
        current_fills: dict[str, Any] | None = None,
        gcs_preview_key: str | None = None,
        message: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        """슬라이드 레벨 이벤트를 콜백한다."""
        ...

    async def close(self) -> None:
        """클라이언트 리소스를 정리한다."""
        ...


MainClientFactory = Callable[[str], MainClient]


class _BasePushPayload(BaseModel):
    """Cloud Tasks push 공통 payload."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    job_id: str = Field(alias="jobId", min_length=1)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1)
    callback_base_url: str = Field(alias="callbackBaseUrl", min_length=1)
    schema_version: int = Field(alias="schemaVersion", ge=1)


class GeneratePushPayload(_BasePushPayload):
    """초기 생성 push payload."""

    message_type: Literal["viz.generate"] = Field(alias="messageType")
    portfolio_id: str = Field(alias="portfolioId", min_length=1)
    user_id: str = Field(alias="userId", min_length=1)
    template_id: str = Field(alias="templateId", min_length=1)

    def to_task(self) -> GenerateVisualizationTask:
        """서비스 위임용 dataclass 로 변환한다."""
        return GenerateVisualizationTask(
            message_type=self.message_type,
            job_id=self.job_id,
            portfolio_id=self.portfolio_id,
            user_id=self.user_id,
            template_id=self.template_id,
            idempotency_key=self.idempotency_key,
            callback_base_url=self.callback_base_url,
            schema_version=self.schema_version,
        )


class RegeneratePushPayload(_BasePushPayload):
    """재생성 또는 retry push payload."""

    message_type: Literal["viz.regenerate"] = Field(alias="messageType")
    slide_id: str = Field(alias="slideId", min_length=1)
    user_request: str | None = Field(default=None, alias="userRequest")
    is_retry: bool = Field(default=False, alias="isRetry")

    @model_validator(mode="after")
    def validate_user_request(self) -> "RegeneratePushPayload":
        """일반 재생성은 userRequest 가 필요하고 retry 는 생략할 수 있다."""
        if not self.is_retry and not self.user_request:
            raise ValueError("isRetry=false인 regenerate payload에는 userRequest가 필요합니다.")
        return self

    def to_task(self) -> RegenerateVisualizationTask:
        """서비스 위임용 dataclass 로 변환한다."""
        return RegenerateVisualizationTask(
            message_type=self.message_type,
            job_id=self.job_id,
            slide_id=self.slide_id,
            user_request=None if self.is_retry else self.user_request,
            is_retry=self.is_retry,
            idempotency_key=self.idempotency_key,
            callback_base_url=self.callback_base_url,
            schema_version=self.schema_version,
        )


def _default_main_client_factory(callback_base_url: str) -> VisualizationMainClient:
    return VisualizationMainClient(base_url=callback_base_url)


_main_client_factory: MainClientFactory = _default_main_client_factory


def set_main_client_factory(factory: MainClientFactory) -> None:
    """테스트용 메인 클라이언트 팩토리 교체."""
    global _main_client_factory

    _main_client_factory = factory


def reset_main_client_factory() -> None:
    """메인 클라이언트 팩토리를 기본값으로 되돌린다."""
    set_main_client_factory(_default_main_client_factory)


def _get_task_service() -> VisualizationTaskService:
    return get_visualization_task_service()


def _get_runtime() -> WorkerRuntime:
    return get_worker_runtime()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_response(
    payload: dict[str, Any],
    *,
    status_code: int = status.HTTP_200_OK,
    background_tasks: BackgroundTasks | None = None,
) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, background=background_tasks)


async def _close_client(client: MainClient) -> None:
    close = getattr(client, "close", None)
    if close is not None:
        await close()


async def _handle_main_context_error(
    exc: MainServerError,
    *,
    message_type: str,
    job_id: str,
    idempotency_key: str,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    if exc.status_code >= 500:
        logger.warning(
            "메인 상태 조회 재시도 가능 실패: message_type=%s job_id=%s status=%s "
            "idempotency_key=%s",
            message_type,
            job_id,
            exc.status_code,
            idempotency_key,
        )
        return _json_response(
            {"status": "retryable_failure", "detail": exc.detail},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            background_tasks=background_tasks,
        )

    logger.warning(
        "메인 상태 조회 치명 실패: message_type=%s job_id=%s status=%s idempotency_key=%s",
        message_type,
        job_id,
        exc.status_code,
        idempotency_key,
    )
    return _json_response(
        {"status": "fatal_failure", "detail": exc.detail},
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        background_tasks=background_tasks,
    )


async def _schedule_recycle_if_needed(
    runtime: WorkerRuntime,
    background_tasks: BackgroundTasks,
) -> None:
    snapshot = await runtime.snapshot()
    if snapshot["ready_for_recycle"]:
        background_tasks.add_task(runtime.shutdown_if_ready)


async def _send_generate_fatal_callback(
    client: MainClient,
    payload: GeneratePushPayload,
    exc: FatalError,
) -> None:
    await client.send_job_event(
        payload.job_id,
        event="all_completed",
        idempotency_key=payload.idempotency_key,
        occurred_at=_utc_now(),
        schema_version=payload.schema_version,
        summary={"completed": 0, "failed": 1},
        error_code=exc.error_code,
    )


def _slide_order_from_context(slide_context: dict[str, Any]) -> int:
    slide_order = slide_context.get("slide_order")
    if isinstance(slide_order, int):
        return slide_order
    return 0


async def _send_regenerate_fatal_callback(
    client: MainClient,
    payload: RegeneratePushPayload,
    slide_context: dict[str, Any],
    exc: FatalError,
) -> None:
    await client.send_slide_event(
        payload.job_id,
        payload.slide_id,
        event="slide_preview_error",
        slide_order=_slide_order_from_context(slide_context),
        idempotency_key=payload.idempotency_key,
        occurred_at=_utc_now(),
        schema_version=payload.schema_version,
        message=str(exc),
        retryable=False,
    )


@router.post("/generate", status_code=status.HTTP_200_OK)
async def handle_generate_task(
    payload: GeneratePushPayload,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """초기 PPTX 생성 Cloud Tasks push 요청을 처리한다."""
    runtime = _get_runtime()
    service = _get_task_service()
    client = _main_client_factory(payload.callback_base_url)

    async with runtime.track_active():
        try:
            try:
                job_context = await client.get_job_context(payload.job_id)
            except MainServerError as exc:
                return await _handle_main_context_error(
                    exc,
                    message_type=payload.message_type,
                    job_id=payload.job_id,
                    idempotency_key=payload.idempotency_key,
                    background_tasks=background_tasks,
                )

            job_status = job_context.get("status")
            if job_status != "generating":
                logger.info(
                    "generate 작업 skip: job_id=%s status=%s idempotency_key=%s",
                    payload.job_id,
                    job_status,
                    payload.idempotency_key,
                )
                return _json_response(
                    {"status": "skipped", "jobId": payload.job_id},
                    background_tasks=background_tasks,
                )

            try:
                await service.generate(payload.to_task())
            except RetryableError as exc:
                logger.warning(
                    "generate 작업 재시도 가능 실패: job_id=%s idempotency_key=%s detail=%s",
                    payload.job_id,
                    payload.idempotency_key,
                    exc,
                )
                return _json_response(
                    {"status": "retryable_failure", "detail": str(exc)},
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    background_tasks=background_tasks,
                )
            except FatalError as exc:
                logger.warning(
                    "generate 작업 치명 실패: job_id=%s idempotency_key=%s detail=%s",
                    payload.job_id,
                    payload.idempotency_key,
                    exc,
                )
                await _send_generate_fatal_callback(client, payload, exc)
                await runtime.mark_processed()
                await _schedule_recycle_if_needed(runtime, background_tasks)
                return _json_response(
                    {"status": "fatal_acked", "jobId": payload.job_id},
                    background_tasks=background_tasks,
                )

            await runtime.mark_processed()
            await _schedule_recycle_if_needed(runtime, background_tasks)
            return _json_response(
                {"status": "ok", "jobId": payload.job_id},
                background_tasks=background_tasks,
            )
        finally:
            await _close_client(client)


@router.post("/regenerate", status_code=status.HTTP_200_OK)
async def handle_regenerate_task(
    payload: RegeneratePushPayload,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """단일 슬라이드 재생성 Cloud Tasks push 요청을 처리한다."""
    runtime = _get_runtime()
    service = _get_task_service()
    client = _main_client_factory(payload.callback_base_url)

    async with runtime.track_active():
        try:
            try:
                slide_context = await client.get_slide_context(payload.job_id, payload.slide_id)
            except MainServerError as exc:
                return await _handle_main_context_error(
                    exc,
                    message_type=payload.message_type,
                    job_id=payload.job_id,
                    idempotency_key=payload.idempotency_key,
                    background_tasks=background_tasks,
                )

            slide_status = slide_context.get("status")
            if slide_status not in ("regenerating", "generating"):
                logger.info(
                    "regenerate 작업 skip: job_id=%s slide_id=%s status=%s idempotency_key=%s",
                    payload.job_id,
                    payload.slide_id,
                    slide_status,
                    payload.idempotency_key,
                )
                return _json_response(
                    {"status": "skipped", "jobId": payload.job_id, "slideId": payload.slide_id},
                    background_tasks=background_tasks,
                )

            try:
                await service.regenerate(payload.to_task())
            except RetryableError as exc:
                logger.warning(
                    "regenerate 작업 재시도 가능 실패: job_id=%s slide_id=%s "
                    "idempotency_key=%s detail=%s",
                    payload.job_id,
                    payload.slide_id,
                    payload.idempotency_key,
                    exc,
                )
                return _json_response(
                    {"status": "retryable_failure", "detail": str(exc)},
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    background_tasks=background_tasks,
                )
            except FatalError as exc:
                logger.warning(
                    "regenerate 작업 치명 실패: job_id=%s slide_id=%s idempotency_key=%s detail=%s",
                    payload.job_id,
                    payload.slide_id,
                    payload.idempotency_key,
                    exc,
                )
                await _send_regenerate_fatal_callback(client, payload, slide_context, exc)
                await runtime.mark_processed()
                await _schedule_recycle_if_needed(runtime, background_tasks)
                return _json_response(
                    {"status": "fatal_acked", "jobId": payload.job_id, "slideId": payload.slide_id},
                    background_tasks=background_tasks,
                )

            await runtime.mark_processed()
            await _schedule_recycle_if_needed(runtime, background_tasks)
            return _json_response(
                {"status": "ok", "jobId": payload.job_id, "slideId": payload.slide_id},
                background_tasks=background_tasks,
            )
        finally:
            await _close_client(client)


__all__ = [
    "GeneratePushPayload",
    "RegeneratePushPayload",
    "reset_main_client_factory",
    "router",
    "set_main_client_factory",
]
