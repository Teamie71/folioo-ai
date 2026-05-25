"""시각화 워커 → 메인 백엔드 콜백/컨텍스트 클라이언트 (§11.3)"""

import asyncio
import logging
import random
from typing import Any

from common.clients.base_client import BaseClient, MainServerError

logger = logging.getLogger(__name__)

_RETRY_MAX = 5
_BACKOFF_BASE = 1.0  # seconds
_JITTER_MAX = 0.5  # seconds

# camelCase(API 응답) → snake_case(워커 내부) — 최상위 키만 매핑.
# slidePlan / currentFills JSONB 내부 키는 그대로 통과시킨다 (§11.0.3).
_JOB_FIELD_MAP: dict[str, str] = {
    "portfolioId": "portfolio_id",
    "portfolioText": "portfolio_text",
    "userId": "user_id",
    "templateId": "template_id",
    "pipelineStage": "pipeline_stage",
    "totalSlides": "total_slides",
    "regenerationCount": "regeneration_count",
    "gcsPptxKey": "gcs_pptx_key",
    "slidePlan": "slide_plan",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}

_SLIDE_FIELD_MAP: dict[str, str] = {
    "jobId": "job_id",
    "slideOrder": "slide_order",
    "sourceSlideId": "source_slide_id",
    "slideFilename": "slide_filename",
    "currentFills": "current_fills",
    "gcsPreviewKey": "gcs_preview_key",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}

_BASE = "/api/internal/visualizations"


def _map_top_level(data: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
    """최상위 키만 field_map 으로 변환. 값은 그대로 (JSONB 내부 불변)."""
    return {field_map.get(k, k): v for k, v in data.items()}


class VisualizationMainClient(BaseClient):
    """
    시각화 워커 → 메인 백엔드 전용 HTTP 클라이언트.

    - 콜백: slide-plan 제출, slide 레벨, job 레벨 이벤트
    - 컨텍스트 조회: job / slide
    - 5xx/timeout(504) → 선형 백오프+jitter 최대 5회 재시도, 4xx → 즉시 실패
    - slidePlan / currentFills JSONB blob 내부 키는 snake_case 그대로 유지 (§11.0.3)
    """

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
    ) -> Any:
        last_exc: MainServerError | None = None
        for attempt in range(1, _RETRY_MAX + 1):
            try:
                return await self._request(method, path, json=json, params=params)
            except MainServerError as exc:
                if exc.status_code >= 500:
                    last_exc = exc
                    if attempt < _RETRY_MAX:
                        wait = _BACKOFF_BASE * attempt + random.uniform(0, _JITTER_MAX)
                        logger.warning(
                            "5xx/timeout retry %d/%d %.2fs: [%s %s] status=%d",
                            attempt,
                            _RETRY_MAX,
                            wait,
                            method,
                            path,
                            exc.status_code,
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise
                raise  # 4xx: 즉시 실패 (재시도 없음)
        raise last_exc  # type: ignore[misc]

    def _extract_result(self, raw: Any) -> Any:
        """NestJS envelope { isSuccess, result } 에서 result 추출."""
        if not isinstance(raw, dict) or "isSuccess" not in raw:
            return raw
        if not raw.get("isSuccess"):
            error = raw.get("error")
            if isinstance(error, dict):
                detail = error.get("message") or error.get("reason") or str(error)
                error_code = error.get("code") or error.get("errorCode")
            else:
                detail = str(error) if error is not None else "isSuccess=false"
                error_code = None
            raise MainServerError(status_code=422, detail=detail, error_code=error_code)
        return raw.get("result")

    # ── 콜백 메서드 ───────────────────────────────────────────────────────────

    async def submit_slide_plan(
        self,
        job_id: str,
        *,
        total_slides: int,
        template_id: str,
        slide_plan: dict[str, Any],
        slides: list[dict[str, Any]],
        idempotency_key: str,
        schema_version: int = 1,
    ) -> None:
        """Step 1 직후 슬라이드 구성 계획 제출 (POST /{job_id}/slide-plan).

        slides 각 항목: { slide_order, source_slide_id, slide_filename }
        slide_plan 내부 키는 snake_case 그대로 전송 (§11.0.3).
        """
        body: dict[str, Any] = {
            "totalSlides": total_slides,
            "templateId": template_id,
            "slidePlan": slide_plan,
            "slides": [
                {
                    "slideOrder": s["slide_order"],
                    "sourceSlideId": s["source_slide_id"],
                    "slideFilename": s["slide_filename"],
                }
                for s in slides
            ],
            "idempotencyKey": idempotency_key,
            "schemaVersion": schema_version,
        }
        raw = await self._request_with_retry("POST", f"{_BASE}/{job_id}/slide-plan", json=body)
        self._extract_result(raw)

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
        """슬라이드 레벨 이벤트 콜백 (POST /{job_id}/slides/{slide_id}/events).

        event: slide_content_ready | slide_content_error |
               slide_preview_ready | slide_preview_error | slide_regenerated
        current_fills 내부 키는 snake_case 그대로 전송 (§11.0.3).
        """
        body: dict[str, Any] = {
            "event": event,
            "slideOrder": slide_order,
            "idempotencyKey": idempotency_key,
            "occurredAt": occurred_at,
            "schemaVersion": schema_version,
        }
        if current_fills is not None:
            body["currentFills"] = current_fills
        if gcs_preview_key is not None:
            body["gcsPreviewKey"] = gcs_preview_key
        if message is not None:
            body["message"] = message
        if retryable is not None:
            body["retryable"] = retryable

        raw = await self._request_with_retry(
            "POST", f"{_BASE}/{job_id}/slides/{slide_id}/events", json=body
        )
        self._extract_result(raw)

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
        """Job 레벨 이벤트 콜백 (POST /{job_id}/events).

        event: pipeline_stage_changed | all_completed
        """
        body: dict[str, Any] = {
            "event": event,
            "idempotencyKey": idempotency_key,
            "occurredAt": occurred_at,
            "schemaVersion": schema_version,
        }
        if pipeline_stage is not None:
            body["pipelineStage"] = pipeline_stage
        if gcs_pptx_key is not None:
            body["gcsPptxKey"] = gcs_pptx_key
        if summary is not None:
            body["summary"] = summary
        if error_code is not None:
            body["errorCode"] = error_code

        raw = await self._request_with_retry("POST", f"{_BASE}/{job_id}/events", json=body)
        self._extract_result(raw)

    # ── 컨텍스트 조회 메서드 ──────────────────────────────────────────────────

    async def get_job_context(self, job_id: str) -> dict[str, Any]:
        """Job 컨텍스트 조회 (GET /{job_id}).

        반환: portfolioText, slidePlan 등을 포함한 snake_case 워커 내부 구조.
        slidePlan 내부 키는 snake_case 그대로 (§11.0.3).
        """
        raw = await self._request_with_retry("GET", f"{_BASE}/{job_id}")
        result = self._extract_result(raw)
        if not isinstance(result, dict):
            raise MainServerError(
                status_code=502,
                detail=f"job context 응답 형식 오류: dict 예상, {type(result).__name__} 수신",
            )
        return _map_top_level(result, _JOB_FIELD_MAP)

    async def get_slide_context(self, job_id: str, slide_id: str) -> dict[str, Any]:
        """슬라이드 컨텍스트 조회 (GET /{job_id}/slides/{slide_id}).

        반환: currentFills, sourceSlideId 등을 포함한 snake_case 워커 내부 구조.
        currentFills 내부 키는 snake_case 그대로 (§11.0.3).
        """
        raw = await self._request_with_retry("GET", f"{_BASE}/{job_id}/slides/{slide_id}")
        result = self._extract_result(raw)
        if not isinstance(result, dict):
            raise MainServerError(
                status_code=502,
                detail=f"slide context 응답 형식 오류: dict 예상, {type(result).__name__} 수신",
            )
        return _map_top_level(result, _SLIDE_FIELD_MAP)
