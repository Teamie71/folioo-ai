"""PDF 추출 API 스키마 정의"""

from typing import Literal

from pydantic import BaseModel, Field


class PdfExtractionAcceptedResponse(BaseModel):
    """PDF 추출 요청 접수 응답"""

    correction_id: str = Field(..., description="첨삭 ID")
    status: Literal["accepted"] = Field(..., description="요청 접수 상태")
    message: str = Field(..., description="응답 메시지")


__all__ = ["PdfExtractionAcceptedResponse"]
