"""PDF 추출 생성기"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

from common.llm.client import get_llm

from .prompts import build_pdf_extraction_messages
from .schemas import PdfActivity, PdfExtractionResult
from .streaming import ActivityJsonStreamParser

logger = logging.getLogger(__name__)

_DEFAULT_PDF_EXTRACTION_MODEL_NAME = "google/gemini-3.1-pro-preview"


class PdfExtractionGenerationError(Exception):
    """PDF 추출 생성 실패 예외"""


class PdfExtractionGenerator:
    """LLM 기반 PDF 추출 생성기"""

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or os.getenv(
            "PDF_EXTRACTION_MODEL_NAME", _DEFAULT_PDF_EXTRACTION_MODEL_NAME
        )

    def extract(self, file_bytes: bytes, filename: str) -> PdfExtractionResult:
        """PDF 바이트를 구조화된 활동 데이터로 추출한다."""
        messages = build_pdf_extraction_messages(file_bytes=file_bytes, filename=filename)

        try:
            llm = get_llm(model=self._model_name, temperature=0.0)
            structured_llm = llm.with_structured_output(PdfExtractionResult)
            return structured_llm.invoke(messages)
        except Exception as exc:
            raise PdfExtractionGenerationError(f"PDF 추출 생성에 실패했습니다: {filename}") from exc

    async def extract_stream(self, file_bytes: bytes, filename: str) -> AsyncIterator[PdfActivity]:
        """PDF 바이트를 추출하되 활동이 완성될 때마다 하나씩 흘려보낸다.

        `extract()` 와 달리 structured output 을 쓰지 않고 원문 토큰을 받아 부분 JSON 을
        직접 파싱한다. 스키마 강제는 프롬프트(`classification.md` 출력 형식)와 원소별
        `PdfActivity` 검증으로 대신한다.

        Args:
            file_bytes: PDF 파일 바이트
            filename: 원본 파일명

        Yields:
            PdfActivity: 완성된 활동 하나

        Raises:
            PdfExtractionGenerationError: LLM 호출이 실패한 경우
        """
        messages = build_pdf_extraction_messages(file_bytes=file_bytes, filename=filename)
        parser = ActivityJsonStreamParser()

        try:
            llm = get_llm(model=self._model_name, temperature=0.0)
            async for chunk in llm.astream(messages):
                for raw in parser.feed(_coerce_chunk_text(chunk)):
                    activity = _build_activity(raw)
                    if activity is not None:
                        yield activity
        except PdfExtractionGenerationError:
            raise
        except Exception as exc:
            raise PdfExtractionGenerationError(f"PDF 추출 생성에 실패했습니다: {filename}") from exc


def _coerce_chunk_text(chunk: object) -> str:
    """LLM 스트림 청크에서 텍스트만 뽑아낸다."""
    content = getattr(chunk, "content", chunk)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)

    return ""


def _build_activity(raw: dict) -> PdfActivity | None:
    """파싱된 원소를 PdfActivity 로 검증한다.

    원소 하나가 스키마에 어긋나도 나머지 활동까지 버리지 않도록 건너뛴다.
    """
    try:
        return PdfActivity.model_validate(raw)
    except Exception:
        logger.warning("PDF 추출 스트림에서 스키마에 맞지 않는 활동을 건너뜁니다", exc_info=True)
        return None


__all__ = ["PdfExtractionGenerationError", "PdfExtractionGenerator"]
