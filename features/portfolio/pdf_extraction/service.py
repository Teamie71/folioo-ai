"""PDF 추출 서비스"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import TYPE_CHECKING, Protocol

from fastapi import BackgroundTasks

from common.clients.correction_client import (
    CorrectionClient,
    build_pdf_activity_payload,
    get_correction_client,
)
from common.sse import SSEErrorCode, SSEEventType
from common.utils.text import is_within_char_limit, truncate_to_char_limit

from .config import get_pdf_extraction_limits
from .schemas import PdfActivity, PdfExtractionResult, PdfProblemSolvingItem

if TYPE_CHECKING:
    from .generator import PdfExtractionGenerator

logger = logging.getLogger(__name__)

_service: "PdfExtractionService | None" = None
_MAX_PDF_FILE_SIZE_BYTES = 10 * 1024 * 1024
_PDF_MIME_TYPE = "application/pdf"
# 카테고리 글자수는 불릿을 개행으로 이은 문자열 기준이라 불릿 사이마다 구분자 1자를 센다.
_CATEGORY_LINE_SEPARATOR_LENGTH = 1
# 문제해결만 메인 서버가 라벨을 붙여 저장한다. 화면 글자수 카운터는 저장된 문자열을 세므로
# AI 쪽 예산도 같은 형식으로 계산해야 한다.
#   "#{no}\n상황: {situation}\n전략: {strategy}\n이유: {reason}" 을 "\n\n" 으로 이음
#   (folioo-server: internal-correction-result.facade.ts `mapActivity`)
# 메인 서버가 이 렌더링 형식을 바꾸면 아래 두 상수도 함께 고쳐야 한다.
_PROBLEM_SOLVING_ITEM_OVERHEAD = 17
_PROBLEM_SOLVING_ITEM_SEPARATOR_LENGTH = 2


class PdfExtractionGeneratorProtocol(Protocol):
    """PDF 추출 generator 프로토콜"""

    def extract(self, file_bytes: bytes, filename: str) -> PdfExtractionResult:
        """PDF 파일을 구조화된 활동 목록으로 추출한다."""

    def extract_stream(self, file_bytes: bytes, filename: str) -> AsyncIterator[PdfActivity]:
        """PDF 파일을 활동 단위로 흘려보낸다."""


def _sse_event(event_type: SSEEventType, payload: dict) -> dict:
    """SSE 라우터가 기대하는 이벤트 dict 를 만든다."""
    return {
        "event": event_type,
        "data": json.dumps({"type": event_type, **payload}, ensure_ascii=False),
    }


class PdfExtractionService:
    """PDF 추출 요청과 콜백을 오케스트레이션한다."""

    def __init__(
        self,
        correction_client: CorrectionClient,
        generator: PdfExtractionGeneratorProtocol,
    ) -> None:
        self._correction_client = correction_client
        self._generator = generator

    async def start_extraction(
        self,
        correction_id: int,
        file_bytes: bytes,
        filename: str,
        content_type: str | None,
        background_tasks: BackgroundTasks,
    ) -> None:
        """PDF 추출 요청을 검증하고 background task를 등록한다."""
        self._validate_file(file_bytes=file_bytes, filename=filename, content_type=content_type)
        background_tasks.add_task(self._extract_background, correction_id, file_bytes, filename)

    @staticmethod
    def _validate_file(file_bytes: bytes, filename: str, content_type: str | None) -> None:
        """업로드 파일이 처리 가능한 PDF인지 검증한다."""
        if not file_bytes:
            raise ValueError("빈 PDF 파일은 업로드할 수 없습니다.")

        if len(file_bytes) > _MAX_PDF_FILE_SIZE_BYTES:
            raise ValueError("PDF 파일 크기는 10MB를 초과할 수 없습니다.")

        normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
        if normalized_content_type == _PDF_MIME_TYPE:
            return

        if not filename.lower().endswith(".pdf"):
            raise ValueError("PDF 파일만 업로드할 수 있습니다.")

    async def _extract_background(
        self,
        correction_id: int,
        file_bytes: bytes,
        filename: str,
    ) -> None:
        """Background Task: PDF 추출 후 메인 서버에 완료/실패 콜백을 전송한다."""
        try:
            result = await asyncio.to_thread(self._generator.extract, file_bytes, filename)
            activities = self._validate_result(result)
        except Exception as exc:
            logger.exception("PDF 추출 실패 (correction_id: %s): %s", correction_id, exc)
            await self._send_failure_callback(correction_id, str(exc))
            return

        await self._send_completion_callback(correction_id, activities)

    async def stream_extraction(
        self,
        correction_id: int,
        file_bytes: bytes,
        filename: str,
        content_type: str | None,
    ) -> AsyncIterator[dict]:
        """PDF 추출을 활동 단위로 스트리밍하고, 끝나면 기존 배치 콜백을 전송한다.

        저장 경로는 바꾸지 않는다. 메인 서버의 PDF 추출 결과 수신은 전부-아니면-전무
        구조라 활동 단위 저장 경로가 없다. 그래서 스트림은 화면 표시용으로만 쓰고,
        영속화는 스트림이 끝난 뒤 기존 `complete_pdf_extraction` 콜백 1회로 처리한다.

        프론트가 중간에 연결을 끊어도 저장이 완료되도록, 콜백 전송은 스트림 소비와
        분리해 태스크로 띄운다.

        Args:
            correction_id: 첨삭 ID
            file_bytes: PDF 파일 바이트
            filename: 원본 파일명
            content_type: 업로드 content-type

        Yields:
            dict: `{"event": ..., "data": ...}` 형태의 SSE 이벤트
        """
        try:
            self._validate_file(file_bytes=file_bytes, filename=filename, content_type=content_type)
        except ValueError as exc:
            await self._send_failure_callback(correction_id, str(exc))
            yield _sse_event(
                SSEEventType.EXTRACTION_FAILED,
                {"error": {"code": SSEErrorCode.EXTRACTION_INVALID_FILE, "message": str(exc)}},
            )
            return

        limits = get_pdf_extraction_limits()
        seen_names: set[str] = set()
        activities: list[PdfActivity] = []

        yield _sse_event(SSEEventType.EXTRACTION_STARTED, {})

        try:
            # aclosing: 상한 도달로 break 하거나 예외로 빠져나가도 하위 LLM 스트림
            # (extract_stream 이 감싸는 llm.astream)이 확실히 닫히도록 한다. async for
            # 를 break 하는 것만으로는 비동기 제너레이터가 즉시 닫히지 않는다.
            async with aclosing(self._generator.extract_stream(file_bytes, filename)) as stream:
                async for raw_activity in stream:
                    normalized = self._normalize_activity(raw_activity, seen_names)
                    if normalized is None:
                        continue

                    activities.append(normalized)
                    formatted = self._format_activities_for_callback([normalized])[0]
                    yield _sse_event(
                        SSEEventType.ACTIVITY_COMPLETED,
                        {
                            "index": len(activities) - 1,
                            "activity": build_pdf_activity_payload(formatted),
                        },
                    )

                    if len(activities) >= limits.max_activity_count:
                        break
        except Exception as exc:
            logger.exception("PDF 추출 스트리밍 실패 (correction_id: %s): %s", correction_id, exc)
            await self._send_failure_callback(correction_id, str(exc))
            yield _sse_event(
                SSEEventType.EXTRACTION_FAILED,
                {"error": {"code": SSEErrorCode.EXTRACTION_FAILED, "message": str(exc)}},
            )
            return

        if not activities:
            message = "PDF에서 추출된 활동이 없습니다."
            logger.error("PDF 추출 스트리밍 결과 없음 (correction_id: %s)", correction_id)
            await self._send_failure_callback(correction_id, message)
            yield _sse_event(
                SSEEventType.EXTRACTION_FAILED,
                {"error": {"code": SSEErrorCode.EXTRACTION_FAILED, "message": message}},
            )
            return

        completion_task = asyncio.create_task(
            self._send_completion_callback(correction_id, activities)
        )
        # asyncio.shield: 소비 측(클라이언트 연결 끊김 등)에서 이 지점의 await가
        # 취소돼도 completion_task 자체는 취소되지 않고 백그라운드에서 계속 실행된다.
        # (common/sse/ping.py 의 finally 블록이 next_event_task 를 취소하면 그 취소가
        # 이 await 체인까지 전파되는데, shield 없이는 completion_task 까지 함께
        # 취소되어 저장이 중간에 끊긴다.)
        completed = await asyncio.shield(completion_task)

        if not completed:
            message = "PDF 추출 결과 저장에 실패했습니다."
            await self._send_failure_callback(correction_id, message)
            yield _sse_event(
                SSEEventType.EXTRACTION_FAILED,
                {"error": {"code": SSEErrorCode.EXTRACTION_FAILED, "message": message}},
            )
            return

        yield _sse_event(
            SSEEventType.EXTRACTION_COMPLETED,
            {"activityCount": len(activities)},
        )

    async def _send_completion_callback(
        self,
        correction_id: int,
        activities: list[PdfActivity],
    ) -> bool:
        """추출 완료 콜백을 전송한다. 실패는 로깅하고 성공 여부를 반환한다."""
        try:
            await self._correction_client.complete_pdf_extraction(
                correction_id,
                activities=[
                    activity.model_dump()
                    for activity in self._format_activities_for_callback(activities)
                ],
                source_type="EXTERNAL",
            )
            return True
        except Exception:
            logger.exception("PDF 추출 완료 콜백 전송 실패 (correction_id: %s)", correction_id)
            return False

    async def _send_failure_callback(self, correction_id: int, error_message: str) -> None:
        """추출 실패 콜백을 전송하고 실패는 로깅만 한다."""
        try:
            await self._correction_client.fail_pdf_extraction(correction_id, error_message)
        except Exception:
            logger.exception("PDF 추출 실패 콜백 전송 실패 (correction_id: %s)", correction_id)

    @staticmethod
    def _normalize_structured_text(value: str) -> str:
        """구조화 문자열의 선행 '- ' bullet만 제거한다."""
        stripped = value.lstrip()
        if stripped.startswith("- "):
            return stripped[2:]
        return value

    @classmethod
    def _format_text_line_for_callback(cls, value: str) -> str:
        """callback 전송용 텍스트 라인을 정리한다."""
        normalized = cls._normalize_structured_text(value).strip()
        if not normalized:
            return ""
        return normalized

    @classmethod
    def _format_text_lines_for_callback(cls, values: list[str]) -> list[str]:
        """빈 항목을 제외하고 callback 전송용 텍스트 라인 목록을 생성한다."""
        formatted_lines = [cls._format_text_line_for_callback(value) for value in values]
        return [line for line in formatted_lines if line]

    @classmethod
    def _format_activities_for_callback(cls, activities: list[PdfActivity]) -> list[PdfActivity]:
        """PDF 추출 완료 callback 전송용으로 텍스트 리스트를 정리한다."""
        formatted_activities: list[PdfActivity] = []
        for activity in activities:
            formatted_activities.append(
                activity.model_copy(
                    update={
                        "detail": cls._format_text_lines_for_callback(activity.detail),
                        "responsibility": cls._format_text_lines_for_callback(
                            activity.responsibility
                        ),
                        "learning": cls._format_text_lines_for_callback(activity.learning),
                    }
                )
            )
        return formatted_activities

    @staticmethod
    def _fit_lines_to_limit(lines: list[str], limit: int) -> list[str]:
        """불릿을 개행으로 이은 전체 길이가 limit 이하가 되도록 앞에서부터 담는다.

        상한을 넘는 불릿에서 멈추되, 남은 예산만큼은 잘라서 살린다.

        Args:
            lines: 카테고리에 속한 불릿 텍스트 목록
            limit: 카테고리 전체 글자수 상한

        Returns:
            list[str]: 상한 이하로 정리된 불릿 목록
        """
        kept: list[str] = []
        used = 0

        for line in lines:
            separator = _CATEGORY_LINE_SEPARATOR_LENGTH if kept else 0
            remaining = limit - used - separator
            if remaining <= 0:
                break

            if is_within_char_limit(line, remaining):
                kept.append(line)
                used += separator + len(line)
                continue

            truncated = truncate_to_char_limit(line, remaining)
            if truncated:
                kept.append(truncated)
            break

        return kept

    @staticmethod
    def _fit_fields_to_budget(fields: list[str], budget: int) -> list[str]:
        """필드들을 순서대로 채우되, 예산을 넘는 필드에서 잘라내고 이후 필드는 비운다.

        `_fit_lines_to_limit` 과 달리 필드 사이에 구분자를 더하지 않는다. 문제해결
        항목의 situation·strategy·reason 사이 개행은 `_PROBLEM_SOLVING_ITEM_OVERHEAD`
        가 라벨과 함께 이미 계산해 뒀기 때문에, 여기서 또 더하면 예산을 이중으로
        깎아 필요 이상으로 잘라내게 된다.

        Args:
            fields: 순서대로 채울 필드 목록
            budget: 필드 전체에 쓸 수 있는 글자수 예산 (구분자 제외)

        Returns:
            list[str]: `fields` 와 같은 길이로, 예산 내로 정리된 필드 목록
        """
        fitted: list[str] = []
        used = 0

        for field in fields:
            remaining = budget - used
            if remaining <= 0:
                fitted.append("")
                continue

            if is_within_char_limit(field, remaining):
                fitted.append(field)
                used += len(field)
            else:
                fitted.append(truncate_to_char_limit(field, remaining))
                used = budget

        return fitted

    @classmethod
    def _fit_problem_solving_to_limit(
        cls,
        items: list[PdfProblemSolvingItem],
        limit: int,
    ) -> list[PdfProblemSolvingItem]:
        """문제해결 항목을 카테고리 글자수 상한에 맞춰 앞에서부터 담는다.

        메인 서버가 라벨(`#N`·`상황: ` 등)을 붙여 저장하므로 항목마다 고정 오버헤드를
        함께 센다. 항목 하나가 남은 예산을 넘으면 situation·strategy·reason 순으로
        채우되, 셋 중 하나라도 빈 문자열이 되면 그 항목은 통째로 버린다 (메인 서버
        DTO 가 세 필드 모두 비어있지 않기를 요구한다).

        Args:
            items: 문제해결 항목 목록
            limit: 카테고리 전체 글자수 상한

        Returns:
            list[PdfProblemSolvingItem]: 상한 이하로 정리된 항목 목록
        """
        kept: list[PdfProblemSolvingItem] = []
        used = 0

        for item in items:
            fields = [item.situation, item.strategy, item.reason]
            item_length = _PROBLEM_SOLVING_ITEM_OVERHEAD + sum(len(field) for field in fields)
            separator = _PROBLEM_SOLVING_ITEM_SEPARATOR_LENGTH if kept else 0
            remaining = limit - used - separator
            if remaining <= 0:
                break

            if item_length <= remaining:
                kept.append(item)
                used += separator + item_length
                continue

            field_budget = remaining - _PROBLEM_SOLVING_ITEM_OVERHEAD
            if field_budget <= 0:
                break

            fitted = cls._fit_fields_to_budget(fields, field_budget)
            # 메인 서버 DTO(PdfExtractionProblemSolvingReqDTO)는 situation·strategy·reason
            # 모두 @IsNotEmpty() 다. 셋 중 하나라도 예산이 바닥나 빈 문자열이 되면 이 항목은
            # 아예 버린다 — 빈 문자열로 보내면 활동 배열 전체가 콜백에서 400으로 거부된다.
            if all(fitted):
                kept.append(
                    item.model_copy(
                        update={
                            "situation": fitted[0],
                            "strategy": fitted[1],
                            "reason": fitted[2],
                        }
                    )
                )
            break

        return kept

    @classmethod
    def _normalize_activity(
        cls,
        activity: PdfActivity,
        seen_names: set[str],
    ) -> PdfActivity | None:
        """활동 1건을 콜백 형식으로 정규화한다.

        중복 활동명이면 `None` 을 반환한다. `seen_names` 는 호출자가 소유하며
        이 메서드가 새 활동명을 추가한다. 배치 경로와 스트리밍 경로가 같은 규칙을
        쓰도록 활동 단위로 분리했다.

        Args:
            activity: 추출된 활동 1건
            seen_names: 지금까지 채택한 활동명 집합 (호출자가 유지)

        Returns:
            PdfActivity | None: 정규화된 활동. 활동명이 비었거나 중복이면 None
        """
        dedupe_key = activity.activity_name.strip()
        if not dedupe_key or dedupe_key in seen_names:
            return None

        seen_names.add(dedupe_key)
        limits = get_pdf_extraction_limits()

        normalized_problem_solving = [
            item.model_copy(
                update={
                    "situation": cls._normalize_structured_text(item.situation).strip(),
                    "strategy": cls._normalize_structured_text(item.strategy).strip(),
                    "reason": cls._normalize_structured_text(item.reason).strip(),
                }
            )
            for item in activity.problem_solving
        ]
        # 메인 서버 DTO 는 situation·strategy·reason 모두 @IsNotEmpty() 다. LLM 이
        # 원본에서부터 한 필드를 비워 뱉었다면(스키마상 빈 문자열도 유효한 str) 여기서
        # 걸러내지 않으면 콜백 전송 시 활동 배열 전체가 400 으로 거부된다.
        problem_solving = [
            item.model_copy(update={"no": index})
            for index, item in enumerate(
                (
                    item
                    for item in normalized_problem_solving
                    if item.situation and item.strategy and item.reason
                ),
                start=1,
            )
        ]

        return activity.model_copy(
            update={
                "activity_name": dedupe_key,
                "detail": cls._fit_lines_to_limit(
                    [cls._normalize_structured_text(item) for item in activity.detail],
                    limits.detail_max_length,
                ),
                "responsibility": cls._fit_lines_to_limit(
                    [cls._normalize_structured_text(item) for item in activity.responsibility],
                    limits.responsibility_max_length,
                ),
                "problem_solving": cls._fit_problem_solving_to_limit(
                    problem_solving,
                    limits.problem_solving_max_length,
                ),
                "learning": cls._fit_lines_to_limit(
                    [cls._normalize_structured_text(item) for item in activity.learning],
                    limits.learning_max_length,
                ),
            }
        )

    @classmethod
    def _validate_result(cls, result: PdfExtractionResult) -> list[PdfActivity]:
        """추출 결과를 후처리하고 메인 서버 콜백 형식으로 정리한다."""
        activities = list(result.activities)
        if not activities:
            raise ValueError("PDF에서 추출된 활동이 없습니다.")

        limits = get_pdf_extraction_limits()
        seen_names: set[str] = set()
        normalized_activities: list[PdfActivity] = []

        for activity in activities:
            normalized = cls._normalize_activity(activity, seen_names)
            if normalized is not None:
                normalized_activities.append(normalized)

        if not normalized_activities:
            raise ValueError("PDF에서 추출된 활동이 없습니다.")

        # 중복 제거 후에 자른다. 먼저 자르면 중복 활동이 상한 슬롯을 차지해
        # 실제 활동이 max_activity_count개보다 적게 남는다.
        return normalized_activities[: limits.max_activity_count]


def _create_default_generator() -> "PdfExtractionGenerator":
    """기본 PDF 추출 generator를 생성한다."""
    from .generator import PdfExtractionGenerator

    return PdfExtractionGenerator()


def get_pdf_extraction_service() -> PdfExtractionService:
    """PdfExtractionService 싱글톤을 반환한다."""
    global _service

    if _service is None:
        _service = PdfExtractionService(
            correction_client=get_correction_client(),
            generator=_create_default_generator(),
        )

    return _service


def init_pdf_extraction_service(
    correction_client: CorrectionClient,
    generator: PdfExtractionGeneratorProtocol,
) -> PdfExtractionService:
    """PdfExtractionService 싱글톤을 초기화한다."""
    global _service

    _service = PdfExtractionService(correction_client=correction_client, generator=generator)
    return _service


def reset_pdf_extraction_service() -> None:
    """PdfExtractionService 싱글톤을 리셋한다."""
    global _service

    _service = None


__all__ = [
    "PdfExtractionService",
    "get_pdf_extraction_service",
    "init_pdf_extraction_service",
    "reset_pdf_extraction_service",
]
