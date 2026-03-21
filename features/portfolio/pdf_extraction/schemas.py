"""PDF 포트폴리오 구조화 스키마"""

from pydantic import BaseModel, Field


class PdfProblemSolvingItem(BaseModel):
    """문제 해결 항목"""

    no: int = Field(..., ge=1, description="문제 해결 순번")
    situation: str = Field(..., min_length=1, description="문제 상황")
    strategy: str = Field(..., min_length=1, description="대응 전략")
    reason: str = Field(..., min_length=1, description="전략 선택 이유")


class PdfActivity(BaseModel):
    """PDF에서 추출한 활동 단위"""

    activity_name: str = Field(..., min_length=1, description="활동명")
    detail: str = Field(..., min_length=1, description="활동 상세 설명")
    responsibility: str = Field(..., min_length=1, description="담당 업무")
    problem_solving: list[PdfProblemSolvingItem] = Field(
        default_factory=list,
        description="문제 해결 목록",
    )
    learning: str = Field(..., min_length=1, description="배운 점")


class PdfExtractionResult(BaseModel):
    """LLM structured output용 PDF 추출 결과"""

    activities: list[PdfActivity] = Field(..., min_length=1, description="추출된 활동 목록")


__all__ = ["PdfActivity", "PdfExtractionResult", "PdfProblemSolvingItem"]
