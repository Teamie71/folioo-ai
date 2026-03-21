"""PDF 추출 프롬프트 생성기"""

from __future__ import annotations

import base64
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

_CLASSIFICATION_PATH = Path(__file__).with_name("classification.md")


def _strip_front_matter(content: str) -> str:
    """YAML front matter가 있으면 제거한다."""
    if not content.startswith("---\n"):
        return content

    _, _, remainder = content.partition("\n---\n")
    return remainder or content


def load_pdf_classification_criteria() -> str:
    """PDF 활동 구조화 기준 문서를 읽는다."""
    try:
        content = _CLASSIFICATION_PATH.read_text(encoding="utf-8").strip()
        return _strip_front_matter(content).strip()
    except OSError as exc:
        raise ValueError(f"PDF 추출 기준 문서를 읽을 수 없습니다: {exc}") from exc


def encode_pdf_data_url(file_bytes: bytes) -> str:
    """PDF 바이트를 base64 데이터 URL로 변환한다."""
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return f"data:application/pdf;base64,{encoded}"


def build_pdf_extraction_messages(
    file_bytes: bytes, filename: str
) -> list[SystemMessage | HumanMessage]:
    """PDF 추출용 멀티모달 메시지 리스트를 생성한다."""
    criteria = load_pdf_classification_criteria()
    pdf_data_url = encode_pdf_data_url(file_bytes)

    system_message = SystemMessage(content=criteria)
    human_message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": (
                    "첨부한 PDF를 읽고 활동 정보를 구조화해주세요. "
                    f"파일명은 '{filename}'입니다. "
                    "응답은 반드시 PdfExtractionResult 스키마에 맞춰 작성하세요."
                ),
            },
            {
                "type": "file",
                "file": {
                    "filename": filename,
                    "file_data": pdf_data_url,
                },
            },
        ]
    )

    return [system_message, human_message]


__all__ = [
    "build_pdf_extraction_messages",
    "encode_pdf_data_url",
    "load_pdf_classification_criteria",
]
