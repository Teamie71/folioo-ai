"""Phase 1 PPTX 초기 생성 파이프라인."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from common.clients.base_client import MainServerError
from features.visualization.agents import (
    ContentFillGenerator,
    LLMContentFillGenerator,
    LLMSlideChangeGenerator,
    LLMSlidePlanGenerator,
    PlannedSlide,
    SlideChangeGenerator,
    SlidePlanGenerator,
)
from features.visualization.fills import merge_current_fills
from features.visualization.main_client import VisualizationMainClient
from features.visualization.pptx import (
    PptxRenderer,
    PptxRenderError,
    PptxToolchain,
    PptxToolchainError,
    SlideEditor,
)
from features.visualization.qa import SlidePreview, VisualQA, VisualQAFixVerifyStep
from features.visualization.storage.gcs_client import GcsClient, job_workdir, preview_key
from features.visualization.templates import require_template_v2_metadata
from features.visualization.text_fit import (
    BasicTextFitResult,
    InlineLabelGroupFitResult,
    TextFitPreflightError,
    TextFitPreflightResult,
    apply_text_fit_preflight,
    evaluate_basic_text_area_fit,
)

logger = logging.getLogger(__name__)


class RetryableError(Exception):
    """Cloud Tasks 가 재시도해야 하는 일시적 실패."""


class FatalError(Exception):
    """재시도해도 복구되지 않는 치명적 실패."""

    def __init__(self, message: str, *, error_code: str = "VISUALIZATION_FATAL") -> None:
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GenerateVisualizationTask:
    """초기 PPTX 생성 작업."""

    message_type: Literal["viz.generate"]
    job_id: str
    portfolio_id: int
    user_id: int
    template_id: str
    idempotency_key: str
    callback_base_url: str
    schema_version: int


@dataclass(frozen=True, slots=True)
class RegenerateVisualizationTask:
    """단일 슬라이드 재생성 또는 retry 작업."""

    message_type: Literal["viz.regenerate"]
    job_id: str
    slide_id: str
    user_request: str | None
    is_retry: bool
    idempotency_key: str
    callback_base_url: str
    schema_version: int


class MainClient(Protocol):
    """생성 파이프라인이 사용하는 메인 백엔드 클라이언트 프로토콜."""

    async def get_job_context(self, job_id: str) -> dict[str, Any]:
        """Job 컨텍스트를 조회한다."""
        ...

    async def get_slide_context(self, job_id: str, slide_id: str) -> dict[str, Any]:
        """슬라이드 컨텍스트를 조회한다."""
        ...

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
    ) -> list[dict[str, Any]]:
        """slide-plan 콜백을 보내고 메인 DB slide row 목록을 반환한다."""
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

    async def close(self) -> None:
        """클라이언트 리소스를 정리한다."""
        ...


class StorageClient(Protocol):
    """생성 파이프라인이 사용하는 GCS 클라이언트 프로토콜."""

    def download_template(self, template_id: str, dest: Path) -> None:
        """템플릿 PPTX 를 다운로드한다."""
        ...

    def download_template_meta(self, template_id: str, dest: Path) -> None:
        """템플릿 meta.json 을 다운로드한다."""
        ...

    def download_pptx(self, job_id: str, dest: Path) -> None:
        """current.pptx 를 다운로드한다."""
        ...

    def upload_pptx(self, job_id: str, src: Path) -> str:
        """current.pptx 를 업로드하고 GCS key 를 반환한다."""
        ...

    def upload_regeneration_attempt_pptx(
        self,
        job_id: str,
        attempt_id: str,
        src: Path,
    ) -> str:
        """재생성 PPTX 를 attempt key 로 업로드하고 GCS key 를 반환한다."""
        ...

    def upload_pdf(self, job_id: str, src: Path) -> str:
        """current.pdf 를 업로드하고 GCS key 를 반환한다."""
        ...

    def upload_regeneration_attempt_pdf(
        self,
        job_id: str,
        attempt_id: str,
        src: Path,
    ) -> str:
        """재생성 PDF 를 attempt key 로 업로드하고 GCS key 를 반환한다."""
        ...

    def upload_preview(self, job_id: str, slide_order: int, src: Path) -> str:
        """슬라이드 프리뷰를 업로드하고 GCS key 를 반환한다."""
        ...

    def upload_regeneration_attempt_preview(
        self,
        job_id: str,
        attempt_id: str,
        slide_order: int,
        src: Path,
    ) -> str:
        """재생성 프리뷰를 attempt key 로 업로드하고 GCS key 를 반환한다."""
        ...

    def promote_regeneration_attempt(
        self,
        job_id: str,
        attempt_id: str,
        slide_order: int,
    ) -> Any:
        """재생성 attempt 산출물을 canonical key 로 promote 한다."""
        ...


class Toolchain(Protocol):
    """PPTX 패키지 도구 체인 프로토콜."""

    def unpack(self, input_pptx: Path, output_dir: Path) -> None:
        """PPTX 를 작업 디렉터리에 해제한다."""
        ...

    def remove_unselected_slides(
        self,
        unpacked_dir: str | Path,
        selected_slide_filenames: Sequence[str],
    ) -> tuple[str, ...]:
        """미선택 슬라이드를 제거하고 선택 순서를 보존한다."""
        ...

    def clean(self, unpacked_dir: Path) -> None:
        """미참조 파트를 정리한다."""
        ...

    def pack(self, unpacked_dir: Path, output_pptx: Path, *, original_pptx: Path) -> None:
        """PPTX 를 패키징한다."""
        ...

    def validate(self, unpacked_dir: Path, *, original_pptx: Path) -> Any:
        """PPTX 패키지 검증을 수행한다."""
        ...

    def repair(self, unpacked_dir: Path, *, original_pptx: Path) -> Any:
        """검증 실패 시 auto-repair 를 수행한다."""
        ...


class Renderer(Protocol):
    """PPTX 렌더러 프로토콜."""

    def render(self, pptx_path: Path | str, output_dir: Path | str, *, page: int | None = None):
        """PPTX 를 PDF/JPG 로 렌더링한다."""
        ...


@dataclass(frozen=True, slots=True)
class RegisteredSlide:
    """메인 백엔드에 생성된 슬라이드 행."""

    slide_id: str
    slide_order: int
    source_slide_id: str
    slide_filename: str


@dataclass(frozen=True, slots=True)
class _ContentOutcome:
    """Step 3 슬라이드 콘텐츠 생성 결과."""

    registered_slide: RegisteredSlide
    planned_slide: PlannedSlide
    status: Literal["ready", "error"]
    current_fills: dict[str, Any] | None = None
    message: str | None = None


class _PipelineFatalError(Exception):
    """워커 내부에서 final all_completed 로 마감할 전체 실패."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        failed_count: int | None = None,
    ) -> None:
        self.error_code = error_code
        self.failed_count = failed_count
        super().__init__(message)


MainClientFactory = Callable[[str], MainClient]
StorageFactory = Callable[[], StorageClient]
ToolchainFactory = Callable[[], Toolchain]
EditorFactory = Callable[[], SlideEditor]
RendererFactory = Callable[[], Renderer]
QAStepFactory = Callable[
    [StorageClient, MainClient, SlideEditor, Toolchain, Renderer],
    VisualQAFixVerifyStep,
]


class VisualizationTaskService:
    """시각화 Cloud Tasks 작업을 실제 파이프라인으로 처리한다."""

    def __init__(
        self,
        *,
        main_client_factory: MainClientFactory | None = None,
        storage_factory: StorageFactory | None = None,
        toolchain_factory: ToolchainFactory | None = None,
        editor_factory: EditorFactory | None = None,
        renderer_factory: RendererFactory | None = None,
        slide_plan_generator: SlidePlanGenerator | None = None,
        content_fill_generator: ContentFillGenerator | None = None,
        slide_change_generator: SlideChangeGenerator | None = None,
        qa_step_factory: QAStepFactory | None = None,
        clock: Callable[[], datetime] | None = None,
        max_content_concurrency: int = 4,
    ) -> None:
        if max_content_concurrency <= 0:
            raise ValueError("max_content_concurrency 는 1 이상이어야 합니다.")
        self._main_client_factory = main_client_factory or (
            lambda callback_base_url: VisualizationMainClient(base_url=callback_base_url)
        )
        self._storage_factory = storage_factory or GcsClient
        self._toolchain_factory = toolchain_factory or PptxToolchain.from_env
        self._editor_factory = editor_factory or SlideEditor
        self._renderer_factory = renderer_factory or _default_renderer_factory
        self._slide_plan_generator = slide_plan_generator or LLMSlidePlanGenerator()
        self._content_fill_generator = content_fill_generator or LLMContentFillGenerator()
        self._slide_change_generator = slide_change_generator or LLMSlideChangeGenerator()
        self._qa_step_factory = qa_step_factory or _default_qa_step_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_content_concurrency = max_content_concurrency

    async def generate(self, task: GenerateVisualizationTask) -> None:
        """Phase 1 초기 PPTX 생성 파이프라인을 실행한다."""
        main_client = self._main_client_factory(task.callback_base_url)
        try:
            try:
                await self._generate_with_client(task, main_client)
            except MainServerError as exc:
                raise RetryableError(f"메인 백엔드 콜백/조회에 실패했습니다: {exc}") from exc
        finally:
            await _close_client(main_client)

    async def regenerate(self, task: RegenerateVisualizationTask) -> None:
        """Phase 2 단일 슬라이드 재생성/재시도 파이프라인을 실행한다."""
        main_client = self._main_client_factory(task.callback_base_url)
        try:
            try:
                await self._regenerate_with_client(task, main_client)
            except MainServerError as exc:
                raise RetryableError(f"메인 백엔드 콜백/조회에 실패했습니다: {exc}") from exc
        finally:
            await _close_client(main_client)

    async def _regenerate_with_client(
        self,
        task: RegenerateVisualizationTask,
        main_client: MainClient,
    ) -> None:
        slide_context = await main_client.get_slide_context(task.job_id, task.slide_id)
        attempt_id = _regeneration_attempt_id(task.idempotency_key)
        registered_slide = RegisteredSlide(
            slide_id=task.slide_id,
            slide_order=0,
            source_slide_id="",
            slide_filename="",
        )
        try:
            registered_slide = _registered_slide_from_context(task.slide_id, slide_context)
            current_fills = _current_fills_from_context(slide_context)
            job_context = await main_client.get_job_context(task.job_id)
            content_brief = _content_brief_for_slide(
                job_context,
                registered_slide,
                required=task.is_retry,
            )

            with job_workdir(task.job_id) as workdir:
                storage = self._storage_factory()
                toolchain = self._toolchain_factory()
                editor = self._editor_factory()
                renderer = self._renderer_factory()
                template_metadata = _load_optional_template_metadata(
                    job_context=job_context,
                    storage=storage,
                    workdir=workdir,
                )

                current_pptx = workdir / "current.pptx"
                unpacked_dir = workdir / "unpacked"
                updated_pptx = workdir / "updated.pptx"
                fixed_pptx = workdir / "updated-fixed.pptx"
                initial_render_dir = workdir / "rendered-initial"
                qa_render_dir = workdir / "rendered-qa"
                final_render_dir = workdir / "rendered-final"

                storage.download_pptx(task.job_id, current_pptx)
                toolchain.unpack(current_pptx, unpacked_dir)

                slide_xml_path = _slide_xml_path(unpacked_dir, registered_slide.slide_filename)
                slots = await asyncio.to_thread(editor.extract_slots, str(slide_xml_path))
                slots = _enrich_slots_with_template_metadata(
                    slots,
                    template_metadata=template_metadata,
                    slide_filename=registered_slide.slide_filename,
                )
                if task.is_retry:
                    updated_fills = await self._create_fills_with_timeout_retry(
                        content_brief=content_brief,
                        slots=slots,
                    )
                    preflight = _preflight_text_fills(
                        job_id=task.job_id,
                        slide_id=registered_slide.slide_id,
                        slide_order=registered_slide.slide_order,
                        slots=slots,
                        fills=updated_fills,
                    )
                    await _apply_preflighted_fills(editor, str(slide_xml_path), preflight, slots)
                    updated_fills = _public_current_fills(preflight.fills)
                else:
                    if task.user_request is None:
                        raise ValueError("일반 재생성에는 user_request 가 필요합니다.")
                    changes = await self._create_slide_changes_with_timeout_retry(
                        user_request=task.user_request,
                        slots=slots,
                        current_fills=current_fills,
                    )
                    if not changes:
                        raise ValueError("재생성 변경 지시가 비어 있습니다.")
                    preflight = _preflight_text_fills(
                        job_id=task.job_id,
                        slide_id=registered_slide.slide_id,
                        slide_order=registered_slide.slide_order,
                        slots=slots,
                        fills=changes,
                    )
                    await _apply_preflighted_fills(editor, str(slide_xml_path), preflight, slots)
                    changes = _public_current_fills(preflight.fills)
                    updated_fills = merge_current_fills(current_fills, changes)
                    updated_fills = _public_current_fills(updated_fills)

                self._pack_and_validate(
                    toolchain=toolchain,
                    unpacked_dir=unpacked_dir,
                    output_pptx=updated_pptx,
                    template_pptx=current_pptx,
                )
                render_result = renderer.render(
                    updated_pptx,
                    initial_render_dir,
                    page=registered_slide.slide_order,
                )
                rendered_by_page = {slide.page: slide.image_path for slide in render_result.slides}
                image_path = rendered_by_page.get(registered_slide.slide_order)
                if image_path is None:
                    raise PptxRenderError(
                        f"렌더 결과에 slide_order={registered_slide.slide_order} 이미지가 없습니다."
                    )

                qa_step = self._qa_step_factory(
                    storage,
                    main_client,
                    editor,
                    toolchain,
                    renderer,
                )
                qa_result = await qa_step.process(
                    job_id=task.job_id,
                    slides=[
                        SlidePreview(
                            slide_id=registered_slide.slide_id,
                            slide_order=registered_slide.slide_order,
                            slide_filename=registered_slide.slide_filename,
                            image_path=image_path,
                            content_brief=content_brief,
                            current_fills=updated_fills,
                        )
                    ],
                    unpacked_dir=unpacked_dir,
                    working_pptx_path=updated_pptx,
                    fixed_pptx_path=fixed_pptx,
                    render_output_dir=qa_render_dir,
                    ready_event=None,
                    preview_attempt_id=attempt_id,
                )
                ready_outcome = _single_ready_outcome(qa_result.outcomes)
                if ready_outcome is None:
                    return

                final_pptx = (
                    fixed_pptx if qa_result.pack_count and fixed_pptx.is_file() else updated_pptx
                )
                final_fills = _public_current_fills(
                    getattr(ready_outcome, "current_fills", None) or updated_fills
                )
                final_pdf = render_result.pdf_path
                if final_pptx != updated_pptx:
                    final_pdf = renderer.render(
                        final_pptx,
                        final_render_dir,
                        page=registered_slide.slide_order,
                    ).pdf_path

                storage.upload_regeneration_attempt_pptx(task.job_id, attempt_id, final_pptx)
                storage.upload_regeneration_attempt_pdf(task.job_id, attempt_id, final_pdf)
                canonical_preview_key = preview_key(task.job_id, registered_slide.slide_order)

                await main_client.send_slide_event(
                    task.job_id,
                    registered_slide.slide_id,
                    event="slide_regenerated",
                    slide_order=registered_slide.slide_order,
                    idempotency_key=_slide_event_key(
                        task.job_id,
                        registered_slide.slide_id,
                        "slide_regenerated",
                        attempt_id,
                    ),
                    occurred_at=self._now_iso(),
                    schema_version=task.schema_version,
                    current_fills=final_fills,
                    gcs_preview_key=canonical_preview_key,
                )
                try:
                    storage.promote_regeneration_attempt(
                        task.job_id,
                        attempt_id,
                        registered_slide.slide_order,
                    )
                except Exception as exc:
                    logger.exception(
                        "regenerate artifact promote failed: job_id=%s slide_id=%s attempt_id=%s",
                        task.job_id,
                        task.slide_id,
                        attempt_id,
                    )
                    raise RetryableError(f"재생성 산출물 promote에 실패했습니다: {exc}") from exc
        except RetryableError:
            raise
        except MainServerError:
            raise
        except Exception as exc:
            logger.exception(
                "regenerate pipeline failed: job_id=%s slide_id=%s is_retry=%s",
                task.job_id,
                task.slide_id,
                task.is_retry,
            )
            await self._send_regenerate_preview_error(
                task=task,
                main_client=main_client,
                registered_slide=registered_slide,
                message=str(exc) or "슬라이드 재생성에 실패했습니다.",
                retryable=_is_regenerate_retryable_error(exc),
            )

    async def _generate_with_client(
        self,
        task: GenerateVisualizationTask,
        main_client: MainClient,
    ) -> None:
        planned_total = 0
        try:
            job_context = await main_client.get_job_context(task.job_id)
            portfolio_text = _required_text(job_context, "portfolio_text")

            with job_workdir(task.job_id) as workdir:
                storage = self._storage_factory()
                toolchain = self._toolchain_factory()
                editor = self._editor_factory()
                renderer = self._renderer_factory()

                template_pptx = workdir / "template.pptx"
                template_meta_path = workdir / "meta.json"
                try:
                    storage.download_template(task.template_id, template_pptx)
                    storage.download_template_meta(task.template_id, template_meta_path)
                except Exception as exc:
                    raise _PipelineFatalError(
                        "템플릿 파일을 가져오지 못했습니다.",
                        error_code="TEMPLATE_FETCH_FAILED",
                    ) from exc
                template_metadata = _load_template_metadata(template_meta_path)

                try:
                    slide_plan = await asyncio.to_thread(
                        self._slide_plan_generator.create_plan,
                        portfolio_text=portfolio_text,
                        template_metadata=template_metadata,
                    )
                except Exception as exc:
                    raise _PipelineFatalError(
                        "slide_plan 생성에 실패했습니다.",
                        error_code="SLIDE_PLAN_FAILED",
                    ) from exc
                planned_total = slide_plan.total_slides
                registered_slides = await self._submit_slide_plan(
                    task=task,
                    main_client=main_client,
                    slide_plan=slide_plan,
                )

                unpacked_dir = workdir / "unpacked"
                output_pptx = workdir / "portfolio.pptx"
                fixed_pptx = workdir / "portfolio-fixed.pptx"
                initial_render_dir = workdir / "rendered-initial"
                qa_render_dir = workdir / "rendered-qa"
                final_render_dir = workdir / "rendered-final"

                self._prepare_working_deck(
                    toolchain=toolchain,
                    template_pptx=template_pptx,
                    unpacked_dir=unpacked_dir,
                    slide_plan=slide_plan,
                )

                content_outcomes = await self._generate_slide_contents(
                    task=task,
                    main_client=main_client,
                    editor=editor,
                    unpacked_dir=unpacked_dir,
                    planned_slides=slide_plan.selected_slides,
                    registered_slides=registered_slides,
                    template_metadata=template_metadata,
                )
                ready_content = [
                    outcome for outcome in content_outcomes if outcome.status == "ready"
                ]
                if not ready_content:
                    await self._send_all_completed(
                        task=task,
                        main_client=main_client,
                        completed=0,
                        failed=planned_total,
                        error_code="SLIDE_CONTENT_ALL_FAILED",
                    )
                    return

                self._pack_and_validate(
                    toolchain=toolchain,
                    unpacked_dir=unpacked_dir,
                    output_pptx=output_pptx,
                    template_pptx=template_pptx,
                )
                await self._send_rendering_event(task, main_client)

                render_result = renderer.render(output_pptx, initial_render_dir)
                rendered_by_page = {slide.page: slide.image_path for slide in render_result.slides}
                qa_slides = _build_qa_slides(ready_content, rendered_by_page)

                qa_step = self._qa_step_factory(
                    storage,
                    main_client,
                    editor,
                    toolchain,
                    renderer,
                )
                qa_result = await qa_step.process(
                    job_id=task.job_id,
                    slides=qa_slides,
                    unpacked_dir=unpacked_dir,
                    working_pptx_path=output_pptx,
                    fixed_pptx_path=fixed_pptx,
                    render_output_dir=qa_render_dir,
                )

                completed = sum(1 for outcome in qa_result.outcomes if outcome.status == "ready")
                failed = planned_total - completed
                if completed == 0:
                    await self._send_all_completed(
                        task=task,
                        main_client=main_client,
                        completed=0,
                        failed=planned_total,
                        error_code="VISUAL_QA_ALL_FAILED",
                    )
                    return

                final_pptx = (
                    fixed_pptx if qa_result.pack_count and fixed_pptx.is_file() else output_pptx
                )
                final_pdf = render_result.pdf_path
                if final_pptx != output_pptx:
                    final_pdf = renderer.render(final_pptx, final_render_dir).pdf_path

                try:
                    gcs_pptx_key = storage.upload_pptx(task.job_id, final_pptx)
                    storage.upload_pdf(task.job_id, final_pdf)
                except Exception as exc:
                    raise _PipelineFatalError(
                        "최종 산출물 업로드에 실패했습니다.",
                        error_code="UPLOAD_FAILED",
                        failed_count=planned_total,
                    ) from exc
                await self._send_all_completed(
                    task=task,
                    main_client=main_client,
                    completed=completed,
                    failed=failed,
                    gcs_pptx_key=gcs_pptx_key,
                )
        except _PipelineFatalError as exc:
            failed_count = exc.failed_count or planned_total or 1
            await self._send_all_completed(
                task=task,
                main_client=main_client,
                completed=0,
                failed=failed_count,
                error_code=exc.error_code,
            )
        except (OSError, ValueError):
            logger.exception("generate pipeline fatal input error: job_id=%s", task.job_id)
            failed_count = planned_total or 1
            await self._send_all_completed(
                task=task,
                main_client=main_client,
                completed=0,
                failed=failed_count,
                error_code="GENERATION_PIPELINE_FAILED",
            )
        except (PptxToolchainError, PptxRenderError) as exc:
            logger.exception("generate pipeline render/package error: job_id=%s", task.job_id)
            failed_count = planned_total or 1
            code = "RENDER_FAILED" if isinstance(exc, PptxRenderError) else "PPTX_PACK_FAILED"
            await self._send_all_completed(
                task=task,
                main_client=main_client,
                completed=0,
                failed=failed_count,
                error_code=code,
            )

    async def _submit_slide_plan(
        self,
        *,
        task: GenerateVisualizationTask,
        main_client: MainClient,
        slide_plan: Any,
    ) -> dict[int, RegisteredSlide]:
        callback_slides = [slide.to_callback_item() for slide in slide_plan.selected_slides]
        response_slides = await main_client.submit_slide_plan(
            task.job_id,
            total_slides=slide_plan.total_slides,
            template_id=task.template_id,
            slide_plan=slide_plan.to_blob(),
            slides=callback_slides,
            idempotency_key=_job_event_key(task.job_id, "slide_plan"),
            schema_version=task.schema_version,
        )
        registered = _registered_slides_by_order(response_slides)
        expected_orders = {slide.slide_order for slide in slide_plan.selected_slides}
        if set(registered) != expected_orders:
            raise _PipelineFatalError(
                "slide-plan 응답에 필요한 slide id 매핑이 없습니다.",
                error_code="SLIDE_PLAN_CALLBACK_FAILED",
                failed_count=slide_plan.total_slides,
            )
        return registered

    def _prepare_working_deck(
        self,
        *,
        toolchain: Toolchain,
        template_pptx: Path,
        unpacked_dir: Path,
        slide_plan: Any,
    ) -> None:
        selected_filenames = [slide.slide_filename for slide in slide_plan.selected_slides]
        toolchain.unpack(template_pptx, unpacked_dir)
        remaining = toolchain.remove_unselected_slides(unpacked_dir, selected_filenames)
        if tuple(selected_filenames) != tuple(remaining):
            raise _PipelineFatalError(
                "선택 슬라이드 순서와 작업 deck 순서가 일치하지 않습니다.",
                error_code="PPTX_SELECTION_FAILED",
                failed_count=slide_plan.total_slides,
            )
        toolchain.clean(unpacked_dir)

    async def _generate_slide_contents(
        self,
        *,
        task: GenerateVisualizationTask,
        main_client: MainClient,
        editor: SlideEditor,
        unpacked_dir: Path,
        planned_slides: Sequence[PlannedSlide],
        registered_slides: dict[int, RegisteredSlide],
        template_metadata: Mapping[str, Any],
    ) -> list[_ContentOutcome]:
        semaphore = asyncio.Semaphore(self._max_content_concurrency)
        tasks = [
            asyncio.create_task(
                self._generate_one_slide_content(
                    task=task,
                    main_client=main_client,
                    editor=editor,
                    unpacked_dir=unpacked_dir,
                    planned_slide=planned_slide,
                    registered_slide=registered_slides[planned_slide.slide_order],
                    template_metadata=template_metadata,
                    semaphore=semaphore,
                )
            )
            for planned_slide in planned_slides
        ]
        outcomes: list[_ContentOutcome] = []
        try:
            for future in asyncio.as_completed(tasks):
                outcomes.append(await future)
            return outcomes
        except Exception:
            for pending in tasks:
                pending.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _generate_one_slide_content(
        self,
        *,
        task: GenerateVisualizationTask,
        main_client: MainClient,
        editor: SlideEditor,
        unpacked_dir: Path,
        planned_slide: PlannedSlide,
        registered_slide: RegisteredSlide,
        template_metadata: Mapping[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> _ContentOutcome:
        async with semaphore:
            slide_xml_path = _slide_xml_path(unpacked_dir, planned_slide.slide_filename)
            try:
                slots = await asyncio.to_thread(editor.extract_slots, str(slide_xml_path))
                slots = _enrich_slots_with_template_metadata(
                    slots,
                    template_metadata=template_metadata,
                    slide_filename=planned_slide.slide_filename,
                )
                fills = await self._create_fills_with_timeout_retry(
                    content_brief=planned_slide.content_brief,
                    slots=slots,
                )
                preflight = await self._preflight_text_fills_with_fit_retry(
                    job_id=task.job_id,
                    slide_id=registered_slide.slide_id,
                    slide_order=registered_slide.slide_order,
                    content_brief=planned_slide.content_brief,
                    slots=slots,
                    fills=fills,
                )
                await _apply_preflighted_fills(editor, str(slide_xml_path), preflight, slots)
                fills = _public_current_fills(preflight.fills)
            except Exception as exc:
                logger.warning(
                    "slide content generation failed: job_id=%s slide_order=%s error=%s",
                    task.job_id,
                    registered_slide.slide_order,
                    exc,
                )
                await asyncio.to_thread(editor.clear_content, str(slide_xml_path))
                message = str(exc) or "슬라이드 콘텐츠 생성에 실패했습니다."
                await main_client.send_slide_event(
                    task.job_id,
                    registered_slide.slide_id,
                    event="slide_content_error",
                    slide_order=registered_slide.slide_order,
                    idempotency_key=_slide_event_key(
                        task.job_id,
                        registered_slide.slide_id,
                        "slide_content_error",
                    ),
                    occurred_at=self._now_iso(),
                    schema_version=task.schema_version,
                    message=message,
                    retryable=True,
                )
                return _ContentOutcome(
                    registered_slide=registered_slide,
                    planned_slide=planned_slide,
                    status="error",
                    message=message,
                )
            try:
                await main_client.send_slide_event(
                    task.job_id,
                    registered_slide.slide_id,
                    event="slide_content_ready",
                    slide_order=registered_slide.slide_order,
                    idempotency_key=_slide_event_key(
                        task.job_id,
                        registered_slide.slide_id,
                        "slide_content_ready",
                    ),
                    occurred_at=self._now_iso(),
                    schema_version=task.schema_version,
                    current_fills=fills,
                )
            except Exception:
                logger.exception(
                    "slide_content_ready callback failed: job_id=%s slide_order=%s",
                    task.job_id,
                    registered_slide.slide_order,
                )
                raise
            return _ContentOutcome(
                registered_slide=registered_slide,
                planned_slide=planned_slide,
                status="ready",
                current_fills=fills,
            )

    async def _preflight_text_fills_with_fit_retry(
        self,
        *,
        job_id: str,
        slide_id: str,
        slide_order: int,
        content_brief: str,
        slots: Sequence[Mapping[str, Any]],
        fills: Mapping[str, Mapping[str, Any]],
    ) -> TextFitPreflightResult:
        """summarize_needed text-fit 실패는 LLM에 한 번 짧게 재요청한다."""
        try:
            return _preflight_text_fills(
                job_id=job_id,
                slide_id=slide_id,
                slide_order=slide_order,
                slots=slots,
                fills=fills,
            )
        except TextFitPreflightError as exc:
            if not _text_fit_error_retryable(exc):
                raise
            fit_issues = _text_fit_retry_issues(exc, fills)
            logger.info(
                "slide content fit retry: job_id=%s slide_order=%s blocking_slots=%s",
                job_id,
                slide_order,
                ",".join(str(issue.get("shape_id")) for issue in fit_issues),
            )
            try:
                revised_fills = await asyncio.to_thread(
                    self._content_fill_generator.revise_fills_for_fit,
                    content_brief=content_brief,
                    slots=slots,
                    current_fills=fills,
                    fit_issues=fit_issues,
                )
                try:
                    preflight = _preflight_text_fills(
                        job_id=job_id,
                        slide_id=slide_id,
                        slide_order=slide_order,
                        slots=slots,
                        fills=revised_fills,
                    )
                except TextFitPreflightError as retry_exc:
                    if not _text_fit_error_retryable(retry_exc):
                        raise
                    fallback_fills = _condense_text_fills_for_fit(
                        slots=slots,
                        fills=revised_fills,
                        exc=retry_exc,
                    )
                    logger.info(
                        "slide content fit deterministic fallback: "
                        "job_id=%s slide_order=%s blocking_slots=%s",
                        job_id,
                        slide_order,
                        ",".join(
                            str(issue.get("shape_id"))
                            for issue in _text_fit_retry_issues(retry_exc, revised_fills)
                        ),
                    )
                    preflight = _preflight_text_fills(
                        job_id=job_id,
                        slide_id=slide_id,
                        slide_order=slide_order,
                        slots=slots,
                        fills=fallback_fills,
                    )
                    logger.info(
                        "slide content fit deterministic fallback succeeded: "
                        "job_id=%s slide_order=%s",
                        job_id,
                        slide_order,
                    )
            except Exception:
                logger.warning(
                    "slide content fit retry failed: job_id=%s slide_order=%s",
                    job_id,
                    slide_order,
                    exc_info=True,
                )
                raise exc
            logger.info(
                "slide content fit retry succeeded: job_id=%s slide_order=%s",
                job_id,
                slide_order,
            )
            return preflight

    async def _create_fills_with_timeout_retry(
        self,
        *,
        content_brief: str,
        slots: Sequence[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        for attempt in range(1, 3):
            try:
                return await asyncio.to_thread(
                    self._content_fill_generator.create_fills,
                    content_brief=content_brief,
                    slots=slots,
                )
            except Exception as exc:
                if attempt == 1 and _is_timeout_error(exc):
                    continue
                raise
        raise RuntimeError("슬라이드 콘텐츠 생성 재시도에 실패했습니다.")

    async def _create_slide_changes_with_timeout_retry(
        self,
        *,
        user_request: str,
        slots: Sequence[Mapping[str, Any]],
        current_fills: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        for attempt in range(1, 3):
            try:
                return await asyncio.to_thread(
                    self._slide_change_generator.create_changes,
                    user_request=user_request,
                    slots=slots,
                    current_fills=current_fills,
                )
            except Exception as exc:
                if attempt == 1 and _is_timeout_error(exc):
                    continue
                raise
        raise RuntimeError("슬라이드 재생성 변경 지시 생성 재시도에 실패했습니다.")

    def _pack_and_validate(
        self,
        *,
        toolchain: Toolchain,
        unpacked_dir: Path,
        output_pptx: Path,
        template_pptx: Path,
    ) -> None:
        output_pptx.parent.mkdir(parents=True, exist_ok=True)
        toolchain.pack(unpacked_dir, output_pptx, original_pptx=template_pptx)
        validation = toolchain.validate(unpacked_dir, original_pptx=template_pptx)
        if validation.success:
            return

        repair_result = toolchain.repair(unpacked_dir, original_pptx=template_pptx)
        if not repair_result.success:
            raise PptxToolchainError(
                "PPTX auto-repair 실행이 실패했습니다.\n"
                f"stdout:\n{repair_result.stdout}\n"
                f"stderr:\n{repair_result.stderr}"
            )
        toolchain.pack(unpacked_dir, output_pptx, original_pptx=template_pptx)
        validation = toolchain.validate(unpacked_dir, original_pptx=template_pptx)
        if not validation.success:
            raise PptxToolchainError(
                "PPTX 검증이 repair 이후에도 실패했습니다.\n"
                f"stdout:\n{validation.stdout}\n"
                f"stderr:\n{validation.stderr}"
            )

    async def _send_rendering_event(
        self,
        task: GenerateVisualizationTask,
        main_client: MainClient,
    ) -> None:
        await main_client.send_job_event(
            task.job_id,
            event="pipeline_stage_changed",
            pipeline_stage="rendering",
            idempotency_key=_job_event_key(
                task.job_id,
                "pipeline_stage_changed",
                "rendering",
            ),
            occurred_at=self._now_iso(),
            schema_version=task.schema_version,
        )

    async def _send_all_completed(
        self,
        *,
        task: GenerateVisualizationTask,
        main_client: MainClient,
        completed: int,
        failed: int,
        gcs_pptx_key: str | None = None,
        error_code: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {
            "event": "all_completed",
            "idempotency_key": _job_event_key(task.job_id, "all_completed"),
            "occurred_at": self._now_iso(),
            "schema_version": task.schema_version,
            "summary": {"completed": completed, "failed": failed},
        }
        if gcs_pptx_key is not None:
            kwargs["gcs_pptx_key"] = gcs_pptx_key
        if error_code is not None:
            kwargs["error_code"] = error_code
        await main_client.send_job_event(
            task.job_id,
            **kwargs,
        )

    async def _send_regenerate_preview_error(
        self,
        *,
        task: RegenerateVisualizationTask,
        main_client: MainClient,
        registered_slide: RegisteredSlide,
        message: str,
        retryable: bool,
    ) -> None:
        await main_client.send_slide_event(
            task.job_id,
            registered_slide.slide_id,
            event="slide_preview_error",
            slide_order=registered_slide.slide_order,
            idempotency_key=_slide_event_key(
                task.job_id,
                registered_slide.slide_id,
                "slide_preview_error",
            ),
            occurred_at=self._now_iso(),
            schema_version=task.schema_version,
            message=message,
            retryable=retryable,
        )

    def _now_iso(self) -> str:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


_service: VisualizationTaskService | None = None


def get_visualization_task_service() -> VisualizationTaskService:
    """시각화 작업 서비스 싱글톤 반환."""
    global _service

    if _service is None:
        _service = VisualizationTaskService()
    return _service


def reset_visualization_task_service() -> None:
    """테스트용 시각화 작업 서비스 싱글톤 초기화."""
    global _service

    _service = None


__all__ = [
    "FatalError",
    "GenerateVisualizationTask",
    "RegenerateVisualizationTask",
    "RetryableError",
    "VisualizationTaskService",
    "get_visualization_task_service",
    "reset_visualization_task_service",
]


def _preflight_text_fills(
    *,
    job_id: str,
    slide_id: str,
    slide_order: int,
    slots: Sequence[Mapping[str, Any]],
    fills: Mapping[str, Mapping[str, Any]],
) -> TextFitPreflightResult:
    """OOXML 적용 전 deterministic text fit preflight 를 실행한다."""
    try:
        preflight = apply_text_fit_preflight(slots=slots, fills=fills)
    except TextFitPreflightError as exc:
        for result in exc.results:
            _log_text_fit_result(
                job_id=job_id,
                slide_id=slide_id,
                slide_order=slide_order,
                result=result,
            )
        raise

    for result in preflight.results:
        _log_text_fit_result(
            job_id=job_id,
            slide_id=slide_id,
            slide_order=slide_order,
            result=result,
        )
    for result in preflight.inline_label_results:
        _log_text_fit_result(
            job_id=job_id,
            slide_id=slide_id,
            slide_order=slide_order,
            result=result,
        )
    if preflight.layout_actions:
        logger.info(
            "pptx layout actions calculated",
            extra={
                "pptx_layout_actions": {
                    "job_id": job_id,
                    "slide_id": slide_id,
                    "slide_order": slide_order,
                    "layout_action_count": len(preflight.layout_actions),
                    "layout_actions": list(preflight.layout_actions),
                }
            },
        )
    return preflight


def _text_fit_error_retryable(exc: TextFitPreflightError) -> bool:
    """LLM 재요약으로 복구 가능한 text-fit 실패인지 판정한다."""
    blocking = [
        result
        for result in exc.results
        if isinstance(result, BasicTextFitResult) and result.is_blocking
    ]
    if not blocking:
        return False
    return all(result.status == "summarize_needed" for result in blocking)


def _text_fit_retry_issues(
    exc: TextFitPreflightError,
    fills: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """LLM 재요약 prompt에 넣을 text-fit 실패 요약을 만든다."""
    issues: list[dict[str, Any]] = []
    for result in exc.results:
        if not isinstance(result, BasicTextFitResult) or not result.is_blocking:
            continue
        shape_id = result.shape_id
        current_fill = fills.get(shape_id, {})
        current_text = str(current_fill.get("text") or "")
        issues.append(
            {
                "shape_id": shape_id,
                "status": result.status,
                "reason": result.reason,
                "current_text": current_text,
                "current_char_count": len(current_text.replace("\n", "")),
                "nowrap": result.nowrap,
                "max_lines": result.max_lines,
                "applied_font_pt": result.applied_font_pt,
                "min_font_pt": result.min_font_pt,
                "available_width_pt": result.constraints.available_width_pt,
                "final_max_line_width_pt": result.final_layout.max_line_width_pt,
                "max_recommended_char_count": _recommended_fit_char_count(
                    result,
                    current_text,
                ),
                "overflow_reasons": list(result.final_layout.overflow_reasons),
            }
        )
    return issues


def _recommended_fit_char_count(result: BasicTextFitResult, current_text: str) -> int:
    """LLM retry 가 참고할 text-fit 기반 권장 글자 수를 계산한다."""
    current_count = len(current_text.replace("\n", ""))
    if current_count <= 0:
        return 0
    available_width = result.constraints.available_width_pt
    final_width = result.final_layout.max_line_width_pt
    if available_width <= 0 or final_width <= 0:
        return max(1, current_count // 2)
    fit_ratio = min(1.0, available_width / final_width)
    return max(1, int(current_count * fit_ratio * 0.85))


def _condense_text_fills_for_fit(
    *,
    slots: Sequence[Mapping[str, Any]],
    fills: Mapping[str, Mapping[str, Any]],
    exc: TextFitPreflightError,
) -> dict[str, dict[str, Any]]:
    """LLM retry 이후에도 넘치는 basic text slot 을 측정 기반으로 축약한다."""
    slots_by_id = {str(slot.get("shape_id")): slot for slot in slots if slot.get("shape_id")}
    adjusted = {str(shape_id): dict(fill) for shape_id, fill in fills.items()}
    for result in exc.results:
        if not isinstance(result, BasicTextFitResult) or result.status != "summarize_needed":
            continue
        shape_id = result.shape_id
        slot = slots_by_id.get(shape_id)
        fill = adjusted.get(shape_id)
        if slot is None or fill is None or str(fill.get("action") or "text") != "text":
            continue
        text = str(fill.get("text") or "")
        condensed = _condense_text_to_fit(slot=slot, fill=fill, result=result, text=text)
        if condensed is None:
            continue
        next_fill = dict(fill)
        next_fill["text"] = condensed
        next_fill["font_size_override"] = result.min_font_pt
        adjusted[shape_id] = next_fill
    return adjusted


def _condense_text_to_fit(
    *,
    slot: Mapping[str, Any],
    fill: Mapping[str, Any],
    result: BasicTextFitResult,
    text: str,
) -> str | None:
    """단일 text slot 에 들어가는 가장 긴 prefix 후보를 찾는다."""
    normalized = _normalize_fit_text(text)
    if not normalized:
        return normalized if _text_candidate_fits(slot, fill, result, normalized) else None
    if _text_candidate_fits(slot, fill, result, normalized):
        return normalized

    for candidate in _dash_preserving_candidates(normalized):
        if _text_candidate_fits(slot, fill, result, candidate):
            return candidate

    token_prefix = _longest_fitting_token_prefix(slot, fill, result, normalized)
    if token_prefix:
        return token_prefix
    return _longest_fitting_char_prefix(slot, fill, result, normalized)


def _normalize_fit_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_DASH_SEPARATOR_PATTERN = re.compile("\\s+([-\\u2013\\u2014])\\s+")


def _dash_preserving_candidates(text: str) -> list[str]:
    """`경험명 - 역할` 형태는 양쪽 핵심을 남긴 후보를 우선 시도한다."""
    match = _DASH_SEPARATOR_PATTERN.search(text)
    if match is None:
        return []

    left_tokens = _text_tokens(text[: match.start()])
    right_tokens = _text_tokens(text[match.end() :])
    if not left_tokens or not right_tokens:
        return []

    separator = f" {match.group(1)} "
    candidates: set[str] = set()
    for left_count in range(len(left_tokens), 0, -1):
        for right_count in range(len(right_tokens), 0, -1):
            candidates.add(
                f"{' '.join(left_tokens[:left_count])}"
                f"{separator}"
                f"{' '.join(right_tokens[:right_count])}"
            )
    return sorted(candidates, key=len, reverse=True)


def _longest_fitting_token_prefix(
    slot: Mapping[str, Any],
    fill: Mapping[str, Any],
    result: BasicTextFitResult,
    text: str,
) -> str | None:
    tokens = _text_tokens(text)
    if not tokens:
        return None

    best: str | None = None
    low = 1
    high = len(tokens)
    while low <= high:
        mid = (low + high) // 2
        candidate = _clean_condensed_text(" ".join(tokens[:mid]))
        if candidate and _text_candidate_fits(slot, fill, result, candidate):
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def _longest_fitting_char_prefix(
    slot: Mapping[str, Any],
    fill: Mapping[str, Any],
    result: BasicTextFitResult,
    text: str,
) -> str | None:
    best: str | None = None
    low = 1
    high = len(text)
    while low <= high:
        mid = (low + high) // 2
        candidate = _clean_condensed_text(text[:mid])
        if candidate and _text_candidate_fits(slot, fill, result, candidate):
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def _text_candidate_fits(
    slot: Mapping[str, Any],
    fill: Mapping[str, Any],
    result: BasicTextFitResult,
    text: str,
) -> bool:
    candidate_fill = dict(fill)
    candidate_fill["text"] = text
    candidate_fill["font_size_override"] = result.min_font_pt
    return not evaluate_basic_text_area_fit(slot=slot, fill=candidate_fill).is_blocking


def _text_tokens(text: str) -> list[str]:
    return [token for token in _normalize_fit_text(text).split(" ") if token]


def _clean_condensed_text(text: str) -> str:
    return text.strip().rstrip(" -\u2013\u2014").strip()


async def _apply_preflighted_fills(
    editor: SlideEditor,
    slide_xml_path: str,
    preflight: TextFitPreflightResult,
    slots: Sequence[Mapping[str, Any]],
) -> None:
    """layout action 을 먼저 적용한 뒤 fill 과 slot style metadata 를 적용한다."""
    if preflight.layout_actions:
        await asyncio.to_thread(
            editor.apply_layout_actions,
            slide_xml_path,
            preflight.layout_actions,
        )

    warnings = await asyncio.to_thread(
        editor.apply_fills,
        slide_xml_path,
        preflight.fills,
        _slot_metadata_by_shape_id(slots),
    )
    for warning in warnings or []:
        logger.warning("pptx fill warning: %s", warning)


def _log_text_fit_result(
    *,
    job_id: str,
    slide_id: str,
    slide_order: int,
    result: BasicTextFitResult | InlineLabelGroupFitResult,
) -> None:
    """fit 결과와 실패 사유를 structured log 로 남긴다."""
    logger.info(
        "pptx text fit preflight result",
        extra={
            "pptx_text_fit": {
                "job_id": job_id,
                "slide_id": slide_id,
                "slide_order": slide_order,
                **result.to_log_dict(),
            }
        },
    )


def _default_qa_step_factory(
    storage: StorageClient,
    main_client: MainClient,
    editor: SlideEditor,
    toolchain: Toolchain,
    renderer: Renderer,
) -> VisualQAFixVerifyStep:
    return VisualQAFixVerifyStep(
        qa=VisualQA(),
        storage=storage,  # type: ignore[arg-type]
        main_client=main_client,  # type: ignore[arg-type]
        editor=editor,
        toolchain=toolchain,  # type: ignore[arg-type]
        renderer=renderer,  # type: ignore[arg-type]
    )


def _default_renderer_factory() -> PptxRenderer:
    """워커 런타임 lifetime 카운터를 공유하는 렌더러를 만든다."""
    from pptx_worker.runtime import get_worker_runtime

    return PptxRenderer(counter=get_worker_runtime().processed_counter)


async def _close_client(client: MainClient) -> None:
    close = getattr(client, "close", None)
    if close is not None:
        await close()


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _PipelineFatalError(
            f"{key} 값이 비어 있습니다.",
            error_code="PORTFOLIO_CONTEXT_INVALID",
        )
    return value


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 형식이 올바르지 않습니다: {path}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"JSON 최상위 값은 객체여야 합니다: {path}")
    return loaded


def _load_template_metadata(path: Path) -> dict[str, Any]:
    metadata = _load_json_object(path)
    try:
        require_template_v2_metadata(metadata)
    except ValueError as exc:
        raise _PipelineFatalError(
            str(exc),
            error_code="TEMPLATE_METADATA_SCHEMA_INVALID",
        ) from exc
    return metadata


def _load_optional_template_metadata(
    *,
    job_context: Mapping[str, Any],
    storage: StorageClient,
    workdir: Path,
) -> dict[str, Any] | None:
    """재생성 경로에서 가능하면 v2 template metadata 를 확보한다."""
    embedded_metadata = _template_metadata_from_job_context(job_context)
    if embedded_metadata is not None:
        require_template_v2_metadata(embedded_metadata)
        return embedded_metadata

    template_id = _optional_text(job_context, "template_id")
    if template_id is None:
        return None

    template_meta_path = workdir / "meta.json"
    try:
        storage.download_template_meta(template_id, template_meta_path)
        return _load_template_metadata(template_meta_path)
    except Exception:
        logger.warning(
            "template metadata unavailable for regenerate; using raw slots: template_id=%s",
            template_id,
            exc_info=True,
        )
        return None


def _template_metadata_from_job_context(job_context: Mapping[str, Any]) -> dict[str, Any] | None:
    for key in ("template_metadata", "templateMetadata", "template_meta", "templateMeta"):
        value = job_context.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return None


def _enrich_slots_with_template_metadata(
    slots: Sequence[Mapping[str, Any]],
    *,
    template_metadata: Mapping[str, Any] | None,
    slide_filename: str,
) -> list[dict[str, Any]]:
    """OOXML slot 에 v2 metadata slot 필드를 overlay 한다."""
    raw_slots = [dict(slot) for slot in slots]
    if template_metadata is None:
        return raw_slots

    template_slots = _template_slots_by_shape_id(
        template_metadata=template_metadata,
        slide_filename=slide_filename,
    )
    if not template_slots:
        return raw_slots

    enriched_slots: list[dict[str, Any]] = []
    for slot in raw_slots:
        shape_id = str(slot.get("shape_id") or "")
        template_slot = template_slots.get(shape_id)
        if template_slot is None:
            enriched_slots.append(slot)
            continue
        merged = dict(template_slot)
        merged.update(slot)
        enriched_slots.append(merged)
    return enriched_slots


def _template_slots_by_shape_id(
    *,
    template_metadata: Mapping[str, Any],
    slide_filename: str,
) -> dict[str, Mapping[str, Any]]:
    slots = template_metadata.get("slots")
    if not isinstance(slots, Sequence) or isinstance(slots, str | bytes | bytearray):
        return {}

    indexed: dict[str, Mapping[str, Any]] = {}
    for slot in slots:
        if not isinstance(slot, Mapping):
            continue
        if not _metadata_slot_matches_slide(slot, slide_filename):
            continue
        shape_id = str(slot.get("shape_id") or "")
        if not shape_id:
            continue
        indexed.setdefault(shape_id, slot)
    return indexed


def _metadata_slot_matches_slide(slot: Mapping[str, Any], slide_filename: str) -> bool:
    slot_filename = _optional_text(slot, "slide_filename")
    if slot_filename is not None:
        return Path(slot_filename).name == slide_filename

    slide_part = _optional_text(slot, "slide_part")
    if slide_part is not None:
        return Path(slide_part).name == slide_filename

    return True


def _optional_text(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _registered_slides_by_order(
    raw_slides: Sequence[Mapping[str, Any]],
) -> dict[int, RegisteredSlide]:
    registered: dict[int, RegisteredSlide] = {}
    for raw_slide in raw_slides:
        slide_id = raw_slide.get("id")
        slide_order = raw_slide.get("slide_order")
        source_slide_id = raw_slide.get("source_slide_id")
        slide_filename = raw_slide.get("slide_filename")
        if not isinstance(slide_id, str) or not slide_id.strip():
            continue
        if not isinstance(slide_order, int):
            continue
        if not isinstance(source_slide_id, str) or not isinstance(slide_filename, str):
            continue
        registered[slide_order] = RegisteredSlide(
            slide_id=slide_id,
            slide_order=slide_order,
            source_slide_id=source_slide_id,
            slide_filename=slide_filename,
        )
    return registered


def _registered_slide_from_context(
    fallback_slide_id: str,
    slide_context: Mapping[str, Any],
) -> RegisteredSlide:
    slide_id = slide_context.get("id")
    if not isinstance(slide_id, str) or not slide_id.strip():
        slide_id = fallback_slide_id

    slide_order = slide_context.get("slide_order")
    if not isinstance(slide_order, int) or isinstance(slide_order, bool) or slide_order < 1:
        logger.error(
            "invalid regenerate slide_context.slide_order: slide_context=%s",
            dict(slide_context),
        )
        raise ValueError("slide_context.slide_order 는 1 이상의 정수여야 합니다.")

    source_slide_id = slide_context.get("source_slide_id")
    if not isinstance(source_slide_id, str):
        source_slide_id = ""

    slide_filename = slide_context.get("slide_filename")
    if not isinstance(slide_filename, str) or not slide_filename.strip():
        logger.error(
            "invalid regenerate slide_context.slide_filename: slide_context=%s",
            dict(slide_context),
        )
        raise ValueError("slide_context.slide_filename 이 비어 있습니다.")

    return RegisteredSlide(
        slide_id=slide_id,
        slide_order=slide_order,
        source_slide_id=source_slide_id,
        slide_filename=slide_filename,
    )


def _current_fills_from_context(slide_context: Mapping[str, Any]) -> dict[str, Any]:
    current_fills = slide_context.get("current_fills")
    if not isinstance(current_fills, Mapping):
        return {}
    normalized: dict[str, Any] = {}
    for shape_id, fill in current_fills.items():
        normalized[str(shape_id)] = dict(fill) if isinstance(fill, Mapping) else fill
    return _public_current_fills(normalized)


def _slot_metadata_by_shape_id(
    slots: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """v2 slot metadata 를 SlideEditor 에 전달할 shape_id index 로 변환한다."""
    indexed: dict[str, Mapping[str, Any]] = {}
    for slot in slots:
        shape_id = str(slot.get("shape_id") or "")
        if shape_id:
            indexed[shape_id] = slot
    return indexed


def _public_current_fills(fills: Mapping[str, Any]) -> dict[str, Any]:
    """워커 내부 layout action 값을 currentFills callback 계약에서 제거한다."""
    public_fills: dict[str, Any] = {}
    for shape_id, fill in fills.items():
        if str(shape_id) == "layout_actions":
            continue
        if isinstance(fill, Mapping):
            public_fills[str(shape_id)] = {
                str(key): value for key, value in fill.items() if str(key) != "layout_actions"
            }
        else:
            public_fills[str(shape_id)] = fill
    return public_fills


def _content_brief_for_slide(
    job_context: Mapping[str, Any],
    slide: RegisteredSlide,
    *,
    required: bool,
) -> str:
    slide_plan = job_context.get("slide_plan")
    selected_slides = slide_plan.get("selected_slides") if isinstance(slide_plan, Mapping) else None
    if not isinstance(selected_slides, Sequence) or isinstance(selected_slides, (str, bytes)):
        if required:
            raise ValueError("retry 재생성에 사용할 slide_plan.selected_slides 가 없습니다.")
        return ""

    for raw_item in selected_slides:
        if not isinstance(raw_item, Mapping):
            continue
        if _is_matching_planned_slide(raw_item, slide):
            content_brief = raw_item.get("content_brief")
            if isinstance(content_brief, str) and content_brief.strip():
                return content_brief
            if required:
                raise ValueError("retry 재생성에 사용할 content_brief 가 비어 있습니다.")
            return ""

    if required:
        raise ValueError(f"slide_plan 에 slide_order={slide.slide_order} 항목이 없습니다.")
    return ""


def _is_matching_planned_slide(item: Mapping[str, Any], slide: RegisteredSlide) -> bool:
    order = item.get("order", item.get("slide_order"))
    if isinstance(order, int) and order == slide.slide_order:
        return True
    if slide.source_slide_id and item.get("source_slide_id") == slide.source_slide_id:
        return True
    return bool(slide.slide_filename and item.get("slide_filename") == slide.slide_filename)


def _single_ready_outcome(outcomes: Sequence[Any]) -> Any | None:
    for outcome in outcomes:
        if getattr(outcome, "status", None) == "ready":
            return outcome
    return None


_SAFE_ATTEMPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_UNSAFE_ATTEMPT_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def _regeneration_attempt_id(idempotency_key: str) -> str:
    """Cloud Tasks idempotency key 를 GCS-safe attempt id 로 변환한다."""
    stripped = idempotency_key.strip()
    if _SAFE_ATTEMPT_ID_PATTERN.fullmatch(stripped):
        return stripped

    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:12]
    slug = _UNSAFE_ATTEMPT_ID_CHARS.sub("-", stripped).strip("-_")[:40]
    if not slug:
        slug = "attempt"
    return f"{slug}-{digest}"


def _slide_xml_path(unpacked_dir: Path, slide_filename: str) -> Path:
    path = unpacked_dir / "ppt" / "slides" / slide_filename
    if not path.is_file():
        raise FileNotFoundError(f"슬라이드 XML 을 찾을 수 없습니다: {path}")
    return path


def _build_qa_slides(
    ready_content: Sequence[_ContentOutcome],
    rendered_by_page: Mapping[int, Path],
) -> list[SlidePreview]:
    slides: list[SlidePreview] = []
    for outcome in sorted(ready_content, key=lambda item: item.registered_slide.slide_order):
        slide_order = outcome.registered_slide.slide_order
        image_path = rendered_by_page.get(slide_order)
        if image_path is None:
            raise PptxRenderError(f"렌더 결과에 slide_order={slide_order} 이미지가 없습니다.")
        slides.append(
            SlidePreview(
                slide_id=outcome.registered_slide.slide_id,
                slide_order=slide_order,
                slide_filename=outcome.registered_slide.slide_filename,
                image_path=image_path,
                content_brief=outcome.planned_slide.content_brief,
                current_fills=outcome.current_fills or {},
            )
        )
    return slides


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    name = exc.__class__.__name__.lower()
    return "timeout" in name or "timedout" in name


def _is_regenerate_retryable_error(exc: Exception) -> bool:
    if _is_timeout_error(exc):
        return True
    if isinstance(exc, FileNotFoundError):
        return False
    return isinstance(exc, (OSError, PptxRenderError))


def _slide_event_key(job_id: str, slide_id: str, event: str, *parts: str) -> str:
    key = f"{job_id}:slide:{slide_id}:{event}"
    if parts:
        return f"{key}:{':'.join(parts)}"
    return key


def _job_event_key(job_id: str, event: str, stage: str | None = None) -> str:
    if stage:
        return f"{job_id}:job:{event}:{stage}"
    return f"{job_id}:job:{event}"
