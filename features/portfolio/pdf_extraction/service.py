"""PDF 추출 서비스"""

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

from fastapi import BackgroundTasks

from common.clients.correction_client import CorrectionClient, get_correction_client

from .schemas import PdfActivity, PdfExtractionResult

if TYPE_CHECKING:
    from .generator import PdfExtractionGenerator

logger = logging.getLogger(__name__)

_service: "PdfExtractionService | None" = None
_MAX_PDF_FILE_SIZE_BYTES = 10 * 1024 * 1024
_PDF_MIME_TYPE = "application/pdf"


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
                activities=[activity.model_dump() for activity in activities],
                source_type="EXTERNAL",
            )
        except Exception:
            logger.exception("PDF 추출 완료 콜백 전송 실패 (correction_id: %s)", correction_id)

    @staticmethod
    def _validate_result(result: PdfExtractionResult) -> list[PdfActivity]:
        """추출 결과를 후처리하고 메인 서버 콜백 형식으로 정리한다."""
        activities = list(result.activities[:5])
        if not activities:
            raise ValueError("PDF에서 추출된 활동이 없습니다.")

        seen_names: set[str] = set()
        normalized_activities: list[PdfActivity] = []

        for activity in activities:
            dedupe_key = activity.activity_name.strip()
            if not dedupe_key or dedupe_key in seen_names:
                continue

            seen_names.add(dedupe_key)
            problem_solving = [
                item.model_copy(update={"no": index})
                for index, item in enumerate(activity.problem_solving, start=1)
            ]
            normalized_activities.append(
                activity.model_copy(
                    update={
                        "activity_name": dedupe_key,
                        "problem_solving": problem_solving,
                    }
                )
            )

        if not normalized_activities:
            raise ValueError("PDF에서 추출된 활동이 없습니다.")

        return normalized_activities


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
