"""포트폴리오 핵심 스키마 정의"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class PortfolioOutput(BaseModel):
    """
    LLM Structured Output용 포트폴리오 스키마

    LLM의 .with_structured_output() 호출에 직접 사용됩니다.
    """

    detail_info: str = Field(..., description="상세정보, 서술형 문단")
    assigned_task: str = Field(..., description="담당업무, 서술형 문단")
    problem_solving: str = Field(..., description="문제해결, 서술형 문단")
    lessons_learned: str = Field(..., description="배운 점, 서술형 문단")


class PortfolioStatus(str, Enum):
    """포트폴리오 생성 상태"""

    NOT_STARTED = "not_started"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class PortfolioResult(BaseModel):
    """
    최종 포트폴리오 결과 모델

    PortfolioOutput + 메타데이터를 포함합니다.
    """

    portfolio_id: str = Field(..., description="포트폴리오 고유 ID")
    session_id: str = Field(..., description="인터뷰 세션 ID")
    user_id: str = Field(..., description="사용자 ID")
    experience_name: str = Field(..., description="경험/프로젝트명")
    status: PortfolioStatus = Field(default=PortfolioStatus.NOT_STARTED, description="생성 상태")
    contribution_rate: int | None = Field(None, ge=0, le=100, description="기여도 (0-100%)")
    output: PortfolioOutput | None = Field(None, description="LLM이 생성한 포트폴리오 내용")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="생성 시각")
