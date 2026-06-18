"""Phase 1 초기 생성 파이프라인 오케스트레이션 테스트."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from common.clients.base_client import MainServerError
from features.visualization.agents import PlannedSlide, SlidePlan
from features.visualization.service import (
    GenerateVisualizationTask,
    RegenerateVisualizationTask,
    RetryableError,
    VisualizationTaskService,
)
from features.visualization.text_fit import EMU_PER_PT


@dataclass(frozen=True)
class FakeValidation:
    """PPTX validate/repair 결과 대역."""

    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class FakeRenderedSlide:
    """렌더된 슬라이드 대역."""

    page: int
    image_path: Path


@dataclass(frozen=True)
class FakeRenderResult:
    """렌더 결과 대역."""

    pdf_path: Path
    slides: tuple[FakeRenderedSlide, ...]


@dataclass(frozen=True)
class FakeQAOutcome:
    """QA 단계 슬라이드별 결과 대역."""

    slide_id: str
    slide_order: int
    status: str
    gcs_preview_key: str | None = None
    current_fills: dict[str, Any] | None = None


@dataclass(frozen=True)
class FakeQAResult:
    """QA 단계 전체 결과 대역."""

    outcomes: tuple[FakeQAOutcome, ...]
    pack_count: int = 0


class FakeMainClient:
    """메인 콜백 클라이언트 대역."""

    def __init__(
        self,
        *,
        return_slide_rows: bool = True,
        fail_slide_events: set[str] | None = None,
    ) -> None:
        self.return_slide_rows = return_slide_rows
        self.fail_slide_events = fail_slide_events or set()
        self.job_context = {
            "status": "pending",
            "portfolio_text": "Folioo KPI 42% 개선",
            "slide_plan": {
                "selected_slides": [
                    {
                        "order": index,
                        "source_slide_id": f"source_{index}",
                        "slide_filename": f"slide{index}.xml",
                        "content_brief": f"brief-{index}",
                    }
                    for index in range(1, 8)
                ]
            },
        }
        self.slide_context = {
            "id": "slide-3",
            "status": "regenerating",
            "slide_order": 3,
            "source_slide_id": "source_3",
            "slide_filename": "slide3.xml",
            "current_fills": {
                "2": {"action": "text", "text": "기존 제목", "font_size_override": 18},
                "3": {"action": "text", "text": "본문 유지", "font_size_override": 14},
            },
        }
        self.slide_plan_requests: list[dict[str, Any]] = []
        self.slide_events: list[dict[str, Any]] = []
        self.job_events: list[dict[str, Any]] = []
        self.closed = False

    async def get_job_context(self, job_id: str) -> dict[str, Any]:
        assert job_id == "job-1"
        return dict(self.job_context)

    async def get_slide_context(self, job_id: str, slide_id: str) -> dict[str, Any]:
        assert job_id == "job-1"
        assert slide_id == "slide-3"
        return dict(self.slide_context)

    async def submit_slide_plan(self, job_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.slide_plan_requests.append({"job_id": job_id, **kwargs})
        if not self.return_slide_rows:
            return []
        return [
            {
                "id": f"slide-{slide['slide_order']}",
                "slide_order": slide["slide_order"],
                "source_slide_id": slide["source_slide_id"],
                "slide_filename": slide["slide_filename"],
            }
            for slide in kwargs["slides"]
        ]

    async def send_slide_event(self, job_id: str, slide_id: str, **kwargs: Any) -> None:
        if kwargs["event"] in self.fail_slide_events:
            raise MainServerError(status_code=503, detail="callback failed")
        self.slide_events.append({"job_id": job_id, "slide_id": slide_id, **kwargs})

    async def send_job_event(self, job_id: str, **kwargs: Any) -> None:
        self.job_events.append({"job_id": job_id, **kwargs})

    async def close(self) -> None:
        self.closed = True


class FakeStorage:
    """GCS 클라이언트 대역."""

    def __init__(self, template_metadata: dict[str, Any] | None = None) -> None:
        self.template_metadata = template_metadata or {
            "schema_version": 2,
            "template_id": "blue",
            "runtime_slides": [],
        }
        self.download_error: Exception | None = None
        self.attempt_pptx_error: Exception | None = None
        self.downloaded_pptx: list[tuple[str, Path]] = []
        self.uploaded_pptx: list[tuple[str, Path]] = []
        self.uploaded_pdf: list[tuple[str, Path]] = []
        self.uploaded_previews: list[tuple[str, int, Path]] = []
        self.uploaded_attempt_pptx: list[tuple[str, str, Path]] = []
        self.uploaded_attempt_pdf: list[tuple[str, str, Path]] = []
        self.uploaded_attempt_previews: list[tuple[str, str, int, Path]] = []
        self.promoted_attempts: list[tuple[str, str, int]] = []

    def download_template(self, template_id: str, dest: Path) -> None:
        assert template_id == "blue"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"pptx")

    def download_template_meta(self, template_id: str, dest: Path) -> None:
        assert template_id == "blue"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(self.template_metadata), encoding="utf-8")

    def download_pptx(self, job_id: str, dest: Path) -> None:
        if self.download_error is not None:
            raise self.download_error
        self.downloaded_pptx.append((job_id, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"current")

    def upload_pptx(self, job_id: str, src: Path) -> str:
        self.uploaded_pptx.append((job_id, src))
        return f"jobs/{job_id}/current.pptx"

    def upload_regeneration_attempt_pptx(
        self,
        job_id: str,
        attempt_id: str,
        src: Path,
    ) -> str:
        if self.attempt_pptx_error is not None:
            raise self.attempt_pptx_error
        self.uploaded_attempt_pptx.append((job_id, attempt_id, src))
        return f"jobs/{job_id}/attempts/{attempt_id}/current.pptx"

    def upload_pdf(self, job_id: str, src: Path) -> str:
        self.uploaded_pdf.append((job_id, src))
        return f"jobs/{job_id}/current.pdf"

    def upload_regeneration_attempt_pdf(self, job_id: str, attempt_id: str, src: Path) -> str:
        self.uploaded_attempt_pdf.append((job_id, attempt_id, src))
        return f"jobs/{job_id}/attempts/{attempt_id}/current.pdf"

    def upload_preview(self, job_id: str, slide_order: int, src: Path) -> str:
        self.uploaded_previews.append((job_id, slide_order, src))
        return f"jobs/{job_id}/previews/slide-{slide_order:02d}.jpg"

    def upload_regeneration_attempt_preview(
        self,
        job_id: str,
        attempt_id: str,
        slide_order: int,
        src: Path,
    ) -> str:
        self.uploaded_attempt_previews.append((job_id, attempt_id, slide_order, src))
        return f"jobs/{job_id}/attempts/{attempt_id}/previews/slide-{slide_order:02d}.jpg"

    def promote_regeneration_attempt(self, job_id: str, attempt_id: str, slide_order: int) -> None:
        self.promoted_attempts.append((job_id, attempt_id, slide_order))


class FakeToolchain:
    """PPTX 도구 체인 대역."""

    def __init__(self) -> None:
        self.unpack_calls: list[tuple[Path, Path]] = []
        self.selected: tuple[str, ...] = ()
        self.clean_calls: list[Path] = []
        self.pack_calls: list[tuple[Path, Path, Path]] = []

    def unpack(self, input_pptx: Path, output_dir: Path) -> None:
        self.unpack_calls.append((input_pptx, output_dir))
        slides_dir = output_dir / "ppt" / "slides"
        slides_dir.mkdir(parents=True)
        for index in range(1, 8):
            (slides_dir / f"slide{index}.xml").write_text(
                f"<p:sld><p:cSld><p:spTree>template {index}</p:spTree></p:cSld></p:sld>",
                encoding="utf-8",
            )

    def remove_unselected_slides(
        self,
        unpacked_dir: str | Path,
        selected_slide_filenames: list[str],
    ) -> tuple[str, ...]:
        del unpacked_dir
        self.selected = tuple(selected_slide_filenames)
        return self.selected

    def clean(self, unpacked_dir: Path) -> None:
        self.clean_calls.append(unpacked_dir)

    def pack(self, unpacked_dir: Path, output_pptx: Path, *, original_pptx: Path) -> None:
        self.pack_calls.append((unpacked_dir, output_pptx, original_pptx))
        output_pptx.write_bytes(b"packed")

    def validate(self, unpacked_dir: Path, *, original_pptx: Path) -> FakeValidation:
        del unpacked_dir, original_pptx
        return FakeValidation(success=True)

    def repair(self, unpacked_dir: Path, *, original_pptx: Path) -> FakeValidation:
        del unpacked_dir, original_pptx
        return FakeValidation(success=True)


class FakeEditor:
    """SlideEditor 대역."""

    def __init__(
        self,
        slots: list[dict[str, Any]] | None = None,
        *,
        layout_action_error: Exception | None = None,
    ) -> None:
        self.slots = slots or [
            {"shape_id": "2", "font_size_pt": 20, "kind": "text"},
        ]
        self.applied: list[tuple[str, dict[str, dict[str, Any]]]] = []
        self.applied_slot_metadata: list[dict[str, dict[str, Any]]] = []
        self.layout_actions: list[tuple[str, tuple[dict[str, Any], ...]]] = []
        self.layout_action_error = layout_action_error
        self.cleared: list[str] = []
        self.operations: list[tuple[str, str]] = []

    def extract_slots(self, slide_xml_path: str) -> list[dict[str, Any]]:
        return [dict(slot, path=slide_xml_path) for slot in self.slots]

    def apply_layout_actions(
        self,
        slide_xml_path: str,
        layout_actions: tuple[dict[str, Any], ...],
    ) -> None:
        self.operations.append(("layout_actions", slide_xml_path))
        self.layout_actions.append(
            (slide_xml_path, tuple(dict(action) for action in layout_actions))
        )
        if self.layout_action_error is not None:
            raise self.layout_action_error

    def apply_fills(
        self,
        slide_xml_path: str,
        fills: dict[str, dict[str, Any]],
        slot_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]:
        self.operations.append(("fills", slide_xml_path))
        self.applied.append((slide_xml_path, fills))
        self.applied_slot_metadata.append(
            {shape_id: dict(metadata) for shape_id, metadata in (slot_metadata or {}).items()}
        )
        return []

    def clear_content(self, slide_xml_path: str) -> None:
        self.operations.append(("clear", slide_xml_path))
        self.cleared.append(slide_xml_path)
        Path(slide_xml_path).write_text(
            "<p:sld><p:cSld><p:spTree/></p:cSld></p:sld>", encoding="utf-8"
        )


class FakePlanGenerator:
    """항상 7장 slide_plan 을 반환한다."""

    def create_plan(self, *, portfolio_text: str, template_metadata: dict[str, Any]) -> SlidePlan:
        del portfolio_text, template_metadata
        categories = ["cover", "overview", "process", "outcome", "chart", "text", "closing"]
        return SlidePlan(
            selected_slides=tuple(
                PlannedSlide(
                    slide_order=index,
                    source_slide_id=f"{category}_A",
                    category=category,
                    slide_filename=f"slide{index}.xml",
                    content_brief=f"brief-{index}",
                    reason="테스트",
                )
                for index, category in enumerate(categories, start=1)
            ),
            llm_model="fake",
        )


class FakeFillGenerator:
    """슬라이드별 fill 생성 결과/예외를 반환한다."""

    def __init__(
        self,
        responses: dict[int, list[object]] | None = None,
        revise_responses: dict[int, list[object]] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.revise_responses = revise_responses or {}
        self.calls: list[int] = []
        self.slot_calls: list[tuple[int, list[dict[str, Any]]]] = []
        self.revise_calls: list[dict[str, Any]] = []

    def create_fills(
        self, *, content_brief: str, slots: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        slide_order = int(content_brief.rsplit("-", maxsplit=1)[1])
        self.calls.append(slide_order)
        self.slot_calls.append((slide_order, [dict(slot) for slot in slots]))
        responses = self.responses.get(slide_order)
        if responses:
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # type: ignore[return-value]
        return {
            "2": {"action": "text", "text": f"슬라이드 {slide_order}", "font_size_override": 18}
        }

    def revise_fills_for_fit(
        self,
        *,
        content_brief: str,
        slots: list[dict[str, Any]],
        current_fills: dict[str, dict[str, Any]],
        fit_issues: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        slide_order = int(content_brief.rsplit("-", maxsplit=1)[1])
        self.revise_calls.append(
            {
                "slide_order": slide_order,
                "slots": [dict(slot) for slot in slots],
                "current_fills": {
                    str(shape_id): dict(fill) for shape_id, fill in current_fills.items()
                },
                "fit_issues": [dict(issue) for issue in fit_issues],
            }
        )
        responses = self.revise_responses.get(slide_order)
        if responses:
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # type: ignore[return-value]
        raise AssertionError("fit retry 응답이 없습니다.")


class FakeChangeGenerator:
    """사용자 재생성 요청을 고정 부분 fill 로 변환한다."""

    def __init__(self, response: dict[str, dict[str, Any]] | None = None) -> None:
        self.response = (
            {"2": {"action": "text", "text": "기존 제목", "font_size_override": 28}}
            if response is None
            else response
        )
        self.calls: list[dict[str, Any]] = []

    def create_changes(
        self,
        *,
        user_request: str,
        slots: list[dict[str, Any]],
        current_fills: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        self.calls.append(
            {
                "user_request": user_request,
                "slots": slots,
                "current_fills": current_fills,
            }
        )
        return self.response


class FakeRenderer:
    """PPTX 렌더러 대역."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, int | None]] = []

    def render(
        self, pptx_path: Path, output_dir: Path, *, page: int | None = None
    ) -> FakeRenderResult:
        self.calls.append((pptx_path, output_dir, page))
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = output_dir / f"{pptx_path.stem}.pdf"
        pdf_path.write_bytes(b"pdf")
        slides = []
        pages = [page] if page is not None else list(range(1, 8))
        for index in pages:
            image_path = output_dir / f"slide-{index:02d}.jpg"
            image_path.write_bytes(b"jpg")
            slides.append(FakeRenderedSlide(page=index, image_path=image_path))
        return FakeRenderResult(pdf_path=pdf_path, slides=tuple(slides))


class FakeQAStep:
    """시각 QA 단계 대역."""

    def __init__(
        self,
        main_client: FakeMainClient,
        storage: FakeStorage | None = None,
        statuses: dict[int, str] | None = None,
        outcome_fills: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self.main_client = main_client
        self.storage = storage
        self.statuses = statuses or {}
        self.outcome_fills = outcome_fills or {}
        self.received_orders: list[int] = []

    async def process(self, *, job_id: str, slides: list[Any], **kwargs: Any) -> FakeQAResult:
        ready_event = kwargs.get("ready_event", "slide_preview_ready")
        preview_attempt_id = kwargs.get("preview_attempt_id")
        outcomes = []
        for slide in slides:
            self.received_orders.append(slide.slide_order)
            status = self.statuses.get(slide.slide_order, "ready")
            if status == "ready":
                if self.storage is not None:
                    if preview_attempt_id is None:
                        gcs_preview_key = self.storage.upload_preview(
                            job_id,
                            slide.slide_order,
                            slide.image_path,
                        )
                    else:
                        gcs_preview_key = self.storage.upload_regeneration_attempt_preview(
                            job_id,
                            preview_attempt_id,
                            slide.slide_order,
                            slide.image_path,
                        )
                else:
                    gcs_preview_key = f"jobs/{job_id}/previews/slide-{slide.slide_order:02d}.jpg"
                if ready_event is not None:
                    await self.main_client.send_slide_event(
                        job_id,
                        slide.slide_id,
                        event=ready_event,
                        slide_order=slide.slide_order,
                        idempotency_key=f"{job_id}:slide:{slide.slide_id}:{ready_event}",
                        occurred_at="2026-05-27T00:00:00Z",
                        gcs_preview_key=gcs_preview_key,
                    )
            else:
                await self.main_client.send_slide_event(
                    job_id,
                    slide.slide_id,
                    event="slide_preview_error",
                    slide_order=slide.slide_order,
                    idempotency_key=f"{job_id}:slide:{slide.slide_id}:slide_preview_error",
                    occurred_at="2026-05-27T00:00:00Z",
                    message="qa failed",
                    retryable=True,
                )
                gcs_preview_key = None
            outcomes.append(
                FakeQAOutcome(
                    slide.slide_id,
                    slide.slide_order,
                    status,
                    gcs_preview_key=gcs_preview_key,
                    current_fills=dict(
                        self.outcome_fills.get(
                            slide.slide_order,
                            getattr(slide, "current_fills", {}) or {},
                        )
                    ),
                )
            )
        return FakeQAResult(outcomes=tuple(outcomes))


@pytest.mark.asyncio
async def test_generate_pipeline_success_sends_callbacks_and_uploads_outputs() -> None:
    """전체 성공 시 content/render/preview/final 콜백과 업로드가 실행된다."""
    context = PipelineContext()

    await context.service.generate(_task())

    assert context.main_client.closed is True
    assert context.main_client.slide_plan_requests[0]["idempotency_key"] == "job-1:job:slide_plan"
    assert (
        context.main_client.slide_plan_requests[0]["slide_plan"]["selected_slides"][0]["reason"]
        == "테스트"
    )
    content_events = _events(context.main_client, "slide_content_ready")
    assert len(content_events) == 7
    assert content_events[0]["idempotency_key"] == "job-1:slide:slide-1:slide_content_ready"
    assert [event["event"] for event in context.main_client.job_events] == [
        "pipeline_stage_changed",
        "all_completed",
    ]
    assert context.main_client.job_events[-1]["summary"] == {"completed": 7, "failed": 0}
    assert context.main_client.job_events[-1]["gcs_pptx_key"] == "jobs/job-1/current.pptx"
    assert "error_code" not in context.main_client.job_events[-1]
    assert context.storage.uploaded_pptx
    assert context.storage.uploaded_pdf
    assert len(context.toolchain.pack_calls) == 1
    assert len(context.renderer.calls) == 1


@pytest.mark.asyncio
async def test_generate_pipeline_rejects_non_v2_template_meta() -> None:
    """schema_version 이 v2가 아닌 meta.json은 generation pipeline에서 즉시 실패한다."""
    context = PipelineContext()
    context.storage.template_metadata = {"schema_version": 1, "slides": []}

    await context.service.generate(_task())

    assert context.main_client.slide_plan_requests == []
    assert context.main_client.job_events[-1]["summary"] == {"completed": 0, "failed": 1}
    assert context.main_client.job_events[-1]["error_code"] == "TEMPLATE_METADATA_SCHEMA_INVALID"
    assert context.toolchain.unpack_calls == []
    assert context.storage.uploaded_pptx == []


@pytest.mark.asyncio
async def test_content_timeout_retries_then_blanks_slide_and_continues_partial() -> None:
    """Call #2 timeout 1회 재시도 후 실패하면 빈 페이지 처리하고 나머지를 계속한다."""
    filler = FakeFillGenerator(responses={3: [TimeoutError("timeout"), TimeoutError("timeout")]})
    qa_step = FakeQAStep(main_client=FakeMainClient())
    context = PipelineContext(filler=filler, qa_step=qa_step)

    await context.service.generate(_task())

    assert filler.calls.count(3) == 2
    assert len(context.editor.cleared) == 1
    error_events = _events(context.main_client, "slide_content_error")
    assert len(error_events) == 1
    assert error_events[0]["slide_order"] == 3
    assert "timeout" in error_events[0]["message"]
    assert qa_step.received_orders == [1, 2, 4, 5, 6, 7]
    assert context.main_client.job_events[-1]["summary"] == {"completed": 6, "failed": 1}
    assert "error_code" not in context.main_client.job_events[-1]


@pytest.mark.asyncio
async def test_text_fit_overflow_logs_structured_result_and_continues_partial(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """basic_text_area overflow 는 structured log 를 남기고 콘텐츠 실패로 수렴한다."""
    editor = FakeEditor(
        slots=[
            {
                "shape_id": "2",
                "font_size_pt": 12,
                "min_font_pt": 10,
                "kind": "text",
                "fit_policy": "basic_text_area",
                "w_emu": int(80 * EMU_PER_PT),
                "h_emu": int(30 * EMU_PER_PT),
                "max_lines": 1,
                "nowrap": True,
            }
        ]
    )
    filler = FakeFillGenerator(
        responses={
            3: [
                {
                    "2": {
                        "action": "text",
                        "text": "OpenAI API OpenAI API OpenAI API",
                        "font_size_override": 12,
                    }
                }
            ]
        }
    )
    context = PipelineContext(editor=editor, filler=filler, max_content_concurrency=1)

    with caplog.at_level(logging.INFO, logger="features.visualization.generation_pipeline"):
        await context.service.generate(_task())

    error_events = _events(context.main_client, "slide_content_error")
    assert len(error_events) == 1
    assert error_events[0]["slide_order"] == 3
    assert "summarize_needed" in error_events[0]["message"]
    assert len(context.editor.cleared) == 1
    assert context.main_client.job_events[-1]["summary"] == {"completed": 6, "failed": 1}

    text_fit_logs = [
        record.pptx_text_fit for record in caplog.records if hasattr(record, "pptx_text_fit")
    ]
    overflow_log = next(item for item in text_fit_logs if item["slide_order"] == 3)
    assert overflow_log["status"] == "summarize_needed"
    assert overflow_log["reason"] in {"nowrap_width_overflow", "width_overflow"}
    assert overflow_log["applied_font_pt"] == 10
    assert overflow_log["final_layout"]["overflow_reasons"]


@pytest.mark.asyncio
async def test_text_fit_summarize_needed_retries_with_shorter_fills() -> None:
    """summarize_needed overflow 는 한 번 더 짧은 fill 을 요청하고 성공 경로로 복구한다."""
    editor = FakeEditor(
        slots=[
            {
                "shape_id": "2",
                "font_size_pt": 12,
                "min_font_pt": 10,
                "kind": "text",
                "fit_policy": "basic_text_area",
                "w_emu": int(80 * EMU_PER_PT),
                "h_emu": int(30 * EMU_PER_PT),
                "max_lines": 1,
                "nowrap": True,
            }
        ]
    )
    filler = FakeFillGenerator(
        responses={
            3: [
                {
                    "2": {
                        "action": "text",
                        "text": "OpenAI API OpenAI API OpenAI API",
                        "font_size_override": 12,
                    }
                }
            ]
        },
        revise_responses={
            3: [
                {
                    "2": {
                        "action": "text",
                        "text": "AI 상담",
                        "font_size_override": 12,
                    }
                }
            ]
        },
    )
    context = PipelineContext(editor=editor, filler=filler, max_content_concurrency=1)

    await context.service.generate(_task())

    assert _events(context.main_client, "slide_content_error") == []
    assert context.main_client.job_events[-1]["summary"] == {"completed": 7, "failed": 0}
    assert len(filler.revise_calls) == 1
    retry_call = filler.revise_calls[0]
    assert retry_call["slide_order"] == 3
    assert retry_call["fit_issues"][0]["shape_id"] == "2"
    assert retry_call["fit_issues"][0]["status"] == "summarize_needed"
    assert retry_call["fit_issues"][0]["reason"] in {"nowrap_width_overflow", "width_overflow"}
    slide3_fills = [fills for path, fills in editor.applied if path.endswith("slide3.xml")][0]
    assert slide3_fills["2"]["text"] == "AI 상담"


@pytest.mark.asyncio
async def test_text_fit_retry_failure_keeps_original_overflow_error() -> None:
    """짧게 재요청한 fill 도 실패하면 원래 overflow 오류로 콘텐츠 실패를 전송한다."""
    editor = FakeEditor(
        slots=[
            {
                "shape_id": "2",
                "font_size_pt": 12,
                "min_font_pt": 10,
                "kind": "text",
                "fit_policy": "basic_text_area",
                "w_emu": int(80 * EMU_PER_PT),
                "h_emu": int(30 * EMU_PER_PT),
                "max_lines": 1,
                "nowrap": True,
            }
        ]
    )
    filler = FakeFillGenerator(
        responses={
            3: [
                {
                    "2": {
                        "action": "text",
                        "text": "OpenAI API OpenAI API OpenAI API",
                        "font_size_override": 12,
                    }
                }
            ]
        },
        revise_responses={
            3: [
                {
                    "2": {
                        "action": "text",
                        "text": "여전히 너무 긴 OpenAI API 상담 서비스 설명 문구",
                        "font_size_override": 12,
                    }
                }
            ]
        },
    )
    context = PipelineContext(editor=editor, filler=filler, max_content_concurrency=1)

    await context.service.generate(_task())

    error_events = _events(context.main_client, "slide_content_error")
    assert len(error_events) == 1
    assert error_events[0]["slide_order"] == 3
    assert "summarize_needed" in error_events[0]["message"]
    assert len(filler.revise_calls) == 1
    assert len(context.editor.cleared) == 1
    assert context.main_client.job_events[-1]["summary"] == {"completed": 6, "failed": 1}


@pytest.mark.asyncio
async def test_generate_enriches_slots_from_template_metadata_before_preflight() -> None:
    """초기 생성은 v2 metadata slot 용량 정보를 prompt/preflight 에 전달한다."""
    editor = FakeEditor(
        slots=[
            {
                "shape_id": "2",
                "font_size_pt": 12,
                "kind": "text",
                "w_emu": int(120 * EMU_PER_PT),
                "h_emu": int(30 * EMU_PER_PT),
            }
        ]
    )
    filler = FakeFillGenerator(
        responses={
            3: [
                {
                    "2": {
                        "action": "text",
                        "text": "OpenAI API OpenAI API OpenAI API",
                        "font_size_override": 12,
                    }
                }
            ]
        }
    )
    context = PipelineContext(editor=editor, filler=filler, max_content_concurrency=1)
    context.storage.template_metadata = _template_metadata_with_resize_label_slot()

    await context.service.generate(_task())

    assert _events(context.main_client, "slide_content_error") == []
    assert context.main_client.job_events[-1]["summary"] == {"completed": 7, "failed": 0}
    slots_by_order = dict(context.filler.slot_calls)
    assert slots_by_order[3][0]["fit_policy"] == "resize_label"
    assert slots_by_order[3][0]["layout_type"] == "inline_label_group"


@pytest.mark.asyncio
async def test_generate_applies_layout_actions_and_sanitizes_current_fills() -> None:
    """초기 생성은 layout action 을 먼저 적용하고 callback 에 내부 action 을 노출하지 않는다."""
    editor = FakeEditor(slots=_inline_label_slots())
    filler = FakeFillGenerator(
        responses={
            3: [
                {
                    "2": {
                        "action": "text",
                        "text": "OpenAI API OpenAI API OpenAI API",
                        "font_size_override": 20,
                        "layout_actions": [{"action": "internal"}],
                    },
                    "3": {
                        "action": "text",
                        "text": "KPI 개선",
                        "font_size_override": 20,
                    },
                    "layout_actions": {"action": "internal"},
                }
            ]
        }
    )
    context = PipelineContext(editor=editor, filler=filler, max_content_concurrency=1)
    context.storage.template_metadata = _template_metadata_with_inline_label_layout()

    await context.service.generate(_task())

    slide3_ops = [name for name, path in editor.operations if path.endswith("slide3.xml")]
    assert slide3_ops[:2] == ["layout_actions", "fills"]
    assert editor.layout_actions
    layout_action_path, layout_actions = editor.layout_actions[0]
    assert layout_action_path.endswith("slide3.xml")
    assert {action["action"] for action in layout_actions} >= {
        "resize_linked_shape",
        "relayout_row",
    }

    slide3_apply_index = next(
        index for index, (path, _fills) in enumerate(editor.applied) if path.endswith("slide3.xml")
    )
    assert editor.applied_slot_metadata[slide3_apply_index]["2"]["marker_color"] == "#FF0000"
    assert editor.applied_slot_metadata[slide3_apply_index]["2"]["output_text_color"] == "#1F4D1D"

    ready_event = next(
        event
        for event in _events(context.main_client, "slide_content_ready")
        if event["slide_order"] == 3
    )
    assert "layout_actions" not in ready_event["current_fills"]
    assert "layout_actions" not in ready_event["current_fills"]["2"]
    assert context.main_client.job_events[-1]["summary"] == {"completed": 7, "failed": 0}
    assert context.storage.uploaded_pptx


@pytest.mark.asyncio
async def test_generate_layout_action_failure_blanks_slide_and_continues() -> None:
    """layout action 실패는 slide_content_error 와 clear_content 로 격리된다."""
    editor = FakeEditor(
        slots=_inline_label_slots(),
        layout_action_error=ValueError("layout action failed"),
    )
    filler = FakeFillGenerator(
        responses={
            3: [
                {
                    "2": {
                        "action": "text",
                        "text": "OpenAI API OpenAI API OpenAI API",
                        "font_size_override": 20,
                    },
                    "3": {
                        "action": "text",
                        "text": "KPI 개선",
                        "font_size_override": 20,
                    },
                }
            ]
        }
    )
    context = PipelineContext(editor=editor, filler=filler, max_content_concurrency=1)
    context.storage.template_metadata = _template_metadata_with_inline_label_layout()

    await context.service.generate(_task())

    error_events = _events(context.main_client, "slide_content_error")
    assert len(error_events) == 1
    assert error_events[0]["slide_order"] == 3
    assert "layout action failed" in error_events[0]["message"]
    assert len(editor.cleared) == 1
    assert editor.cleared[0].endswith("slide3.xml")
    assert context.main_client.job_events[-1]["summary"] == {"completed": 6, "failed": 1}


@pytest.mark.asyncio
async def test_all_content_failures_send_error_without_render_or_upload() -> None:
    """모든 슬라이드 콘텐츠 생성 실패는 전체 실패 final callback 으로 마감한다."""
    filler = FakeFillGenerator(
        responses={
            index: [TimeoutError("timeout"), TimeoutError("timeout")] for index in range(1, 8)
        }
    )
    context = PipelineContext(filler=filler)

    await context.service.generate(_task())

    assert len(_events(context.main_client, "slide_content_error")) == 7
    assert context.toolchain.pack_calls == []
    assert context.renderer.calls == []
    assert context.storage.uploaded_pptx == []
    assert context.main_client.job_events[-1]["summary"] == {"completed": 0, "failed": 7}
    assert context.main_client.job_events[-1]["error_code"] == "SLIDE_CONTENT_ALL_FAILED"


@pytest.mark.asyncio
async def test_qa_preview_error_is_reflected_in_final_summary() -> None:
    """QA 실패 슬라이드는 preview error 로 남고 final summary.failed 에 반영된다."""
    qa_step = FakeQAStep(main_client=FakeMainClient(), statuses={5: "error"})
    context = PipelineContext(qa_step=qa_step)

    await context.service.generate(_task())

    preview_errors = _events(context.main_client, "slide_preview_error")
    assert len(preview_errors) == 1
    assert preview_errors[0]["slide_order"] == 5
    assert context.main_client.job_events[-1]["summary"] == {"completed": 6, "failed": 1}
    assert "error_code" not in context.main_client.job_events[-1]
    assert context.storage.uploaded_pptx
    assert context.storage.uploaded_pdf


@pytest.mark.asyncio
async def test_all_qa_failures_send_error_without_final_upload() -> None:
    """모든 QA 대상 슬라이드가 실패하면 전체 실패로 마감하고 산출물을 업로드하지 않는다."""
    qa_step = FakeQAStep(
        main_client=FakeMainClient(),
        statuses={index: "error" for index in range(1, 8)},
    )
    context = PipelineContext(qa_step=qa_step)

    await context.service.generate(_task())

    assert len(_events(context.main_client, "slide_preview_error")) == 7
    assert context.main_client.job_events[-1]["summary"] == {"completed": 0, "failed": 7}
    assert context.main_client.job_events[-1]["error_code"] == "VISUAL_QA_ALL_FAILED"
    assert context.storage.uploaded_pptx == []
    assert context.storage.uploaded_pdf == []


@pytest.mark.asyncio
async def test_slide_plan_response_must_return_slide_ids() -> None:
    """slide-plan 이 204처럼 id 목록을 반환하지 않으면 계약 오류로 마감한다."""
    context = PipelineContext(main_client=FakeMainClient(return_slide_rows=False))

    await context.service.generate(_task())

    assert context.main_client.job_events[-1]["summary"] == {"completed": 0, "failed": 7}
    assert context.main_client.job_events[-1]["error_code"] == "SLIDE_PLAN_CALLBACK_FAILED"
    assert context.toolchain.unpack_calls == []


@pytest.mark.asyncio
async def test_slide_content_ready_callback_failure_does_not_blank_generated_slide() -> None:
    """ready 콜백 실패는 콘텐츠 실패로 뒤집지 않고 Cloud Tasks 재시도로 넘긴다."""
    context = PipelineContext(
        main_client=FakeMainClient(fail_slide_events={"slide_content_ready"}),
        max_content_concurrency=1,
    )

    with pytest.raises(RetryableError):
        await context.service.generate(_task())

    assert context.editor.applied
    assert context.editor.cleared == []
    assert _events(context.main_client, "slide_content_error") == []


@pytest.mark.asyncio
async def test_regenerate_user_request_changes_only_requested_shape() -> None:
    """일반 재생성은 지정 도형만 변경하고 미지정 current_fills 는 보존한다."""
    context = PipelineContext()

    await context.service.regenerate(_regenerate_task(user_request="제목 크기 키워줘"))

    assert context.main_client.closed is True
    assert context.storage.downloaded_pptx[0][0] == "job-1"
    assert len(context.editor.applied) == 1
    applied_path, applied_fills = context.editor.applied[0]
    assert applied_path.endswith("slide3.xml")
    assert set(applied_fills) == {"2"}
    assert context.change_generator.calls[0]["user_request"] == "제목 크기 키워줘"
    assert context.filler.calls == []
    assert context.renderer.calls[0][2] == 3

    regenerated_events = _events(context.main_client, "slide_regenerated")
    assert len(regenerated_events) == 1
    event = regenerated_events[0]
    assert event["slide_order"] == 3
    assert event["idempotency_key"] == "job-1:slide:slide-3:slide_regenerated:task-key"
    assert event["gcs_preview_key"] == "jobs/job-1/previews/slide-03.jpg"
    assert event["current_fills"]["2"]["font_size_override"] == 28
    assert event["current_fills"]["3"]["text"] == "본문 유지"
    assert _events(context.main_client, "slide_preview_ready") == []
    assert context.storage.uploaded_pptx == []
    assert context.storage.uploaded_pdf == []
    assert context.storage.uploaded_previews == []
    assert context.storage.uploaded_attempt_pptx[0][:2] == ("job-1", "task-key")
    assert context.storage.uploaded_attempt_pdf[0][:2] == ("job-1", "task-key")
    assert any(
        item[:3] == ("job-1", "task-key", 3) for item in context.storage.uploaded_attempt_previews
    )
    assert context.storage.promoted_attempts == [("job-1", "task-key", 3)]


@pytest.mark.asyncio
async def test_regenerate_enriches_slots_from_downloaded_template_metadata() -> None:
    """재생성은 template_id 로 v2 metadata 를 내려받아 slot 정책을 복원한다."""
    main_client = FakeMainClient()
    main_client.job_context["template_id"] = "blue"
    editor = FakeEditor(
        slots=[
            {
                "shape_id": "2",
                "font_size_pt": 12,
                "kind": "text",
                "current_text": "기존 제목",
                "w_emu": int(120 * EMU_PER_PT),
                "h_emu": int(30 * EMU_PER_PT),
            }
        ]
    )
    change_generator = FakeChangeGenerator(
        response={
            "2": {
                "action": "text",
                "text": "OpenAI API OpenAI API OpenAI API",
                "font_size_override": 12,
            }
        }
    )
    context = PipelineContext(
        main_client=main_client,
        editor=editor,
        change_generator=change_generator,
    )
    context.storage.template_metadata = _template_metadata_with_resize_label_slot()

    await context.service.regenerate(_regenerate_task(user_request="제목을 자세히 써줘"))

    assert _events(context.main_client, "slide_preview_error") == []
    assert len(_events(context.main_client, "slide_regenerated")) == 1
    assert context.change_generator.calls[0]["slots"][0]["fit_policy"] == "resize_label"
    assert context.change_generator.calls[0]["slots"][0]["layout_type"] == "inline_label_group"


@pytest.mark.asyncio
async def test_regenerate_applies_layout_actions_before_fills_with_slot_metadata() -> None:
    """재생성도 layout action 을 먼저 적용하고 marker style metadata 를 전달한다."""
    main_client = FakeMainClient()
    main_client.job_context["template_id"] = "blue"
    editor = FakeEditor(slots=_inline_label_slots())
    change_generator = FakeChangeGenerator(
        response={
            "2": {
                "action": "text",
                "text": "OpenAI API OpenAI API OpenAI API",
                "font_size_override": 20,
                "layout_actions": [{"action": "internal"}],
            },
            "3": {
                "action": "text",
                "text": "KPI 개선",
                "font_size_override": 20,
            },
            "layout_actions": {"action": "internal"},
        }
    )
    context = PipelineContext(
        main_client=main_client,
        editor=editor,
        change_generator=change_generator,
    )
    context.storage.template_metadata = _template_metadata_with_inline_label_layout()

    await context.service.regenerate(_regenerate_task(user_request="라벨을 자세히 써줘"))

    slide3_ops = [name for name, path in editor.operations if path.endswith("slide3.xml")]
    assert slide3_ops[:2] == ["layout_actions", "fills"]
    assert editor.applied_slot_metadata[0]["2"]["output_text_color"] == "#1F4D1D"

    regenerated_event = _events(context.main_client, "slide_regenerated")[0]
    assert "layout_actions" not in regenerated_event["current_fills"]
    assert "layout_actions" not in regenerated_event["current_fills"]["2"]
    assert regenerated_event["current_fills"]["3"]["text"] == "KPI 개선"


@pytest.mark.asyncio
async def test_regenerate_sanitizes_qa_outcome_current_fills() -> None:
    """QA/fix 결과가 내부 layout action 을 되돌려도 최종 callback 에 노출하지 않는다."""
    qa_step = FakeQAStep(
        FakeMainClient(),
        outcome_fills={
            3: {
                "2": {
                    "action": "text",
                    "text": "수정된 제목",
                    "layout_actions": [{"action": "internal"}],
                },
                "layout_actions": {"action": "internal"},
            }
        },
    )
    context = PipelineContext(qa_step=qa_step)

    await context.service.regenerate(_regenerate_task(user_request="제목 크기 키워줘"))

    regenerated_event = _events(context.main_client, "slide_regenerated")[0]
    assert regenerated_event["current_fills"] == {"2": {"action": "text", "text": "수정된 제목"}}


@pytest.mark.asyncio
async def test_regenerate_layout_action_failure_sends_preview_error_without_upload() -> None:
    """재생성 layout action 실패는 preview error 로 격리하고 attempt 산출물을 만들지 않는다."""
    main_client = FakeMainClient()
    main_client.job_context["template_id"] = "blue"
    editor = FakeEditor(
        slots=_inline_label_slots(),
        layout_action_error=ValueError("layout action failed"),
    )
    change_generator = FakeChangeGenerator(
        response={
            "2": {
                "action": "text",
                "text": "OpenAI API OpenAI API OpenAI API",
                "font_size_override": 20,
            },
            "3": {
                "action": "text",
                "text": "KPI 개선",
                "font_size_override": 20,
            },
        }
    )
    context = PipelineContext(
        main_client=main_client,
        editor=editor,
        change_generator=change_generator,
    )
    context.storage.template_metadata = _template_metadata_with_inline_label_layout()

    await context.service.regenerate(_regenerate_task(user_request="라벨을 자세히 써줘"))

    assert _events(context.main_client, "slide_regenerated") == []
    error_events = _events(context.main_client, "slide_preview_error")
    assert len(error_events) == 1
    assert error_events[0]["slide_order"] == 3
    assert error_events[0]["retryable"] is False
    assert "layout action failed" in error_events[0]["message"]
    assert context.storage.uploaded_attempt_pptx == []
    assert context.storage.uploaded_attempt_pdf == []
    assert context.storage.uploaded_attempt_previews == []
    assert context.storage.promoted_attempts == []


@pytest.mark.asyncio
async def test_regenerate_sanitizes_stored_current_fills_before_change_generation() -> None:
    """backend 저장 current_fills 가 오염되어도 변경 생성기와 callback 계약에는 넘기지 않는다."""
    main_client = FakeMainClient()
    main_client.slide_context["current_fills"]["2"]["layout_actions"] = [{"action": "internal"}]
    main_client.slide_context["current_fills"]["layout_actions"] = {"action": "internal"}
    context = PipelineContext(main_client=main_client)

    await context.service.regenerate(_regenerate_task(user_request="제목 크기 키워줘"))

    generator_current_fills = context.change_generator.calls[0]["current_fills"]
    assert "layout_actions" not in generator_current_fills
    assert "layout_actions" not in generator_current_fills["2"]
    regenerated_event = _events(context.main_client, "slide_regenerated")[0]
    assert "layout_actions" not in regenerated_event["current_fills"]
    assert "layout_actions" not in regenerated_event["current_fills"]["2"]


@pytest.mark.asyncio
async def test_regenerate_preserves_unrequested_text_and_chart_fills() -> None:
    """제목만 키우는 요청은 본문과 차트 current_fills 를 그대로 유지한다."""
    main_client = FakeMainClient()
    main_client.slide_context["current_fills"]["8"] = {
        "action": "chart",
        "data": {
            "categories": ["전", "후"],
            "series": [{"name": "전환율", "values": [12, 42]}],
        },
    }
    editor = FakeEditor(
        slots=[
            {"shape_id": "2", "font_size_pt": 20, "kind": "text", "current_text": "기존 제목"},
            {"shape_id": "3", "font_size_pt": 14, "kind": "text", "current_text": "본문 유지"},
            {"shape_id": "8", "kind": "chart", "current_text": "전환율 차트"},
        ]
    )
    context = PipelineContext(main_client=main_client, editor=editor)

    await context.service.regenerate(_regenerate_task(user_request="제목만 키워줘"))

    _, applied_fills = context.editor.applied[0]
    assert set(applied_fills) == {"2"}

    event = _events(context.main_client, "slide_regenerated")[0]
    assert event["current_fills"]["2"]["font_size_override"] == 28
    assert event["current_fills"]["3"]["text"] == "본문 유지"
    assert event["current_fills"]["8"]["data"]["series"][0]["values"] == [12, 42]


@pytest.mark.asyncio
async def test_retry_regenerate_uses_content_brief_without_user_request() -> None:
    """retry 는 userRequest 없이 저장된 content_brief 로 Step 3 fill 생성을 재사용한다."""
    context = PipelineContext()

    await context.service.regenerate(_regenerate_task(is_retry=True, user_request=None))

    assert context.change_generator.calls == []
    assert context.filler.calls == [3]
    assert len(context.editor.applied) == 1
    _, applied_fills = context.editor.applied[0]
    assert applied_fills == {
        "2": {"action": "text", "text": "슬라이드 3", "font_size_override": 18}
    }
    regenerated_events = _events(context.main_client, "slide_regenerated")
    assert regenerated_events[0]["current_fills"] == applied_fills
    assert regenerated_events[0]["gcs_preview_key"] == "jobs/job-1/previews/slide-03.jpg"
    assert context.storage.uploaded_attempt_pptx[0][:2] == ("job-1", "task-key")
    assert context.storage.promoted_attempts == [("job-1", "task-key", 3)]


@pytest.mark.asyncio
async def test_regenerate_failure_sends_preview_error_without_output_upload() -> None:
    """재생성 실패 시 워커는 preview error 만 보내고 current 산출물 업로드를 하지 않는다."""
    change_generator = FakeChangeGenerator(response={})
    context = PipelineContext(change_generator=change_generator)

    await context.service.regenerate(_regenerate_task(user_request="제목 크기 키워줘"))

    assert _events(context.main_client, "slide_regenerated") == []
    error_events = _events(context.main_client, "slide_preview_error")
    assert len(error_events) == 1
    assert error_events[0]["slide_order"] == 3
    assert error_events[0]["retryable"] is False
    assert "변경 지시" in error_events[0]["message"]
    assert context.storage.uploaded_pptx == []
    assert context.storage.uploaded_pdf == []
    assert context.storage.uploaded_previews == []
    assert context.storage.uploaded_attempt_pptx == []
    assert context.storage.uploaded_attempt_pdf == []
    assert context.storage.uploaded_attempt_previews == []
    assert context.storage.promoted_attempts == []


def _regenerate_qa_failure_context() -> PipelineContext:
    qa_step = FakeQAStep(main_client=FakeMainClient(), statuses={3: "error"})
    return PipelineContext(qa_step=qa_step)


def _regenerate_invalid_slide_order_context() -> PipelineContext:
    main_client = FakeMainClient()
    main_client.slide_context["slide_order"] = "3"
    return PipelineContext(main_client=main_client)


def _regenerate_download_error_context() -> PipelineContext:
    context = PipelineContext()
    context.storage.download_error = OSError("gcs unavailable")
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "context_factory",
        "expected_slide_order",
        "expected_retryable",
        "expected_message",
        "expected_downloaded",
        "expected_rendered",
    ),
    [
        pytest.param(
            _regenerate_qa_failure_context,
            3,
            True,
            "qa failed",
            True,
            True,
            id="qa-failure",
        ),
        pytest.param(
            _regenerate_invalid_slide_order_context,
            0,
            False,
            "slide_order",
            False,
            False,
            id="invalid-slide-order",
        ),
        pytest.param(
            _regenerate_download_error_context,
            3,
            True,
            "gcs unavailable",
            False,
            False,
            id="download-error",
        ),
    ],
)
async def test_regenerate_error_scenarios_send_preview_error_without_current_upload(
    context_factory: Callable[[], PipelineContext],
    expected_slide_order: int,
    expected_retryable: bool,
    expected_message: str,
    expected_downloaded: bool,
    expected_rendered: bool,
) -> None:
    """재생성 오류 시나리오는 preview error 만 남기고 current 산출물을 업로드하지 않는다."""
    context = context_factory()

    await context.service.regenerate(_regenerate_task(user_request="제목 크기 키워줘"))

    assert _events(context.main_client, "slide_regenerated") == []
    error_events = _events(context.main_client, "slide_preview_error")
    assert len(error_events) == 1
    assert error_events[0]["slide_order"] == expected_slide_order
    assert error_events[0]["retryable"] is expected_retryable
    assert expected_message in error_events[0]["message"]
    assert bool(context.storage.downloaded_pptx) is expected_downloaded
    assert bool(context.renderer.calls) is expected_rendered
    assert context.storage.uploaded_pptx == []
    assert context.storage.uploaded_pdf == []
    assert context.storage.uploaded_previews == []
    assert context.storage.uploaded_attempt_pptx == []
    assert context.storage.uploaded_attempt_pdf == []
    assert context.storage.promoted_attempts == []


@pytest.mark.asyncio
async def test_regenerate_callback_failure_keeps_canonical_outputs_unchanged() -> None:
    """성공 후보 업로드 후 callback 이 실패하면 canonical promote 를 실행하지 않는다."""
    context = PipelineContext(main_client=FakeMainClient(fail_slide_events={"slide_regenerated"}))

    with pytest.raises(RetryableError):
        await context.service.regenerate(_regenerate_task(user_request="제목 크기 키워줘"))

    assert any(
        item[:3] == ("job-1", "task-key", 3) for item in context.storage.uploaded_attempt_previews
    )
    assert context.storage.uploaded_attempt_pptx[0][:2] == ("job-1", "task-key")
    assert context.storage.uploaded_attempt_pdf[0][:2] == ("job-1", "task-key")
    assert context.storage.uploaded_pptx == []
    assert context.storage.uploaded_pdf == []
    assert context.storage.uploaded_previews == []
    assert context.storage.promoted_attempts == []


@pytest.mark.asyncio
async def test_regenerate_attempt_upload_failure_does_not_expose_attempt_key() -> None:
    """attempt 업로드 실패는 preview error 로 수렴하고 attempt key 를 callback 하지 않는다."""
    context = PipelineContext()
    context.storage.attempt_pptx_error = OSError("attempt upload failed")

    await context.service.regenerate(_regenerate_task(user_request="제목 크기 키워줘"))

    assert _events(context.main_client, "slide_regenerated") == []
    error_events = _events(context.main_client, "slide_preview_error")
    assert len(error_events) == 1
    assert "attempt upload failed" in error_events[0]["message"]
    assert "gcs_preview_key" not in error_events[0]
    assert any(
        item[:3] == ("job-1", "task-key", 3) for item in context.storage.uploaded_attempt_previews
    )
    assert context.storage.uploaded_attempt_pptx == []
    assert context.storage.uploaded_pptx == []
    assert context.storage.uploaded_pdf == []
    assert context.storage.uploaded_previews == []
    assert context.storage.promoted_attempts == []


@pytest.mark.asyncio
async def test_duplicate_regenerate_push_reuses_same_attempt_key() -> None:
    """같은 idempotency key 재시도는 같은 attempt key 로 업로드/promote 되어 충돌 범위가 고정된다."""
    context = PipelineContext()

    await context.service.regenerate(_regenerate_task(user_request="제목 크기 키워줘"))
    await context.service.regenerate(_regenerate_task(user_request="제목 크기 키워줘"))

    assert [item[1] for item in context.storage.uploaded_attempt_pptx] == [
        "task-key",
        "task-key",
    ]
    assert [
        event["idempotency_key"] for event in _events(context.main_client, "slide_regenerated")
    ] == [
        "job-1:slide:slide-3:slide_regenerated:task-key",
        "job-1:slide:slide-3:slide_regenerated:task-key",
    ]
    assert context.storage.promoted_attempts == [
        ("job-1", "task-key", 3),
        ("job-1", "task-key", 3),
    ]


class PipelineContext:
    """파이프라인 테스트 의존성 묶음."""

    def __init__(
        self,
        *,
        main_client: FakeMainClient | None = None,
        filler: FakeFillGenerator | None = None,
        change_generator: FakeChangeGenerator | None = None,
        qa_step: FakeQAStep | None = None,
        editor: FakeEditor | None = None,
        max_content_concurrency: int = 4,
    ) -> None:
        self.main_client = main_client or FakeMainClient()
        self.storage = FakeStorage()
        self.toolchain = FakeToolchain()
        self.editor = editor or FakeEditor()
        self.renderer = FakeRenderer()
        self.filler = filler or FakeFillGenerator()
        self.change_generator = change_generator or FakeChangeGenerator()
        self.qa_step = qa_step or FakeQAStep(self.main_client, self.storage)
        if qa_step is not None:
            qa_step.main_client = self.main_client
            qa_step.storage = self.storage
        self.service = VisualizationTaskService(
            main_client_factory=lambda _: self.main_client,
            storage_factory=lambda: self.storage,
            toolchain_factory=lambda: self.toolchain,
            editor_factory=lambda: self.editor,
            renderer_factory=lambda: self.renderer,
            slide_plan_generator=FakePlanGenerator(),
            content_fill_generator=self.filler,
            slide_change_generator=self.change_generator,
            qa_step_factory=lambda storage, main, editor, toolchain, renderer: self.qa_step,
            clock=lambda: datetime(2026, 5, 27, tzinfo=UTC),
            max_content_concurrency=max_content_concurrency,
        )


def _task() -> GenerateVisualizationTask:
    return GenerateVisualizationTask(
        message_type="viz.generate",
        job_id="job-1",
        portfolio_id=84,
        user_id=69,
        template_id="blue",
        idempotency_key="task-key",
        callback_base_url="http://main.local",
        schema_version=1,
    )


def _regenerate_task(
    *,
    user_request: str | None = "제목 크기 키워줘",
    is_retry: bool = False,
) -> RegenerateVisualizationTask:
    return RegenerateVisualizationTask(
        message_type="viz.regenerate",
        job_id="job-1",
        slide_id="slide-3",
        user_request=user_request,
        is_retry=is_retry,
        idempotency_key="task-key",
        callback_base_url="http://main.local",
        schema_version=1,
    )


def _events(main_client: FakeMainClient, event: str) -> list[dict[str, Any]]:
    return [item for item in main_client.slide_events if item["event"] == event]


def _template_metadata_with_resize_label_slot() -> dict[str, Any]:
    """shape 2를 basic_text_area 가 아닌 inline label slot 으로 선언한 v2 metadata."""
    return {
        "schema_version": 2,
        "template_id": "blue",
        "runtime_slides": [],
        "layout_groups": [],
        "slots": [
            {
                "slot_id": "slide3_shape2",
                "slide_filename": "slide3.xml",
                "shape_id": "2",
                "kind": "text",
                "fit_policy": "resize_label",
                "layout_type": "inline_label_group",
                "max_lines": 1,
                "nowrap": True,
            }
        ],
    }


def _inline_label_slots() -> list[dict[str, Any]]:
    """pipeline test 에서 metadata overlay 를 받을 inline label shape 목록."""
    return [
        {"shape_id": "2", "font_size_pt": 20, "kind": "text", "current_text": "기존 라벨"},
        {"shape_id": "3", "font_size_pt": 20, "kind": "text", "current_text": "기존 값"},
    ]


def _template_metadata_with_inline_label_layout() -> dict[str, Any]:
    """slide3 inline label group 이 layout action 을 만들도록 하는 v2 metadata."""
    return {
        "schema_version": 2,
        "template_id": "blue",
        "runtime_slides": [],
        "layout_groups": [
            {
                "group_id": "slide3_labels",
                "slide_filename": "slide3.xml",
                "layout_type": "inline_label_group",
            }
        ],
        "slots": [
            {
                "slot_id": "slide3_shape2",
                "slide_filename": "slide3.xml",
                "shape_id": "2",
                "kind": "text",
                "fit_policy": "resize_label",
                "layout_type": "inline_label_group",
                "layout_group_id": "slide3_labels",
                "x_emu": 1_100_000,
                "y_emu": 1_000_000,
                "w_emu": 220_000,
                "h_emu": 240_000,
                "row_right_bound_emu": 12_000_000,
                "gap_emu": 400_000,
                "min_gap_emu": 100_000,
                "marker_color": "#FF0000",
                "output_text_color": "#1F4D1D",
                "item_background": {
                    "shape_id": "12",
                    "x_emu": 1_000_000,
                    "y_emu": 940_000,
                    "w_emu": 420_000,
                    "h_emu": 360_000,
                },
            },
            {
                "slot_id": "slide3_shape3",
                "slide_filename": "slide3.xml",
                "shape_id": "3",
                "kind": "text",
                "fit_policy": "resize_label",
                "layout_type": "inline_label_group",
                "layout_group_id": "slide3_labels",
                "x_emu": 2_100_000,
                "y_emu": 1_000_000,
                "w_emu": 220_000,
                "h_emu": 240_000,
                "row_right_bound_emu": 12_000_000,
                "gap_emu": 400_000,
                "min_gap_emu": 100_000,
            },
        ],
    }
