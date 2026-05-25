"""VisualizationMainClient 단위 테스트"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, call, patch

import pytest

from common.clients.base_client import MainServerError
from features.visualization.main_client import (
    VisualizationMainClient,
    _JOB_FIELD_MAP,
    _RETRY_MAX,
    _SLIDE_FIELD_MAP,
    _map_top_level,
)

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

_URL = "http://main-test"
_KEY = "test-api-key"


def make_client() -> VisualizationMainClient:
    return VisualizationMainClient(base_url=_URL, api_key=_KEY)


def server_err(status: int = 503) -> MainServerError:
    return MainServerError(status_code=status, detail=f"Error {status}")


# ---------------------------------------------------------------------------
# 필드 매핑 상수 검증
# ---------------------------------------------------------------------------


class TestFieldMaps:
    def test_job_field_map_keys(self):
        assert _JOB_FIELD_MAP["portfolioText"] == "portfolio_text"
        assert _JOB_FIELD_MAP["slidePlan"] == "slide_plan"
        assert _JOB_FIELD_MAP["gcsPptxKey"] == "gcs_pptx_key"
        assert _JOB_FIELD_MAP["pipelineStage"] == "pipeline_stage"
        assert _JOB_FIELD_MAP["totalSlides"] == "total_slides"

    def test_slide_field_map_keys(self):
        assert _SLIDE_FIELD_MAP["currentFills"] == "current_fills"
        assert _SLIDE_FIELD_MAP["sourceSlideId"] == "source_slide_id"
        assert _SLIDE_FIELD_MAP["gcsPreviewKey"] == "gcs_preview_key"
        assert _SLIDE_FIELD_MAP["slideOrder"] == "slide_order"

    def test_map_top_level_only(self):
        """JSONB blob 내부 키는 변환하지 않는다."""
        raw = {
            "slidePlan": {"selected_slides": [{"source_slide_id": "cover_B"}]},
            "totalSlides": 5,
        }
        result = _map_top_level(raw, _JOB_FIELD_MAP)
        assert "slide_plan" in result
        assert result["slide_plan"]["selected_slides"][0]["source_slide_id"] == "cover_B"
        assert result["total_slides"] == 5

    def test_map_preserves_current_fills_snake(self):
        """currentFills JSONB blob 내부 snake_case 키는 그대로다."""
        raw = {
            "currentFills": {"shape-1": {"font_size_override": 14, "is_title": True}},
        }
        result = _map_top_level(raw, _SLIDE_FIELD_MAP)
        assert "current_fills" in result
        fills = result["current_fills"]["shape-1"]
        assert fills["font_size_override"] == 14
        assert fills["is_title"] is True


# ---------------------------------------------------------------------------
# 재시도 동작
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_5xx_retries_up_to_max_then_raises(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = make_client()
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=[server_err(503)] * _RETRY_MAX,
        ) as mock_req:
            with pytest.raises(MainServerError) as exc_info:
                await client._request_with_retry("POST", "/test")
            assert mock_req.call_count == _RETRY_MAX
            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_5xx_succeeds_on_second_attempt(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = make_client()
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=[server_err(500), None],
        ) as mock_req:
            result = await client._request_with_retry("POST", "/test")
            assert result is None
            assert mock_req.call_count == 2

    @pytest.mark.asyncio
    async def test_504_timeout_is_retried(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = make_client()
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=[server_err(504)] * _RETRY_MAX,
        ) as mock_req:
            with pytest.raises(MainServerError) as exc_info:
                await client._request_with_retry("POST", "/test")
            assert mock_req.call_count == _RETRY_MAX
            assert exc_info.value.status_code == 504

    @pytest.mark.asyncio
    async def test_4xx_raises_immediately_no_retry(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = make_client()
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=server_err(404),
        ) as mock_req:
            with pytest.raises(MainServerError) as exc_info:
                await client._request_with_retry("GET", "/test")
            assert mock_req.call_count == 1
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_422_raises_immediately_no_retry(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = make_client()
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=server_err(422),
        ) as mock_req:
            with pytest.raises(MainServerError) as exc_info:
                await client._request_with_retry("POST", "/test")
            assert mock_req.call_count == 1
            assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_sleep_called_between_retries(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        client = make_client()
        with patch.object(
            client,
            "_request",
            new_callable=AsyncMock,
            side_effect=[server_err(503)] * _RETRY_MAX,
        ):
            with pytest.raises(MainServerError):
                await client._request_with_retry("POST", "/test")
        assert sleep_mock.call_count == _RETRY_MAX - 1


# ---------------------------------------------------------------------------
# submit_slide_plan
# ---------------------------------------------------------------------------


class TestSubmitSlidePlan:
    @pytest.mark.asyncio
    async def test_correct_path_and_body(self):
        client = make_client()
        slide_plan_blob = {
            "selected_slides": [
                {"source_slide_id": "cover_B", "content_brief": "표지"}
            ]
        }
        slides = [
            {"slide_order": 1, "source_slide_id": "cover_B", "slide_filename": "slide1.xml"},
            {"slide_order": 2, "source_slide_id": "skills_A", "slide_filename": "slide2.xml"},
        ]
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=None
        ) as mock_rwr:
            await client.submit_slide_plan(
                "job-abc",
                total_slides=2,
                template_id="blue",
                slide_plan=slide_plan_blob,
                slides=slides,
                idempotency_key="idem-1",
            )
        mock_rwr.assert_called_once_with(
            "POST",
            "/api/internal/visualizations/job-abc/slide-plan",
            json={
                "totalSlides": 2,
                "templateId": "blue",
                "slidePlan": slide_plan_blob,
                "slides": [
                    {"slideOrder": 1, "sourceSlideId": "cover_B", "slideFilename": "slide1.xml"},
                    {"slideOrder": 2, "sourceSlideId": "skills_A", "slideFilename": "slide2.xml"},
                ],
                "idempotencyKey": "idem-1",
                "schemaVersion": 1,
            },
        )

    @pytest.mark.asyncio
    async def test_slide_plan_blob_keys_not_converted(self):
        """slidePlan JSONB 내부 키 source_slide_id 는 camel 로 변환되지 않는다."""
        client = make_client()
        captured_body: dict[str, Any] = {}

        async def capture(method, path, *, json=None, params=None):
            captured_body.update(json or {})

        with patch.object(client, "_request_with_retry", side_effect=capture):
            await client.submit_slide_plan(
                "job-1",
                total_slides=1,
                template_id="blue",
                slide_plan={"selected_slides": [{"source_slide_id": "cover_B"}]},
                slides=[{"slide_order": 1, "source_slide_id": "cover_B", "slide_filename": "s.xml"}],
                idempotency_key="k",
            )

        blob = captured_body["slidePlan"]
        assert blob["selected_slides"][0]["source_slide_id"] == "cover_B"
        assert "sourceSlideId" not in blob["selected_slides"][0]


# ---------------------------------------------------------------------------
# send_slide_event
# ---------------------------------------------------------------------------


class TestSendSlideEvent:
    @pytest.mark.asyncio
    async def test_slide_content_ready_path_and_body(self):
        client = make_client()
        fills = {"shape-1": {"font_size_override": 18, "is_title": False}}
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=None
        ) as mock_rwr:
            await client.send_slide_event(
                "job-1",
                "slide-2",
                event="slide_content_ready",
                slide_order=2,
                idempotency_key="idem-2",
                occurred_at="2026-05-25T10:00:00Z",
                current_fills=fills,
            )
        mock_rwr.assert_called_once_with(
            "POST",
            "/api/internal/visualizations/job-1/slides/slide-2/events",
            json={
                "event": "slide_content_ready",
                "slideOrder": 2,
                "idempotencyKey": "idem-2",
                "occurredAt": "2026-05-25T10:00:00Z",
                "schemaVersion": 1,
                "currentFills": fills,
            },
        )

    @pytest.mark.asyncio
    async def test_slide_preview_ready_includes_gcs_key(self):
        client = make_client()
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=None
        ) as mock_rwr:
            await client.send_slide_event(
                "job-1",
                "slide-2",
                event="slide_preview_ready",
                slide_order=2,
                idempotency_key="idem-3",
                occurred_at="2026-05-25T10:01:00Z",
                gcs_preview_key="jobs/job-1/previews/slide-02.jpg",
            )
        body = mock_rwr.call_args.kwargs["json"]
        assert body["event"] == "slide_preview_ready"
        assert body["gcsPreviewKey"] == "jobs/job-1/previews/slide-02.jpg"
        assert "currentFills" not in body

    @pytest.mark.asyncio
    async def test_slide_error_includes_message_and_retryable(self):
        client = make_client()
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=None
        ) as mock_rwr:
            await client.send_slide_event(
                "job-1",
                "slide-3",
                event="slide_content_error",
                slide_order=3,
                idempotency_key="idem-4",
                occurred_at="2026-05-25T10:02:00Z",
                message="LLM 호출 실패",
                retryable=True,
            )
        body = mock_rwr.call_args.kwargs["json"]
        assert body["event"] == "slide_content_error"
        assert body["message"] == "LLM 호출 실패"
        assert body["retryable"] is True
        assert "currentFills" not in body

    @pytest.mark.asyncio
    async def test_current_fills_blob_keys_not_converted(self):
        """currentFills JSONB 내부 font_size_override / is_title 는 camel 변환 안 됨."""
        client = make_client()
        fills = {"shape-1": {"font_size_override": 14, "is_title": True}}
        captured: dict[str, Any] = {}

        async def capture(method, path, *, json=None, params=None):
            captured.update(json or {})

        with patch.object(client, "_request_with_retry", side_effect=capture):
            await client.send_slide_event(
                "job-1",
                "slide-1",
                event="slide_regenerated",
                slide_order=1,
                idempotency_key="k",
                occurred_at="2026-05-25T11:00:00Z",
                current_fills=fills,
            )

        blob = captured["currentFills"]
        assert blob["shape-1"]["font_size_override"] == 14
        assert blob["shape-1"]["is_title"] is True
        assert "fontSizeOverride" not in blob["shape-1"]


# ---------------------------------------------------------------------------
# send_job_event
# ---------------------------------------------------------------------------


class TestSendJobEvent:
    @pytest.mark.asyncio
    async def test_pipeline_stage_changed(self):
        client = make_client()
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=None
        ) as mock_rwr:
            await client.send_job_event(
                "job-1",
                event="pipeline_stage_changed",
                idempotency_key="idem-5",
                occurred_at="2026-05-25T10:03:00Z",
                pipeline_stage="rendering",
            )
        mock_rwr.assert_called_once_with(
            "POST",
            "/api/internal/visualizations/job-1/events",
            json={
                "event": "pipeline_stage_changed",
                "idempotencyKey": "idem-5",
                "occurredAt": "2026-05-25T10:03:00Z",
                "schemaVersion": 1,
                "pipelineStage": "rendering",
            },
        )

    @pytest.mark.asyncio
    async def test_all_completed_with_gcs_pptx_key_and_summary(self):
        client = make_client()
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=None
        ) as mock_rwr:
            await client.send_job_event(
                "job-1",
                event="all_completed",
                idempotency_key="idem-6",
                occurred_at="2026-05-25T10:10:00Z",
                gcs_pptx_key="jobs/job-1/current.pptx",
                summary={"completed": 7, "failed": 1},
            )
        body = mock_rwr.call_args.kwargs["json"]
        assert body["event"] == "all_completed"
        assert body["gcsPptxKey"] == "jobs/job-1/current.pptx"
        assert body["summary"] == {"completed": 7, "failed": 1}
        assert "pipelineStage" not in body

    @pytest.mark.asyncio
    async def test_optional_fields_omitted_when_none(self):
        client = make_client()
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=None
        ) as mock_rwr:
            await client.send_job_event(
                "job-1",
                event="pipeline_stage_changed",
                idempotency_key="k",
                occurred_at="2026-05-25T10:00:00Z",
            )
        body = mock_rwr.call_args.kwargs["json"]
        assert "pipelineStage" not in body
        assert "gcsPptxKey" not in body
        assert "summary" not in body
        assert "errorCode" not in body


# ---------------------------------------------------------------------------
# 컨텍스트 조회 — 필드 매핑 검증
# ---------------------------------------------------------------------------


class TestExtractResult:
    """_extract_result 의 isSuccess=false 경로 검증"""

    def test_isSuccess_false_with_string_error_raises(self):
        client = make_client()
        raw = {"isSuccess": False, "error": "job not found"}
        with pytest.raises(MainServerError) as exc_info:
            client._extract_result(raw)
        assert exc_info.value.status_code == 422
        assert "job not found" in exc_info.value.detail

    def test_isSuccess_false_with_structured_error_extracts_code(self):
        client = make_client()
        raw = {"isSuccess": False, "error": {"code": "TEMPLATE_NOT_FOUND", "message": "템플릿 없음"}}
        with pytest.raises(MainServerError) as exc_info:
            client._extract_result(raw)
        assert exc_info.value.detail == "템플릿 없음"
        assert exc_info.value.error_code == "TEMPLATE_NOT_FOUND"

    def test_isSuccess_false_with_reason_field(self):
        client = make_client()
        raw = {"isSuccess": False, "error": {"reason": "validation failed"}}
        with pytest.raises(MainServerError) as exc_info:
            client._extract_result(raw)
        assert "validation failed" in exc_info.value.detail

    def test_isSuccess_false_with_null_error(self):
        client = make_client()
        raw = {"isSuccess": False, "error": None}
        with pytest.raises(MainServerError) as exc_info:
            client._extract_result(raw)
        assert exc_info.value.detail == "isSuccess=false"

    def test_non_envelope_passthrough(self):
        client = make_client()
        assert client._extract_result(None) is None
        assert client._extract_result({"data": "raw"}) == {"data": "raw"}


class TestCallbackIsSuccessFalse:
    """POST 콜백이 isSuccess=false envelope 을 MainServerError 로 전파하는지 검증"""

    @pytest.mark.asyncio
    async def test_submit_slide_plan_raises_on_isSuccess_false(self):
        client = make_client()
        envelope = {"isSuccess": False, "error": "duplicate slide plan"}
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=envelope
        ):
            with pytest.raises(MainServerError) as exc_info:
                await client.submit_slide_plan(
                    "job-1",
                    total_slides=1,
                    template_id="blue",
                    slide_plan={},
                    slides=[{"slide_order": 1, "source_slide_id": "c", "slide_filename": "s.xml"}],
                    idempotency_key="k",
                )
            assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_send_slide_event_raises_on_isSuccess_false(self):
        client = make_client()
        envelope = {"isSuccess": False, "error": {"code": "JOB_NOT_FOUND", "message": "없음"}}
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=envelope
        ):
            with pytest.raises(MainServerError) as exc_info:
                await client.send_slide_event(
                    "job-1", "slide-1",
                    event="slide_content_ready",
                    slide_order=1,
                    idempotency_key="k",
                    occurred_at="2026-05-25T10:00:00Z",
                )
            assert exc_info.value.error_code == "JOB_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_send_job_event_raises_on_isSuccess_false(self):
        client = make_client()
        envelope = {"isSuccess": False, "error": "state conflict"}
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=envelope
        ):
            with pytest.raises(MainServerError):
                await client.send_job_event(
                    "job-1",
                    event="all_completed",
                    idempotency_key="k",
                    occurred_at="2026-05-25T10:00:00Z",
                )

    @pytest.mark.asyncio
    async def test_submit_slide_plan_ok_on_204(self):
        """204 No Content (None) 은 정상 처리된다."""
        client = make_client()
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=None
        ):
            await client.submit_slide_plan(
                "job-1",
                total_slides=1,
                template_id="blue",
                slide_plan={},
                slides=[{"slide_order": 1, "source_slide_id": "c", "slide_filename": "s.xml"}],
                idempotency_key="k",
            )


class TestGetJobContext:
    @pytest.mark.asyncio
    async def test_response_mapped_to_snake_case(self):
        client = make_client()
        envelope = {
            "isSuccess": True,
            "result": {
                "id": "job-1",
                "portfolioId": "port-1",
                "portfolioText": "텍스트 내용",
                "userId": "user-1",
                "templateId": "blue",
                "status": "generating",
                "pipelineStage": "contentGenerating",
                "totalSlides": 8,
                "regenerationCount": 2,
                "gcsPptxKey": None,
                "slidePlan": {"selected_slides": [{"source_slide_id": "cover_B"}]},
                "createdAt": "2026-05-25T09:00:00Z",
                "updatedAt": "2026-05-25T10:00:00Z",
            },
        }
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=envelope
        ):
            result = await client.get_job_context("job-1")

        assert result["portfolio_id"] == "port-1"
        assert result["portfolio_text"] == "텍스트 내용"
        assert result["pipeline_stage"] == "contentGenerating"
        assert result["total_slides"] == 8
        assert result["regeneration_count"] == 2
        assert result["gcs_pptx_key"] is None
        assert "portfolioId" not in result
        assert "pipelineStage" not in result

    @pytest.mark.asyncio
    async def test_slide_plan_internal_keys_preserved(self):
        """slidePlan 내부 source_slide_id / content_brief 는 snake_case 그대로."""
        client = make_client()
        slide_plan = {
            "selected_slides": [
                {"source_slide_id": "cover_B", "content_brief": "표지 내용"}
            ]
        }
        envelope = {"isSuccess": True, "result": {"slidePlan": slide_plan}}
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=envelope
        ):
            result = await client.get_job_context("job-1")

        blob = result["slide_plan"]
        assert blob["selected_slides"][0]["source_slide_id"] == "cover_B"
        assert blob["selected_slides"][0]["content_brief"] == "표지 내용"

    @pytest.mark.asyncio
    async def test_calls_correct_path(self):
        client = make_client()
        with patch.object(
            client,
            "_request_with_retry",
            new_callable=AsyncMock,
            return_value={"isSuccess": True, "result": {}},
        ) as mock_rwr:
            await client.get_job_context("job-xyz")
        mock_rwr.assert_called_once_with("GET", "/api/internal/visualizations/job-xyz")

    @pytest.mark.asyncio
    async def test_non_dict_result_raises(self):
        """result 가 dict 가 아니면 MainServerError(502) 를 던진다."""
        client = make_client()
        for bad_result in (None, [], "string"):
            envelope = {"isSuccess": True, "result": bad_result}
            with patch.object(
                client, "_request_with_retry", new_callable=AsyncMock, return_value=envelope
            ):
                with pytest.raises(MainServerError) as exc_info:
                    await client.get_job_context("job-1")
                assert exc_info.value.status_code == 502


class TestGetSlideContext:
    @pytest.mark.asyncio
    async def test_response_mapped_to_snake_case(self):
        client = make_client()
        fills = {"shape-1": {"font_size_override": 16, "is_title": False}}
        envelope = {
            "isSuccess": True,
            "result": {
                "id": "slide-1",
                "jobId": "job-1",
                "slideOrder": 3,
                "sourceSlideId": "skills_A",
                "slideFilename": "slide3.xml",
                "status": "completed",
                "currentFills": fills,
                "gcsPreviewKey": "jobs/job-1/previews/slide-03.jpg",
                "createdAt": "2026-05-25T09:00:00Z",
                "updatedAt": "2026-05-25T10:00:00Z",
            },
        }
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=envelope
        ):
            result = await client.get_slide_context("job-1", "slide-1")

        assert result["job_id"] == "job-1"
        assert result["slide_order"] == 3
        assert result["source_slide_id"] == "skills_A"
        assert result["slide_filename"] == "slide3.xml"
        assert result["gcs_preview_key"] == "jobs/job-1/previews/slide-03.jpg"
        assert "jobId" not in result
        assert "sourceSlideId" not in result

    @pytest.mark.asyncio
    async def test_current_fills_internal_keys_preserved(self):
        """currentFills 내부 font_size_override / is_title 는 snake_case 그대로."""
        client = make_client()
        fills = {"shape-1": {"font_size_override": 14, "is_title": True}}
        envelope = {"isSuccess": True, "result": {"currentFills": fills}}
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=envelope
        ):
            result = await client.get_slide_context("job-1", "slide-1")

        blob = result["current_fills"]
        assert blob["shape-1"]["font_size_override"] == 14
        assert blob["shape-1"]["is_title"] is True
        assert "fontSizeOverride" not in blob["shape-1"]

    @pytest.mark.asyncio
    async def test_calls_correct_path(self):
        client = make_client()
        with patch.object(
            client,
            "_request_with_retry",
            new_callable=AsyncMock,
            return_value={"isSuccess": True, "result": {}},
        ) as mock_rwr:
            await client.get_slide_context("job-xyz", "slide-abc")
        mock_rwr.assert_called_once_with(
            "GET", "/api/internal/visualizations/job-xyz/slides/slide-abc"
        )

    @pytest.mark.asyncio
    async def test_non_dict_result_raises(self):
        """result 가 dict 가 아니면 MainServerError(502) 를 던진다."""
        client = make_client()
        envelope = {"isSuccess": True, "result": None}
        with patch.object(
            client, "_request_with_retry", new_callable=AsyncMock, return_value=envelope
        ):
            with pytest.raises(MainServerError) as exc_info:
                await client.get_slide_context("job-1", "slide-1")
            assert exc_info.value.status_code == 502
