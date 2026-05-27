"""Phase 1 초기 생성 파이프라인 오케스트레이션 테스트."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from common.clients.base_client import MainServerError
from features.visualization.agents import PlannedSlide, SlidePlan
from features.visualization.service import (
    FatalError,
    GenerateVisualizationTask,
    RegenerateVisualizationTask,
    RetryableError,
    VisualizationTaskService,
)


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
        self.job_context = {"status": "pending", "portfolio_text": "Folioo KPI 42% 개선"}
        self.slide_plan_requests: list[dict[str, Any]] = []
        self.slide_events: list[dict[str, Any]] = []
        self.job_events: list[dict[str, Any]] = []
        self.closed = False

    async def get_job_context(self, job_id: str) -> dict[str, Any]:
        assert job_id == "job-1"
        return dict(self.job_context)

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

    def __init__(self) -> None:
        self.uploaded_pptx: list[tuple[str, Path]] = []
        self.uploaded_pdf: list[tuple[str, Path]] = []

    def download_template(self, template_id: str, dest: Path) -> None:
        assert template_id == "blue"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"pptx")

    def download_template_meta(self, template_id: str, dest: Path) -> None:
        assert template_id == "blue"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({"slides": []}), encoding="utf-8")

    def upload_pptx(self, job_id: str, src: Path) -> str:
        self.uploaded_pptx.append((job_id, src))
        return f"jobs/{job_id}/current.pptx"

    def upload_pdf(self, job_id: str, src: Path) -> str:
        self.uploaded_pdf.append((job_id, src))
        return f"jobs/{job_id}/current.pdf"


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

    def __init__(self) -> None:
        self.applied: list[tuple[str, dict[str, dict[str, Any]]]] = []
        self.cleared: list[str] = []

    def extract_slots(self, slide_xml_path: str) -> list[dict[str, Any]]:
        return [{"shape_id": "2", "font_size_pt": 20, "kind": "text", "path": slide_xml_path}]

    def apply_fills(self, slide_xml_path: str, fills: dict[str, dict[str, Any]]) -> None:
        self.applied.append((slide_xml_path, fills))

    def clear_content(self, slide_xml_path: str) -> None:
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

    def __init__(self, responses: dict[int, list[object]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[int] = []

    def create_fills(
        self, *, content_brief: str, slots: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        del slots
        slide_order = int(content_brief.rsplit("-", maxsplit=1)[1])
        self.calls.append(slide_order)
        responses = self.responses.get(slide_order)
        if responses:
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # type: ignore[return-value]
        return {
            "2": {"action": "text", "text": f"슬라이드 {slide_order}", "font_size_override": 18}
        }


class FakeRenderer:
    """PPTX 렌더러 대역."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def render(
        self, pptx_path: Path, output_dir: Path, *, page: int | None = None
    ) -> FakeRenderResult:
        del page
        self.calls.append((pptx_path, output_dir))
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = output_dir / f"{pptx_path.stem}.pdf"
        pdf_path.write_bytes(b"pdf")
        slides = []
        for index in range(1, 8):
            image_path = output_dir / f"slide-{index:02d}.jpg"
            image_path.write_bytes(b"jpg")
            slides.append(FakeRenderedSlide(page=index, image_path=image_path))
        return FakeRenderResult(pdf_path=pdf_path, slides=tuple(slides))


class FakeQAStep:
    """시각 QA 단계 대역."""

    def __init__(self, main_client: FakeMainClient, statuses: dict[int, str] | None = None) -> None:
        self.main_client = main_client
        self.statuses = statuses or {}
        self.received_orders: list[int] = []

    async def process(self, *, job_id: str, slides: list[Any], **kwargs: Any) -> FakeQAResult:
        del kwargs
        outcomes = []
        for slide in slides:
            self.received_orders.append(slide.slide_order)
            status = self.statuses.get(slide.slide_order, "ready")
            if status == "ready":
                await self.main_client.send_slide_event(
                    job_id,
                    slide.slide_id,
                    event="slide_preview_ready",
                    slide_order=slide.slide_order,
                    idempotency_key=f"{job_id}:slide:{slide.slide_id}:slide_preview_ready",
                    occurred_at="2026-05-27T00:00:00Z",
                    gcs_preview_key=f"jobs/{job_id}/previews/slide-{slide.slide_order:02d}.jpg",
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
            outcomes.append(FakeQAOutcome(slide.slide_id, slide.slide_order, status))
        return FakeQAResult(outcomes=tuple(outcomes))


@pytest.mark.asyncio
async def test_generate_pipeline_success_sends_callbacks_and_uploads_outputs() -> None:
    """전체 성공 시 content/render/preview/final 콜백과 업로드가 실행된다."""
    context = PipelineContext()

    await context.service.generate(_task())

    assert context.main_client.closed is True
    assert context.main_client.slide_plan_requests[0]["idempotency_key"] == "job-1:job:slide_plan"
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
async def test_regenerate_unsupported_is_fatal_not_retryable() -> None:
    """미구현 regenerate 는 무한 재시도 없이 fatal 로 ACK 대상이 된다."""
    service = VisualizationTaskService()

    with pytest.raises(FatalError) as exc_info:
        await service.regenerate(
            RegenerateVisualizationTask(
                message_type="viz.regenerate",
                job_id="job-1",
                slide_id="slide-1",
                user_request=None,
                is_retry=False,
                idempotency_key="task-key",
                callback_base_url="http://main.local",
                schema_version=1,
            )
        )

    assert exc_info.value.error_code == "VISUALIZATION_REGENERATE_UNSUPPORTED"


class PipelineContext:
    """파이프라인 테스트 의존성 묶음."""

    def __init__(
        self,
        *,
        main_client: FakeMainClient | None = None,
        filler: FakeFillGenerator | None = None,
        qa_step: FakeQAStep | None = None,
        max_content_concurrency: int = 4,
    ) -> None:
        self.main_client = main_client or FakeMainClient()
        self.storage = FakeStorage()
        self.toolchain = FakeToolchain()
        self.editor = FakeEditor()
        self.renderer = FakeRenderer()
        self.filler = filler or FakeFillGenerator()
        self.qa_step = qa_step or FakeQAStep(self.main_client)
        if qa_step is not None:
            qa_step.main_client = self.main_client
        self.service = VisualizationTaskService(
            main_client_factory=lambda _: self.main_client,
            storage_factory=lambda: self.storage,
            toolchain_factory=lambda: self.toolchain,
            editor_factory=lambda: self.editor,
            renderer_factory=lambda: self.renderer,
            slide_plan_generator=FakePlanGenerator(),
            content_fill_generator=self.filler,
            qa_step_factory=lambda storage, main, editor, toolchain, renderer: self.qa_step,
            clock=lambda: datetime(2026, 5, 27, tzinfo=UTC),
            max_content_concurrency=max_content_concurrency,
        )


def _task() -> GenerateVisualizationTask:
    return GenerateVisualizationTask(
        message_type="viz.generate",
        job_id="job-1",
        portfolio_id="portfolio-1",
        user_id="user-1",
        template_id="blue",
        idempotency_key="task-key",
        callback_base_url="http://main.local",
        schema_version=1,
    )


def _events(main_client: FakeMainClient, event: str) -> list[dict[str, Any]]:
    return [item for item in main_client.slide_events if item["event"] == event]
