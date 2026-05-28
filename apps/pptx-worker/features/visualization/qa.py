"""시각 QA, fix-and-verify, 프리뷰 업로드 단계."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from common.llm.client import get_file_processor_llm
from features.visualization.fills import merge_current_fills
from features.visualization.main_client import VisualizationMainClient
from features.visualization.pptx import (
    PptxRenderer,
    PptxToolchain,
    PptxToolchainError,
    SlideEditor,
)
from features.visualization.storage.gcs_client import GcsClient

logger = logging.getLogger(__name__)

IssueSeverity = Literal["warning", "error"]
OutcomeStatus = Literal["ready", "error"]

__all__ = [
    "FixInstructionBuilder",
    "IssueSeverity",
    "LLMVisualFixer",
    "OutcomeStatus",
    "PreviewMetadata",
    "SlidePreview",
    "SlidePreviewOutcome",
    "VisionLLM",
    "VisualQA",
    "VisualQAFixVerifyStep",
    "VisualQAIssue",
    "VisualQAPipelineResult",
    "VisualQAResult",
    "preview_metadata",
]

_MAX_TEXT_SUMMARY_CHARS = 1200
_QA_SYSTEM_PROMPT = """렌더된 PPT 슬라이드 이미지를 검사하는 시각 QA 담당자입니다.
응답은 반드시 JSON 객체 하나로만 작성하세요.
스키마: {"passed": boolean, "issues": [{"code": string, "message": string, "severity": "warning"|"error", "retryable": boolean}]}
검사 항목:
1. 텍스트 오버플로우, 잘림, 화면 밖 이탈
2. 요소 겹침
3. 디자이너 안내 문구나 미교체 placeholder 잔존
4. 읽기 어려울 정도로 작은 텍스트
5. 전체 레이아웃 균형
"""
_FIX_SYSTEM_PROMPT = """PPTX OOXML 슬라이드의 자동 품질 보정 담당자입니다.
응답은 반드시 JSON 객체 하나로만 작성하세요.
스키마: {"fills": {"shape_id": {"action": "text", "text": string, "font_size_override": number|null, "is_title": boolean|null}}}
지침:
- 이슈 해결에 필요한 shape_id 만 수정하세요.
- 숫자, 고유명사, 기술 스택, 성과 지표는 삭제하거나 변경하지 마세요.
- 텍스트 요약이 불가피하면 의미를 보존하고 핵심 수치와 이름을 유지하세요.
- 폰트 크기는 10pt 이상 48pt 이하로만 조정하세요.
- 슬라이드 추가/삭제는 하지 마세요.
"""


class VisionLLM(Protocol):
    """LangChain compatible vision LLM interface."""

    def invoke(self, messages: list[object]) -> object:
        """메시지를 처리하고 응답 객체를 반환한다."""


class FixInstructionBuilder(Protocol):
    """시각 QA 이슈를 SlideEditor fill 명령으로 변환한다."""

    def build_fills(
        self,
        slide: SlidePreview,
        qa_result: VisualQAResult,
        *,
        slide_xml_path: Path,
        attempt: int,
    ) -> dict[str, dict[str, Any]]:
        """수정 대상 shape_id -> fill 맵을 반환한다."""


@dataclass(frozen=True)
class VisualQAIssue:
    """단일 시각 QA 이슈."""

    code: str
    message: str
    severity: IssueSeverity = "error"
    retryable: bool = True


@dataclass(frozen=True)
class VisualQAResult:
    """단일 슬라이드 시각 QA 결과."""

    passed: bool
    issues: tuple[VisualQAIssue, ...] = ()
    raw_response: str = ""


@dataclass(frozen=True)
class SlidePreview:
    """QA 단계가 처리할 렌더된 슬라이드 입력."""

    slide_id: str
    slide_order: int
    slide_filename: str
    image_path: Path
    content_brief: str = ""
    current_fills: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreviewMetadata:
    """업로드된 프리뷰 이미지 메타데이터."""

    width: int
    height: int
    byte_size: int


@dataclass(frozen=True)
class SlidePreviewOutcome:
    """슬라이드별 QA 단계 최종 결과."""

    slide_id: str
    slide_order: int
    status: OutcomeStatus
    qa_attempts: int
    gcs_preview_key: str | None = None
    current_fills: Mapping[str, Any] = field(default_factory=dict)
    issues: tuple[VisualQAIssue, ...] = ()


@dataclass(frozen=True)
class VisualQAPipelineResult:
    """QA 단계 전체 결과."""

    outcomes: tuple[SlidePreviewOutcome, ...]
    qa_checked_slide_orders: tuple[int, ...]
    fix_attempts: int
    pack_count: int
    render_count: int

    @property
    def qa_performed(self) -> bool:
        """완료 선언 전 시각 QA가 최소 1회 수행됐는지 반환한다."""
        return bool(self.qa_checked_slide_orders)

    def ensure_visual_qa_performed(self) -> None:
        """시각 QA 없이 완료를 선언하려는 흐름을 차단한다."""
        if not self.qa_performed:
            raise RuntimeError("완료 선언 전 최소 1회 시각 QA가 필요합니다.")


@dataclass
class _PendingSlide:
    slide: SlidePreview
    image_path: Path
    qa_attempts: int = 0
    last_result: VisualQAResult | None = None


@dataclass(frozen=True)
class _SlideCheckResult:
    """비동기 QA 호출 하나의 결과."""

    slide_order: int
    qa_result: VisualQAResult
    had_exception: bool = False


class VisualQA:
    """렌더된 슬라이드 이미지를 비전 LLM으로 검사한다."""

    def __init__(self, llm: VisionLLM | None = None) -> None:
        self._llm = llm

    def check_slide(
        self,
        slide_image_path: str | Path,
        expected_content: Mapping[str, Any] | None = None,
    ) -> VisualQAResult:
        """프리뷰 이미지를 검사하고 통과 여부와 이슈 목록을 반환한다."""
        image_path = Path(slide_image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"슬라이드 프리뷰 이미지를 찾을 수 없습니다: {image_path}")

        llm = self._llm or get_file_processor_llm()
        messages = [
            SystemMessage(content=_QA_SYSTEM_PROMPT),
            HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": _build_qa_user_prompt(expected_content or {}),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(image_path)},
                    },
                ]
            ),
        ]
        response = llm.invoke(messages)
        response_text = _normalize_response_text(getattr(response, "content", response))
        return _parse_qa_response(response_text)


class LLMVisualFixer:
    """LLM 지시를 SlideEditor fill 맵으로 변환하는 기본 fix 빌더."""

    def __init__(
        self,
        *,
        llm: VisionLLM | None = None,
        editor: SlideEditor | None = None,
    ) -> None:
        self._llm = llm
        self._editor = editor or SlideEditor()

    def build_fills(
        self,
        slide: SlidePreview,
        qa_result: VisualQAResult,
        *,
        slide_xml_path: Path,
        attempt: int,
    ) -> dict[str, dict[str, Any]]:
        """이슈와 현재 XML slot 정보를 기반으로 보정 fill 맵을 만든다."""
        llm = self._llm or get_file_processor_llm()
        slots = self._editor.extract_slots(str(slide_xml_path))
        messages = [
            SystemMessage(content=_FIX_SYSTEM_PROMPT),
            HumanMessage(
                content=_build_fix_user_prompt(
                    slide=slide,
                    issues=qa_result.issues,
                    slots=slots,
                    attempt=attempt,
                )
            ),
        ]
        response = llm.invoke(messages)
        response_text = _normalize_response_text(getattr(response, "content", response))
        return _parse_fix_response(response_text)


class VisualQAFixVerifyStep:
    """
    Phase 1 Step 6: 시각 QA, 자동 수정 검증, 프리뷰 업로드, 슬라이드 콜백.

    통과 슬라이드는 즉시 업로드하고, 이슈 슬라이드만 제한된 fix-and-verify 루프에서
    재패키징/재렌더/재검사한다.
    """

    def __init__(
        self,
        *,
        qa: VisualQA,
        storage: GcsClient,
        main_client: VisualizationMainClient,
        editor: SlideEditor,
        toolchain: PptxToolchain,
        renderer: PptxRenderer,
        fixer: FixInstructionBuilder | None = None,
        max_fix_attempts: int = 2,
        max_qa_concurrency: int = 4,
        clock: Any | None = None,
    ) -> None:
        if max_fix_attempts < 0:
            raise ValueError("max_fix_attempts는 0 이상이어야 합니다.")
        if max_qa_concurrency <= 0:
            raise ValueError("max_qa_concurrency는 1 이상이어야 합니다.")
        self._qa = qa
        self._storage = storage
        self._main_client = main_client
        self._editor = editor
        self._toolchain = toolchain
        self._renderer = renderer
        self._fixer = fixer or LLMVisualFixer(editor=editor)
        self._max_fix_attempts = max_fix_attempts
        self._max_qa_concurrency = max_qa_concurrency
        self._clock = clock or (lambda: datetime.now(UTC))

    async def process(
        self,
        *,
        job_id: str,
        slides: list[SlidePreview],
        unpacked_dir: str | Path,
        working_pptx_path: str | Path,
        fixed_pptx_path: str | Path,
        render_output_dir: str | Path,
        ready_event: str | None = "slide_preview_ready",
    ) -> VisualQAPipelineResult:
        """렌더된 슬라이드들을 QA 처리하고 슬라이드별 ready/error 콜백을 보낸다."""
        if not slides:
            raise ValueError("시각 QA 대상 슬라이드가 없습니다.")

        unpacked_root = Path(unpacked_dir)
        working_pptx = Path(working_pptx_path)
        fixed_pptx = Path(fixed_pptx_path)
        render_dir = Path(render_output_dir)
        pending = {
            slide.slide_order: _PendingSlide(slide=slide, image_path=slide.image_path)
            for slide in sorted(slides, key=lambda item: item.slide_order)
        }
        outcomes: dict[int, SlidePreviewOutcome] = {}
        checked_orders: list[int] = []
        pack_count = 0
        render_count = 0

        failed_orders = await self._check_and_publish_passes(
            job_id=job_id,
            pending=pending,
            candidate_orders=tuple(pending),
            outcomes=outcomes,
            checked_orders=checked_orders,
            ready_event=ready_event,
        )

        fix_attempts = 0
        while failed_orders and fix_attempts < self._max_fix_attempts:
            fix_attempts += 1
            affected_orders = tuple(failed_orders)
            fixed_orders = await self._apply_fixes(
                pending=pending,
                affected_orders=affected_orders,
                unpacked_root=unpacked_root,
                attempt=fix_attempts,
            )
            unfixed_orders = [
                slide_order for slide_order in affected_orders if slide_order not in fixed_orders
            ]
            recheck_failed_orders: list[int] = []

            if fixed_orders:
                try:
                    self._pack_and_validate_fixed_pptx(
                        unpacked_root=unpacked_root,
                        fixed_pptx=fixed_pptx,
                        working_pptx=working_pptx,
                        attempt=fix_attempts,
                    )
                except Exception as exc:
                    logger.warning(
                        "visual QA fixed PPTX validation failed: orders=%s attempt=%s error=%s",
                        fixed_orders,
                        fix_attempts,
                        exc,
                    )
                    self._mark_orders_failed(
                        pending,
                        fixed_orders,
                        code="pptx_validation_failed",
                        message=f"자동 수정 산출물 검증에 실패했습니다: {exc}",
                        retryable=True,
                    )
                    failed_orders = [*unfixed_orders, *fixed_orders]
                    continue

                pack_count += 1
                try:
                    render_page = fixed_orders[0] if len(fixed_orders) == 1 else None
                    render_result = self._renderer.render(fixed_pptx, render_dir, page=render_page)
                    render_count += 1
                except Exception as exc:
                    logger.warning(
                        "visual QA fixed PPTX render failed: orders=%s attempt=%s error=%s",
                        fixed_orders,
                        fix_attempts,
                        exc,
                    )
                    self._mark_orders_failed(
                        pending,
                        fixed_orders,
                        code="pptx_render_failed",
                        message=f"자동 수정 산출물 렌더링에 실패했습니다: {exc}",
                        retryable=True,
                    )
                    failed_orders = [*unfixed_orders, *fixed_orders]
                    continue

                rendered_by_page = {
                    rendered.page: rendered.image_path for rendered in render_result.slides
                }
                rendered_orders: list[int] = []
                missing_orders: list[int] = []
                for slide_order in fixed_orders:
                    rendered_image = rendered_by_page.get(slide_order)
                    if rendered_image is None:
                        missing_orders.append(slide_order)
                        continue
                    pending[slide_order].image_path = rendered_image
                    rendered_orders.append(slide_order)

                if missing_orders:
                    self._mark_orders_failed(
                        pending,
                        tuple(missing_orders),
                        code="pptx_render_missing_slide",
                        message="재렌더 결과에 슬라이드 이미지가 없습니다.",
                        retryable=True,
                    )

                if rendered_orders:
                    recheck_failed_orders = await self._check_and_publish_passes(
                        job_id=job_id,
                        pending=pending,
                        candidate_orders=tuple(rendered_orders),
                        outcomes=outcomes,
                        checked_orders=checked_orders,
                        ready_event=ready_event,
                    )
                else:
                    recheck_failed_orders = []
                recheck_failed_orders = [*missing_orders, *recheck_failed_orders]

            failed_orders = [*unfixed_orders, *recheck_failed_orders]

        for slide_order in failed_orders:
            pending_slide = pending[slide_order]
            qa_result = pending_slide.last_result or VisualQAResult(
                passed=False,
                issues=(VisualQAIssue(code="visual_qa_failed", message="시각 QA에 실패했습니다."),),
            )
            message = _format_issue_message(qa_result.issues)
            await self._send_preview_error(
                job_id,
                pending_slide.slide,
                message=message,
                retryable=_issues_retryable(qa_result.issues),
            )
            outcomes[slide_order] = SlidePreviewOutcome(
                slide_id=pending_slide.slide.slide_id,
                slide_order=slide_order,
                status="error",
                qa_attempts=pending_slide.qa_attempts,
                issues=qa_result.issues,
            )

        result = VisualQAPipelineResult(
            outcomes=tuple(outcomes[key] for key in sorted(outcomes)),
            qa_checked_slide_orders=tuple(checked_orders),
            fix_attempts=fix_attempts,
            pack_count=pack_count,
            render_count=render_count,
        )
        result.ensure_visual_qa_performed()
        return result

    async def _check_and_publish_passes(
        self,
        *,
        job_id: str,
        pending: dict[int, _PendingSlide],
        candidate_orders: tuple[int, ...],
        outcomes: dict[int, SlidePreviewOutcome],
        checked_orders: list[int],
        ready_event: str | None,
    ) -> list[int]:
        check_orders = tuple(
            slide_order for slide_order in candidate_orders if slide_order not in outcomes
        )
        if not check_orders:
            return []

        failed_order_set: set[int] = set()
        semaphore = asyncio.Semaphore(self._max_qa_concurrency)
        tasks = [
            asyncio.create_task(
                self._check_one_slide(
                    pending_slide=pending[slide_order],
                    semaphore=semaphore,
                )
            )
            for slide_order in check_orders
        ]
        try:
            for future in asyncio.as_completed(tasks):
                check_result = await future
                slide_order = check_result.slide_order
                pending_slide = pending[slide_order]
                qa_result = check_result.qa_result

                pending_slide.qa_attempts += 1
                pending_slide.last_result = qa_result
                checked_orders.append(slide_order)

                if check_result.had_exception:
                    await self._send_preview_error(
                        job_id,
                        pending_slide.slide,
                        message=_format_issue_message(qa_result.issues),
                        retryable=_issues_retryable(qa_result.issues),
                    )
                    outcomes[slide_order] = self._error_outcome(pending_slide, qa_result.issues)
                    continue

                if qa_result.passed:
                    outcomes[slide_order] = await self._publish_ready_or_error(
                        job_id,
                        pending_slide,
                        ready_event=ready_event,
                    )
                    continue

                failed_order_set.add(slide_order)
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        return [slide_order for slide_order in check_orders if slide_order in failed_order_set]

    async def _check_one_slide(
        self,
        *,
        pending_slide: _PendingSlide,
        semaphore: asyncio.Semaphore,
    ) -> _SlideCheckResult:
        slide = pending_slide.slide
        async with semaphore:
            try:
                qa_result = await asyncio.to_thread(
                    self._qa.check_slide,
                    pending_slide.image_path,
                    _expected_content(slide),
                )
            except Exception as exc:
                logger.warning(
                    "visual QA check failed: slide_order=%s error=%s",
                    slide.slide_order,
                    exc,
                )
                return _SlideCheckResult(
                    slide_order=slide.slide_order,
                    qa_result=VisualQAResult(
                        passed=False,
                        issues=(
                            VisualQAIssue(
                                code="visual_qa_exception",
                                message=f"시각 QA 호출에 실패했습니다: {exc}",
                                retryable=True,
                            ),
                        ),
                    ),
                    had_exception=True,
                )
        return _SlideCheckResult(slide_order=slide.slide_order, qa_result=qa_result)

    async def _publish_ready_or_error(
        self,
        job_id: str,
        pending_slide: _PendingSlide,
        *,
        ready_event: str | None,
    ) -> SlidePreviewOutcome:
        try:
            gcs_key = await self._upload_and_send_ready(
                job_id,
                pending_slide,
                ready_event=ready_event,
            )
        except Exception as exc:
            logger.warning(
                "visual QA preview publish failed: slide_order=%s error=%s",
                pending_slide.slide.slide_order,
                exc,
            )
            issue = VisualQAIssue(
                code="preview_publish_failed",
                message=f"프리뷰 업로드 또는 콜백에 실패했습니다: {exc}",
                retryable=True,
            )
            pending_slide.last_result = VisualQAResult(passed=False, issues=(issue,))
            await self._send_preview_error(
                job_id,
                pending_slide.slide,
                message=_format_issue_message((issue,)),
                retryable=issue.retryable,
            )
            return self._error_outcome(pending_slide, (issue,))

        return SlidePreviewOutcome(
            slide_id=pending_slide.slide.slide_id,
            slide_order=pending_slide.slide.slide_order,
            status="ready",
            qa_attempts=pending_slide.qa_attempts,
            gcs_preview_key=gcs_key,
            current_fills=pending_slide.slide.current_fills,
        )

    async def _apply_fixes(
        self,
        *,
        pending: dict[int, _PendingSlide],
        affected_orders: tuple[int, ...],
        unpacked_root: Path,
        attempt: int,
    ) -> tuple[int, ...]:
        fixed_orders: list[int] = []
        for slide_order in affected_orders:
            pending_slide = pending[slide_order]
            qa_result = pending_slide.last_result
            if qa_result is None:
                raise RuntimeError(f"slide_order={slide_order} 의 QA 결과가 없습니다.")
            slide_xml_path = _slide_xml_path(unpacked_root, pending_slide.slide.slide_filename)
            try:
                fills = await asyncio.to_thread(
                    self._fixer.build_fills,
                    pending_slide.slide,
                    qa_result,
                    slide_xml_path=slide_xml_path,
                    attempt=attempt,
                )
                if not fills:
                    raise ValueError("자동 수정 fill 이 비어 있습니다.")
                await asyncio.to_thread(self._editor.apply_fills, str(slide_xml_path), fills)
                pending_slide.slide = replace(
                    pending_slide.slide,
                    current_fills=merge_current_fills(
                        pending_slide.slide.current_fills,
                        fills,
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "visual QA fix failed: slide_order=%s attempt=%s error=%s",
                    slide_order,
                    attempt,
                    exc,
                )
                pending_slide.last_result = VisualQAResult(
                    passed=False,
                    issues=(
                        VisualQAIssue(
                            code="fix_failed",
                            message=f"자동 수정에 실패했습니다: {exc}",
                            retryable=True,
                        ),
                    ),
                )
                continue
            fixed_orders.append(slide_order)
        return tuple(fixed_orders)

    def _pack_and_validate_fixed_pptx(
        self,
        *,
        unpacked_root: Path,
        fixed_pptx: Path,
        working_pptx: Path,
        attempt: int,
    ) -> None:
        candidate_pptx = _candidate_fixed_pptx_path(fixed_pptx, attempt)
        candidate_pptx.parent.mkdir(parents=True, exist_ok=True)
        candidate_pptx.unlink(missing_ok=True)

        self._toolchain.pack(unpacked_root, candidate_pptx, original_pptx=working_pptx)
        validation = self._toolchain.validate(unpacked_root, original_pptx=working_pptx)
        if not validation.success:
            repair_result = self._toolchain.repair(unpacked_root, original_pptx=working_pptx)
            if not repair_result.success:
                candidate_pptx.unlink(missing_ok=True)
                raise PptxToolchainError(
                    _validation_error_message("PPTX auto-repair 실패", repair_result)
                )
            self._toolchain.pack(unpacked_root, candidate_pptx, original_pptx=working_pptx)
            validation = self._toolchain.validate(unpacked_root, original_pptx=working_pptx)
            if not validation.success:
                candidate_pptx.unlink(missing_ok=True)
                raise PptxToolchainError(
                    _validation_error_message("PPTX 검증이 repair 이후에도 실패", validation)
                )

        candidate_pptx.replace(fixed_pptx)

    def _mark_orders_failed(
        self,
        pending: dict[int, _PendingSlide],
        slide_orders: tuple[int, ...],
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        issue = VisualQAIssue(code=code, message=message, retryable=retryable)
        for slide_order in slide_orders:
            pending[slide_order].last_result = VisualQAResult(passed=False, issues=(issue,))

    def _error_outcome(
        self,
        pending_slide: _PendingSlide,
        issues: tuple[VisualQAIssue, ...],
    ) -> SlidePreviewOutcome:
        return SlidePreviewOutcome(
            slide_id=pending_slide.slide.slide_id,
            slide_order=pending_slide.slide.slide_order,
            status="error",
            qa_attempts=pending_slide.qa_attempts,
            issues=issues,
        )

    async def _upload_and_send_ready(
        self,
        job_id: str,
        pending_slide: _PendingSlide,
        *,
        ready_event: str | None,
    ) -> str:
        slide = pending_slide.slide
        metadata = preview_metadata(pending_slide.image_path)
        gcs_key = self._storage.upload_preview(job_id, slide.slide_order, pending_slide.image_path)
        if ready_event is None:
            return gcs_key

        await self._main_client.send_slide_event(
            job_id,
            slide.slide_id,
            event=ready_event,
            slide_order=slide.slide_order,
            idempotency_key=_idempotency_key(job_id, slide, ready_event),
            occurred_at=self._now_iso(),
            gcs_preview_key=gcs_key,
            current_fills=dict(slide.current_fills),
            preview_width=metadata.width,
            preview_height=metadata.height,
            preview_byte_size=metadata.byte_size,
        )
        return gcs_key

    async def _send_preview_error(
        self,
        job_id: str,
        slide: SlidePreview,
        *,
        message: str,
        retryable: bool,
    ) -> None:
        await self._main_client.send_slide_event(
            job_id,
            slide.slide_id,
            event="slide_preview_error",
            slide_order=slide.slide_order,
            idempotency_key=_idempotency_key(job_id, slide, "slide_preview_error"),
            occurred_at=self._now_iso(),
            message=message,
            retryable=retryable,
        )

    def _now_iso(self) -> str:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def preview_metadata(image_path: str | Path) -> PreviewMetadata:
    """프리뷰 이미지의 width/height/byteSize 를 읽는다."""
    path = Path(image_path)
    data = path.read_bytes()
    width, height = _image_dimensions(data)
    return PreviewMetadata(width=width, height=height, byte_size=len(data))


def _build_qa_user_prompt(expected_content: Mapping[str, Any]) -> str:
    brief = expected_content.get("brief") or expected_content.get("content_brief") or "N/A"
    texts_summary = expected_content.get("texts_summary") or "N/A"
    return (
        "이 PPT 슬라이드 이미지를 검사하세요.\n\n"
        "예상 내용:\n"
        f"- 슬라이드 요지: {brief}\n"
        f"- 채워진 주요 텍스트: {texts_summary}\n\n"
        "통과면 passed=true 와 빈 issues 를 반환하세요. "
        "문제가 있으면 자동 수정 가능한 원인을 code/message 로 구체화하세요."
    )


def _build_fix_user_prompt(
    *,
    slide: SlidePreview,
    issues: tuple[VisualQAIssue, ...],
    slots: list[dict[str, Any]],
    attempt: int,
) -> str:
    payload = {
        "slide_order": slide.slide_order,
        "content_brief": slide.content_brief,
        "current_fills": slide.current_fills,
        "issues": [
            {
                "code": issue.code,
                "message": issue.message,
                "severity": issue.severity,
                "retryable": issue.retryable,
            }
            for issue in issues
        ],
        "slots": slots,
        "attempt": attempt,
    }
    return (
        "다음 QA 이슈를 해결하는 최소 fill 변경을 산출하세요.\n"
        "숫자, 고유명사, 기술 스택, 성과 지표는 반드시 보존하세요.\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


def _expected_content(slide: SlidePreview) -> dict[str, str]:
    return {
        "brief": slide.content_brief,
        "texts_summary": _summarize_fills(slide.current_fills),
    }


def _summarize_fills(fills: Mapping[str, Any]) -> str:
    texts: list[str] = []
    for shape_id, fill in fills.items():
        if not isinstance(fill, Mapping):
            continue
        text = fill.get("text")
        if text is None:
            continue
        texts.append(f"{shape_id}: {text}")
    summary = "\n".join(texts)
    if len(summary) <= _MAX_TEXT_SUMMARY_CHARS:
        return summary
    return f"{summary[:_MAX_TEXT_SUMMARY_CHARS].rstrip()}\n...(요약 길이 제한)"


def _image_data_url(image_path: Path) -> str:
    content_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{content_type};base64,{encoded}"


def _normalize_response_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                texts.append(item.strip())
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n".join(texts).strip()
    return str(content).strip()


def _parse_qa_response(response_text: str) -> VisualQAResult:
    try:
        payload = _loads_json_object(response_text)
    except ValueError as exc:
        return VisualQAResult(
            passed=False,
            issues=(
                VisualQAIssue(
                    code="qa_parse_error",
                    message=f"시각 QA 응답을 해석할 수 없습니다: {exc}",
                ),
            ),
            raw_response=response_text,
        )

    issues = tuple(_parse_issue(item) for item in payload.get("issues") or [])
    passed = bool(payload.get("passed")) and not issues
    return VisualQAResult(passed=passed, issues=issues, raw_response=response_text)


def _parse_issue(item: object) -> VisualQAIssue:
    if isinstance(item, str):
        return VisualQAIssue(code="visual_issue", message=item)
    if not isinstance(item, Mapping):
        return VisualQAIssue(code="visual_issue", message=str(item))
    severity = item.get("severity")
    if severity not in {"warning", "error"}:
        severity = "error"
    return VisualQAIssue(
        code=str(item.get("code") or "visual_issue"),
        message=str(item.get("message") or item.get("detail") or "시각 QA 이슈가 발견되었습니다."),
        severity=severity,
        retryable=bool(item.get("retryable", True)),
    )


def _parse_fix_response(response_text: str) -> dict[str, dict[str, Any]]:
    payload = _loads_json_object(response_text)
    fills = payload.get("fills")
    if not isinstance(fills, Mapping):
        raise ValueError("자동 수정 응답에는 fills 객체가 필요합니다.")
    normalized: dict[str, dict[str, Any]] = {}
    for shape_id, fill in fills.items():
        if not isinstance(fill, Mapping):
            continue
        normalized[str(shape_id)] = dict(fill)
    return normalized


def _loads_json_object(response_text: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(response_text)
    decoder = json.JSONDecoder()
    starts = [index for index, char in enumerate(cleaned) if char == "{"]
    if cleaned.startswith("{") and 0 not in starts:
        starts.insert(0, 0)

    for start in starts:
        try:
            payload, _ = decoder.raw_decode(cleaned, idx=start)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    raise ValueError("JSON 객체를 찾을 수 없습니다.")


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _format_issue_message(issues: tuple[VisualQAIssue, ...]) -> str:
    if not issues:
        return "시각 QA를 통과하지 못했습니다."
    return "; ".join(f"{issue.code}: {issue.message}" for issue in issues)


def _issues_retryable(issues: tuple[VisualQAIssue, ...]) -> bool:
    if not issues:
        return True
    return any(issue.retryable for issue in issues)


def _slide_xml_path(unpacked_root: Path, slide_filename: str) -> Path:
    path = unpacked_root / "ppt" / "slides" / slide_filename
    if not path.is_file():
        raise FileNotFoundError(f"슬라이드 XML을 찾을 수 없습니다: {path}")
    return path


def _candidate_fixed_pptx_path(fixed_pptx: Path, attempt: int) -> Path:
    return fixed_pptx.with_name(f"{fixed_pptx.stem}.attempt-{attempt}{fixed_pptx.suffix}")


def _validation_error_message(reason: str, validation: object) -> str:
    stdout = getattr(validation, "stdout", "")
    stderr = getattr(validation, "stderr", "")
    return f"{reason}.\nstdout:\n{stdout}\nstderr:\n{stderr}"


def _idempotency_key(job_id: str, slide: SlidePreview, event: str) -> str:
    return f"{job_id}:{slide.slide_id}:{slide.slide_order}:{event}"


def _image_dimensions(data: bytes) -> tuple[int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(data)
    raise ValueError("지원하지 않는 프리뷰 이미지 형식입니다.")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    index = 2
    while index < len(data):
        while index < len(data) and data[index] != 0xFF:
            index += 1
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break

        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_length < 7:
                break
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    raise ValueError("JPEG 프리뷰 이미지 크기를 읽을 수 없습니다.")
