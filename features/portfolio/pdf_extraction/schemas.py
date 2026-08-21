"""PDF 포트폴리오 구조화 도메인 스키마 정의"""

from pydantic import BaseModel, Field


class PdfProblemSolvingItem(BaseModel):
    """문제 해결 항목 스키마"""

    no: int = Field(..., ge=1, description="문제 해결 항목 번호")
    situation: str = Field(..., description="문제 상황")
    strategy: str = Field(..., description="대응 전략")
    reason: str = Field(..., description="전략 선택 이유")


class PdfActivity(BaseModel):
    """PDF에서 추출한 활동 스키마"""

    activity_name: str = Field(..., description="활동명 또는 프로젝트명")
    detail: list[str] = Field(..., description="활동 상세 설명 목록")
    responsibility: list[str] = Field(..., description="담당 업무 목록")
    problem_solving: list[PdfProblemSolvingItem] = Field(..., description="문제 해결 내용 목록")
    learning: list[str] = Field(..., description="배운 점 목록")


class PdfExtractionResult(BaseModel):
    """LLM Structured Output용 PDF 추출 결과"""

    activities: list[PdfActivity] = Field(
        ...,
        min_length=1,
        max_length=4,
        description="PDF에서 추출한 활동 목록",
    )
