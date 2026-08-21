"""PDF 추출 서비스"""

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

from fastapi import BackgroundTasks

from common.clients.correction_client import CorrectionClient, get_correction_client

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
            try:
                await self._correction_client.fail_pdf_extraction(correction_id, str(exc))
            except Exception:
                logger.exception("PDF 추출 실패 콜백 전송 실패 (correction_id: %s)", correction_id)
            return

        try:
            await self._correction_client.complete_pdf_extraction(
                correction_id,
                activities=[
                    activity.model_dump()
                    for activity in self._format_activities_for_callback(activities)
                ],
                source_type="EXTERNAL",
            )
        except Exception:
            logger.exception("PDF 추출 완료 콜백 전송 실패 (correction_id: %s)", correction_id)

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

            if len(line) <= remaining:
                kept.append(line)
                used += separator + len(line)
                continue

            truncated = line[:remaining]
            if truncated:
                kept.append(truncated)
            break

        return kept

    @classmethod
    def _fit_problem_solving_to_limit(
        cls,
        items: list[PdfProblemSolvingItem],
        limit: int,
    ) -> list[PdfProblemSolvingItem]:
        """문제해결 항목을 카테고리 글자수 상한에 맞춰 앞에서부터 담는다.

        메인 서버가 라벨(`#N`·`상황: ` 등)을 붙여 저장하므로 항목마다 고정 오버헤드를
        함께 센다. 항목 하나가 남은 예산을 넘으면 situation·strategy·reason 순으로
        채우고, 예산이 바닥난 필드는 빈 문자열로 남긴다.

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

            fitted = cls._fit_lines_to_limit(fields, field_budget)
            fitted += [""] * (len(fields) - len(fitted))
            if any(fitted):
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
    def _validate_result(cls, result: PdfExtractionResult) -> list[PdfActivity]:
        """추출 결과를 후처리하고 메인 서버 콜백 형식으로 정리한다."""
        activities = list(result.activities)
        if not activities:
            raise ValueError("PDF에서 추출된 활동이 없습니다.")

        limits = get_pdf_extraction_limits()
        seen_names: set[str] = set()
        normalized_activities: list[PdfActivity] = []

        for activity in activities:
            dedupe_key = activity.activity_name.strip()
            if not dedupe_key or dedupe_key in seen_names:
                continue

            seen_names.add(dedupe_key)
            problem_solving = [
                item.model_copy(
                    update={
                        "no": index,
                        "situation": cls._normalize_structured_text(item.situation),
                        "strategy": cls._normalize_structured_text(item.strategy),
                        "reason": cls._normalize_structured_text(item.reason),
                    }
                )
                for index, item in enumerate(activity.problem_solving, start=1)
            ]
            normalized_activities.append(
                activity.model_copy(
                    update={
                        "activity_name": dedupe_key,
                        "detail": cls._fit_lines_to_limit(
                            [cls._normalize_structured_text(item) for item in activity.detail],
                            limits.detail_max_length,
                        ),
                        "responsibility": cls._fit_lines_to_limit(
                            [
                                cls._normalize_structured_text(item)
                                for item in activity.responsibility
                            ],
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
            )

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
