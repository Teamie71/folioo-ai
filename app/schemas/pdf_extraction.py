"""PDF 포트폴리오 구조화 API 스키마 정의"""

from pydantic import BaseModel, Field


class PdfExtractionAcceptedResponse(BaseModel):
    """PDF 추출 요청 접수 응답"""

    correction_id: str = Field(..., description="첨삭 ID")
    status: str = Field(..., description="현재 처리 상태")
    message: str = Field(..., description="처리 시작 안내 메시지")
