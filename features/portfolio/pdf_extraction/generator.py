"""PDF 추출 생성기"""

from __future__ import annotations

import os

from common.llm.client import get_llm

from .prompts import build_pdf_extraction_messages
from .schemas import PdfExtractionResult

_DEFAULT_PDF_EXTRACTION_MODEL_NAME = "google/gemini-3.1-pro"


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


__all__ = ["PdfExtractionGenerationError", "PdfExtractionGenerator"]
