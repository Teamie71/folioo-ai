"""시각 QA + fix-and-verify 단계 테스트."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from features.visualization.qa import (
    LLMVisualFixer,
    SlidePreview,
    VisualQA,
    VisualQAFixVerifyStep,
    VisualQAIssue,
    VisualQAResult,
    preview_metadata,
)
from features.visualization.storage.gcs_client import preview_key


@dataclass
class LLMResponse:
    """테스트용 LLM 응답."""

    content: object


class FakeLLM:
    """순서대로 응답하는 LLM 대역."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.messages: list[list[object]] = []

    def invoke(self, messages: list[object]) -> LLMResponse:
        self.messages.append(messages)
        if not self.responses:
            raise AssertionError("LLM 응답이 소진되었습니다.")
        return LLMResponse(self.responses.pop(0))


class FakeSlotEditor:
    """LLMVisualFixer 용 Slot 추출 대역."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def extract_slots(self, slide_xml_path: str) -> list[dict[str, Any]]:
        self.paths.append(slide_xml_path)
        return [
            {
                "shape_id": "2",
                "current_text": "Folioo 매출 42% 개선",
                "font_size_pt": 18,
                "kind": "text",
            }
        ]


class FakeQA:
    """슬라이드 순서별 QA 응답 대역."""

    def __init__(self, responses: dict[int, list[VisualQAResult]]) -> None:
        self.responses = responses
        self.calls: list[tuple[int, dict[str, str]]] = []

    def check_slide(
        self, slide_image_path: Path, expected_content: dict[str, str]
    ) -> VisualQAResult:
        slide_order = _slide_order_from_path(slide_image_path)
        self.calls.append((slide_order, expected_content))
        if not self.responses[slide_order]:
            raise AssertionError(f"slide_order={slide_order} QA 응답이 소진되었습니다.")
        return self.responses[slide_order].pop(0)


class FakeStorage:
    """GCS 업로드 대역."""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, int, Path]] = []
        self.attempt_uploads: list[tuple[str, str, int, Path]] = []

    def upload_preview(self, job_id: str, slide_order: int, src: Path) -> str:
        self.uploads.append((job_id, slide_order, src))
        return preview_key(job_id, slide_order)

    def upload_regeneration_attempt_preview(
        self,
        job_id: str,
        attempt_id: str,
        slide_order: int,
        src: Path,
    ) -> str:
        self.attempt_uploads.append((job_id, attempt_id, slide_order, src))
        return f"jobs/{job_id}/attempts/{attempt_id}/previews/slide-{slide_order:02d}.jpg"


class FakeMainClient:
    """메인 콜백 클라이언트 대역."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def send_slide_event(self, job_id: str, slide_id: str, **kwargs: Any) -> None:
        self.events.append({"job_id": job_id, "slide_id": slide_id, **kwargs})


class FakeEditor:
    """SlideEditor.apply_fills 대역."""

    def __init__(self) -> None:
        self.applied: list[tuple[str, dict[str, dict[str, Any]]]] = []

    def apply_fills(self, slide_xml_path: str, fills: dict[str, dict[str, Any]]) -> None:
        assert Path(slide_xml_path).is_file()
        self.applied.append((slide_xml_path, fills))


class FakeFixer:
    """QA 이슈를 고정 fill 로 변환하는 대역."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, tuple[VisualQAIssue, ...]]] = []

    def build_fills(
        self,
        slide: SlidePreview,
        qa_result: VisualQAResult,
        *,
        slide_xml_path: Path,
        attempt: int,
    ) -> dict[str, dict[str, Any]]:
        assert slide_xml_path.is_file()
        self.calls.append((slide.slide_order, attempt, qa_result.issues))
        return {
            "2": {
                "action": "text",
                "text": f"수정된 슬라이드 {slide.slide_order}",
                "font_size_override": 14,
            }
        }


class FailingFixer:
    """자동 수정 지시 생성 실패 대역."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def build_fills(
        self,
        slide: SlidePreview,
        qa_result: VisualQAResult,
        *,
        slide_xml_path: Path,
        attempt: int,
    ) -> dict[str, dict[str, Any]]:
        del qa_result, slide_xml_path, attempt
        self.calls.append(slide.slide_order)
        raise ValueError("LLM JSON 파싱 실패")


class FakeToolchain:
    """PPTX pack 대역."""

    def __init__(self) -> None:
        self.pack_calls: list[tuple[Path, Path, Path]] = []

    def pack(self, unpacked_dir: Path, output_pptx: Path, *, original_pptx: Path) -> None:
        self.pack_calls.append((unpacked_dir, output_pptx, original_pptx))
        output_pptx.write_bytes(b"pptx")


@dataclass(frozen=True)
class FakeRenderedSlide:
    """렌더된 슬라이드 대역."""

    page: int
    image_path: Path


@dataclass(frozen=True)
class FakeRenderResult:
    """렌더 결과 대역."""

    slides: tuple[FakeRenderedSlide, ...]


class FakeRenderer:
    """PPTX 렌더러 대역."""

    def __init__(self, pages: list[int]) -> None:
        self.pages = pages
        self.calls: list[tuple[Path, Path, int | None]] = []

    def render(
        self,
        pptx_path: Path,
        output_dir: Path,
        *,
        page: int | None = None,
    ) -> FakeRenderResult:
        self.calls.append((pptx_path, output_dir, page))
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered = []
        pages = [page] if page is not None else self.pages
        for rendered_page in pages:
            image_path = output_dir / f"slide-{rendered_page:02d}.jpg"
            _write_jpeg(image_path, width=960 + rendered_page, height=540 + rendered_page)
            rendered.append(FakeRenderedSlide(page=rendered_page, image_path=image_path))
        return FakeRenderResult(slides=tuple(rendered))


def test_visual_qa_classifies_passed_and_issue_slides(tmp_path: Path) -> None:
    """LLM JSON 응답을 통과/이슈 결과로 파싱한다."""
    image_path = tmp_path / "slide-01.jpg"
    _write_jpeg(image_path, width=800, height=450)
    llm = FakeLLM(
        [
            '{"passed": true, "issues": []}',
            '{"passed": false, "issues": [{"code": "placeholder", "message": "안내 문구 잔존"}]}',
        ]
    )
    qa = VisualQA(llm=llm)

    passed = qa.check_slide(image_path, {"brief": "표지", "texts_summary": "Folioo"})
    failed = qa.check_slide(image_path, {"brief": "본문", "texts_summary": "여기에 프로젝트명"})

    assert passed.passed is True
    assert passed.issues == ()
    assert failed.passed is False
    assert failed.issues[0].code == "placeholder"
    assert failed.issues[0].message == "안내 문구 잔존"
    assert len(llm.messages) == 2


def test_visual_qa_parses_first_json_object_from_wrapped_response(tmp_path: Path) -> None:
    """설명 텍스트와 여러 JSON 블록이 있어도 첫 JSON 객체를 안전하게 파싱한다."""
    image_path = tmp_path / "slide-01.jpg"
    _write_jpeg(image_path, width=800, height=450)
    llm = FakeLLM(
        [
            (
                '검사 결과입니다.\n{"passed": true, "issues": []}\n'
                '{"passed": false, "issues": [{"code": "wrong"}]}'
            )
        ]
    )
    qa = VisualQA(llm=llm)

    result = qa.check_slide(image_path, {"brief": "표지"})

    assert result.passed is True
    assert result.issues == ()


def test_llm_visual_fixer_preserves_guardrails_in_prompt(tmp_path: Path) -> None:
    """자동 수정 프롬프트가 숫자·고유명사·성과 지표 보존 가드를 포함한다."""
    slide_xml = tmp_path / "slide1.xml"
    slide_xml.write_text("<p:sld/>", encoding="utf-8")
    llm = FakeLLM(
        [
            (
                '{"fills": {"2": {"action": "text", '
                '"text": "Folioo 매출 42% 개선", "font_size_override": 14}}}'
            )
        ]
    )
    fixer = LLMVisualFixer(llm=llm, editor=FakeSlotEditor())
    slide = SlidePreview(
        slide_id="slide-1",
        slide_order=1,
        slide_filename="slide1.xml",
        image_path=tmp_path / "slide-01.jpg",
        content_brief="Folioo 성과 요약",
        current_fills={"2": {"text": "Folioo 매출 42% 개선"}},
    )

    fills = fixer.build_fills(
        slide,
        VisualQAResult(
            passed=False,
            issues=(VisualQAIssue(code="overflow", message="텍스트 넘침"),),
        ),
        slide_xml_path=slide_xml,
        attempt=1,
    )

    assert fills["2"]["text"] == "Folioo 매출 42% 개선"
    system_prompt = llm.messages[0][0].content
    user_prompt = llm.messages[0][1].content
    assert "숫자" in system_prompt
    assert "고유명사" in system_prompt
    assert "성과 지표" in system_prompt
    assert "Folioo 매출 42% 개선" in user_prompt


@pytest.mark.asyncio
async def test_passed_slides_upload_preview_and_send_ready_callbacks(tmp_path: Path) -> None:
    """정상 슬라이드는 canonical key 로 업로드되고 즉시 ready 콜백이 발신된다."""
    context = _make_step_context(tmp_path, slide_orders=[1, 2])
    qa = FakeQA(
        {
            1: [_passed()],
            2: [_passed()],
        }
    )
    step = context.make_step(qa=qa, renderer=FakeRenderer(pages=[1, 2]))

    result = await step.process(
        job_id="job-1",
        slides=context.slides,
        unpacked_dir=context.unpacked_dir,
        working_pptx_path=context.working_pptx,
        fixed_pptx_path=context.fixed_pptx,
        render_output_dir=context.render_dir,
    )

    assert result.qa_performed is True
    assert result.qa_checked_slide_orders == (1, 2)
    assert [outcome.status for outcome in result.outcomes] == ["ready", "ready"]
    assert [upload[:2] for upload in context.storage.uploads] == [("job-1", 1), ("job-1", 2)]
    assert [event["event"] for event in context.main_client.events] == [
        "slide_preview_ready",
        "slide_preview_ready",
    ]
    first_event = context.main_client.events[0]
    assert first_event["gcs_preview_key"] == "jobs/job-1/previews/slide-01.jpg"
    assert first_event["preview_width"] == 801
    assert first_event["preview_height"] == 451
    assert first_event["preview_byte_size"] == len(context.slides[0].image_path.read_bytes())
    assert context.toolchain.pack_calls == []


@pytest.mark.asyncio
async def test_issue_slides_are_fixed_as_batch_and_only_affected_slides_rechecked(
    tmp_path: Path,
) -> None:
    """이슈 슬라이드만 fix-and-verify 대상이 되고 pack/render 는 배치 1회로 묶인다."""
    context = _make_step_context(tmp_path, slide_orders=[1, 2, 3])
    qa = FakeQA(
        {
            1: [_failed("overflow"), _passed()],
            2: [_passed()],
            3: [_failed("placeholder"), _passed()],
        }
    )
    renderer = FakeRenderer(pages=[1, 2, 3])
    step = context.make_step(qa=qa, renderer=renderer)

    result = await step.process(
        job_id="job-1",
        slides=context.slides,
        unpacked_dir=context.unpacked_dir,
        working_pptx_path=context.working_pptx,
        fixed_pptx_path=context.fixed_pptx,
        render_output_dir=context.render_dir,
    )

    assert [call[0] for call in qa.calls] == [1, 2, 3, 1, 3]
    assert [call[0] for call in context.fixer.calls] == [1, 3]
    assert len(context.editor.applied) == 2
    assert result.fix_attempts == 1
    assert result.pack_count == 1
    assert result.render_count == 1
    assert len(context.toolchain.pack_calls) == 1
    assert len(renderer.calls) == 1
    assert renderer.calls[0][2] is None
    assert [event["slide_order"] for event in context.main_client.events] == [2, 1, 3]
    assert all(event["event"] == "slide_preview_ready" for event in context.main_client.events)
    assert [outcome.status for outcome in result.outcomes] == ["ready", "ready", "ready"]


@pytest.mark.asyncio
async def test_failed_after_max_attempts_sends_retryable_preview_error(tmp_path: Path) -> None:
    """최대 수정 시도 후에도 실패하면 retryable preview error 로 보고한다."""
    context = _make_step_context(tmp_path, slide_orders=[1])
    qa = FakeQA(
        {
            1: [
                _failed("overflow"),
                _failed("overflow"),
                _failed("overflow"),
            ]
        }
    )
    renderer = FakeRenderer(pages=[1])
    step = context.make_step(qa=qa, renderer=renderer)

    result = await step.process(
        job_id="job-1",
        slides=context.slides,
        unpacked_dir=context.unpacked_dir,
        working_pptx_path=context.working_pptx,
        fixed_pptx_path=context.fixed_pptx,
        render_output_dir=context.render_dir,
    )

    assert context.storage.uploads == []
    assert [call[0] for call in context.fixer.calls] == [1, 1]
    assert len(context.toolchain.pack_calls) == 2
    assert len(renderer.calls) == 2
    assert [call[2] for call in renderer.calls] == [1, 1]
    assert len(context.main_client.events) == 1
    event = context.main_client.events[0]
    assert event["event"] == "slide_preview_error"
    assert event["retryable"] is True
    assert "overflow" in event["message"]
    assert result.outcomes[0].status == "error"
    assert result.outcomes[0].qa_attempts == 3
    assert result.fix_attempts == 2


@pytest.mark.asyncio
async def test_ready_event_can_be_suppressed_after_preview_upload(tmp_path: Path) -> None:
    """재생성 경로는 QA preview 업로드 후 성공 콜백을 서비스 계층에서 따로 보낼 수 있다."""
    context = _make_step_context(tmp_path, slide_orders=[1])
    qa = FakeQA({1: [_passed()]})
    step = context.make_step(qa=qa, renderer=FakeRenderer(pages=[1]))

    result = await step.process(
        job_id="job-1",
        slides=context.slides,
        unpacked_dir=context.unpacked_dir,
        working_pptx_path=context.working_pptx,
        fixed_pptx_path=context.fixed_pptx,
        render_output_dir=context.render_dir,
        ready_event=None,
    )

    assert context.storage.uploads[0][:2] == ("job-1", 1)
    assert context.main_client.events == []
    assert result.outcomes[0].status == "ready"
    assert result.outcomes[0].gcs_preview_key == "jobs/job-1/previews/slide-01.jpg"
    assert result.outcomes[0].current_fills["2"]["text"] == "Folioo KPI 10%"


@pytest.mark.asyncio
async def test_preview_attempt_id_uploads_regenerate_preview_to_attempt_key(
    tmp_path: Path,
) -> None:
    """재생성 QA 경로는 canonical preview 대신 attempt preview key 로 업로드할 수 있다."""
    context = _make_step_context(tmp_path, slide_orders=[1])
    qa = FakeQA({1: [_passed()]})
    step = context.make_step(qa=qa, renderer=FakeRenderer(pages=[1]))

    result = await step.process(
        job_id="job-1",
        slides=context.slides,
        unpacked_dir=context.unpacked_dir,
        working_pptx_path=context.working_pptx,
        fixed_pptx_path=context.fixed_pptx,
        render_output_dir=context.render_dir,
        ready_event=None,
        preview_attempt_id="attempt-1",
    )

    assert context.storage.uploads == []
    assert context.storage.attempt_uploads[0][:3] == ("job-1", "attempt-1", 1)
    assert (
        result.outcomes[0].gcs_preview_key == "jobs/job-1/attempts/attempt-1/previews/slide-01.jpg"
    )
    assert context.main_client.events == []


@pytest.mark.asyncio
async def test_failed_after_max_attempts_preserves_non_retryable_issue(
    tmp_path: Path,
) -> None:
    """QA 이슈가 retryable=false 이면 preview error 콜백도 false 로 보낸다."""
    context = _make_step_context(tmp_path, slide_orders=[1])
    qa = FakeQA(
        {
            1: [
                _failed("fatal_layout", retryable=False),
                _failed("fatal_layout", retryable=False),
            ]
        }
    )
    step = context.make_step(qa=qa, renderer=FakeRenderer(pages=[1]), max_fix_attempts=1)

    result = await step.process(
        job_id="job-1",
        slides=context.slides,
        unpacked_dir=context.unpacked_dir,
        working_pptx_path=context.working_pptx,
        fixed_pptx_path=context.fixed_pptx,
        render_output_dir=context.render_dir,
    )

    event = context.main_client.events[0]
    assert event["event"] == "slide_preview_error"
    assert event["retryable"] is False
    assert result.outcomes[0].issues[0].retryable is False


@pytest.mark.asyncio
async def test_fix_failure_reports_slide_error_without_aborting_job(tmp_path: Path) -> None:
    """자동 수정 응답 파싱 실패는 전체 job 중단 대신 해당 슬라이드 error 로 보고한다."""
    context = _make_step_context(tmp_path, slide_orders=[1, 2])
    context.fixer = FailingFixer()
    qa = FakeQA(
        {
            1: [_failed("overflow")],
            2: [_passed()],
        }
    )
    renderer = FakeRenderer(pages=[1, 2])
    step = context.make_step(qa=qa, renderer=renderer, max_fix_attempts=1)

    result = await step.process(
        job_id="job-1",
        slides=context.slides,
        unpacked_dir=context.unpacked_dir,
        working_pptx_path=context.working_pptx,
        fixed_pptx_path=context.fixed_pptx,
        render_output_dir=context.render_dir,
    )

    assert context.fixer.calls == [1]
    assert context.toolchain.pack_calls == []
    assert renderer.calls == []
    assert [event["event"] for event in context.main_client.events] == [
        "slide_preview_ready",
        "slide_preview_error",
    ]
    error_event = context.main_client.events[1]
    assert error_event["slide_order"] == 1
    assert error_event["retryable"] is True
    assert "fix_failed" in error_event["message"]
    assert [outcome.status for outcome in result.outcomes] == ["error", "ready"]


def test_preview_metadata_reads_jpeg_dimensions_and_size(tmp_path: Path) -> None:
    """프리뷰 콜백용 이미지 메타데이터를 JPEG에서 읽는다."""
    image_path = tmp_path / "slide-01.jpg"
    _write_jpeg(image_path, width=1280, height=720)

    metadata = preview_metadata(image_path)

    assert metadata.width == 1280
    assert metadata.height == 720
    assert metadata.byte_size == len(image_path.read_bytes())


@dataclass
class StepContext:
    """Step 컨트롤러 테스트 fixture."""

    unpacked_dir: Path
    working_pptx: Path
    fixed_pptx: Path
    render_dir: Path
    slides: list[SlidePreview]
    storage: FakeStorage
    main_client: FakeMainClient
    editor: FakeEditor
    fixer: FakeFixer
    toolchain: FakeToolchain

    def make_step(
        self,
        *,
        qa: FakeQA,
        renderer: FakeRenderer,
        max_fix_attempts: int = 2,
    ) -> VisualQAFixVerifyStep:
        return VisualQAFixVerifyStep(
            qa=qa,
            storage=self.storage,
            main_client=self.main_client,
            editor=self.editor,
            toolchain=self.toolchain,
            renderer=renderer,
            fixer=self.fixer,
            max_fix_attempts=max_fix_attempts,
            clock=lambda: datetime(2026, 5, 27, 3, 0, tzinfo=UTC),
        )


def _make_step_context(tmp_path: Path, *, slide_orders: list[int]) -> StepContext:
    unpacked_dir = tmp_path / "unpacked"
    slides_dir = unpacked_dir / "ppt" / "slides"
    slides_dir.mkdir(parents=True)
    image_dir = tmp_path / "initial-images"
    image_dir.mkdir()
    slides: list[SlidePreview] = []
    for slide_order in slide_orders:
        slide_filename = f"slide{slide_order}.xml"
        (slides_dir / slide_filename).write_text("<p:sld/>", encoding="utf-8")
        image_path = image_dir / f"slide-{slide_order:02d}.jpg"
        _write_jpeg(image_path, width=800 + slide_order, height=450 + slide_order)
        slides.append(
            SlidePreview(
                slide_id=f"slide-{slide_order}",
                slide_order=slide_order,
                slide_filename=slide_filename,
                image_path=image_path,
                content_brief=f"슬라이드 {slide_order} 요약",
                current_fills={"2": {"text": f"Folioo KPI {slide_order * 10}%"}},
            )
        )

    working_pptx = tmp_path / "working.pptx"
    working_pptx.write_bytes(b"pptx")
    return StepContext(
        unpacked_dir=unpacked_dir,
        working_pptx=working_pptx,
        fixed_pptx=tmp_path / "fixed.pptx",
        render_dir=tmp_path / "rendered",
        slides=slides,
        storage=FakeStorage(),
        main_client=FakeMainClient(),
        editor=FakeEditor(),
        fixer=FakeFixer(),
        toolchain=FakeToolchain(),
    )


def _passed() -> VisualQAResult:
    return VisualQAResult(passed=True)


def _failed(code: str, *, retryable: bool = True) -> VisualQAResult:
    return VisualQAResult(
        passed=False,
        issues=(VisualQAIssue(code=code, message=f"{code} issue", retryable=retryable),),
    )


def _slide_order_from_path(path: Path) -> int:
    return int(path.stem.rsplit("-", maxsplit=1)[1])


def _write_jpeg(path: Path, *, width: int, height: int) -> None:
    """SOF0 width/height 를 포함한 최소 JPEG 바이트를 쓴다."""
    sof0_payload = (
        b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03"
        + b"\x01\x11\x00"
        + b"\x02\x11\x00"
        + b"\x03\x11\x00"
    )
    path.write_bytes(
        b"\xff\xd8"
        + b"\xff\xc0"
        + (len(sof0_payload) + 2).to_bytes(2, "big")
        + sof0_payload
        + b"\xff\xd9"
    )
