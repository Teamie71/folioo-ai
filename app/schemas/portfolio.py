"""포트폴리오 API 스키마 정의"""

from pydantic import BaseModel, Field, field_validator

from features.portfolio.schemas import PortfolioStatus


# ===== 생성 요청/응답 =====
class GeneratePortfolioRequest(BaseModel):
    """포트폴리오 생성 요청"""

    portfolio_id: int = Field(..., gt=0, description="메인 서버에서 생성된 포트폴리오 ID")
    session_id: str = Field(..., min_length=1, description="인터뷰 세션 ID")
    user_id: str = Field(..., min_length=1, description="사용자 ID")


class GeneratePortfolioResponse(BaseModel):
    """포트폴리오 생성 응답 (202 Accepted)"""

    portfolio_id: str = Field(..., description="생성된 포트폴리오 ID")
    status: PortfolioStatus = Field(..., description="현재 생성 상태")


# ===== 상태 조회 =====
class PortfolioStatusResponse(BaseModel):
    """포트폴리오 생성 상태 조회 응답"""

    status: PortfolioStatus = Field(..., description="현재 생성 상태")
    progress_message: str | None = Field(None, description="진행 상황 메시지")


# ===== 결과 조회 =====
class PortfolioResultResponse(BaseModel):
    """포트폴리오 전체 결과 응답"""

    portfolio_id: str = Field(..., description="포트폴리오 ID")
    session_id: str = Field(..., description="인터뷰 세션 ID")
    user_id: str = Field(..., description="사용자 ID")
    experience_name: str = Field(..., description="경험/프로젝트명")
    status: PortfolioStatus = Field(..., description="생성 상태")
    contribution_rate: int | None = Field(None, ge=0, le=100, description="기여도 (0-100%)")
    description: str = Field(..., description="상세정보")
    contributions: str = Field(..., description="담당업무")
    achievements: str = Field(..., description="문제해결")
    insights: str = Field(..., description="배운 점")


# ===== 기여도 수정 =====
class UpdateContributionRateRequest(BaseModel):
    """기여도 수정 요청"""

    contribution_rate: int = Field(..., ge=0, le=100, description="기여도 (0-100)")

    @field_validator("contribution_rate")
    @classmethod
    def validate_contribution_rate(cls, v: int) -> int:
        """기여도 범위 검증"""
        if not 0 <= v <= 100:
            raise ValueError("기여도는 0에서 100 사이여야 합니다.")
        return v
