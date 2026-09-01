"""첨삭 API 스키마 정의"""

from pydantic import BaseModel, Field

from features.correction.config.loader import get_correction_rag_config
from features.correction.schemas import CorrectionOutput, CorrectionStatus

# 기업 분석 최대 길이 (설정에서 로드)
MAX_COMPANY_INSIGHT_LENGTH = get_correction_rag_config().company_insight_max_length


# ===== 생성 요청/응답 =====
class CreateCorrectionRequest(BaseModel):
    """첨삭 세션 생성 요청"""

    portfolio_id: str = Field(..., min_length=1, description="포트폴리오 ID")
    user_id: str = Field(..., min_length=1, description="사용자 ID")
    company_name: str = Field(..., min_length=1, description="회사명")
    job_title: str = Field(..., min_length=1, description="직무명")
    job_description: str = Field(..., min_length=1, description="직무 설명")


class CreateCorrectionResponse(BaseModel):
    """첨삭 세션 생성 응답"""

    correction_id: str = Field(..., description="생성된 첨삭 ID")
    status: CorrectionStatus = Field(..., description="현재 첨삭 상태")


# ===== 상태 조회 =====
class CorrectionStatusResponse(BaseModel):
    """첨삭 상태 조회 응답"""

    status: CorrectionStatus = Field(..., description="현재 첨삭 상태")
    progress_message: str | None = Field(None, description="진행 상황 메시지")


# ===== 결과 조회 =====
class CorrectionResultResponse(BaseModel):
    """첨삭 결과 조회 응답"""

    correction_id: str = Field(..., description="첨삭 ID")
    status: CorrectionStatus = Field(..., description="첨삭 상태")
    result: CorrectionOutput | None = Field(None, description="첨삭 결과")


# ===== 기업 분석 수정 =====
class UpdateCompanyInsightRequest(BaseModel):
    """기업 분석 수정 요청"""

    company_insight: str = Field(
        ...,
        min_length=1,
        max_length=MAX_COMPANY_INSIGHT_LENGTH,
        description="수정된 기업 분석 내용",
    )


# ===== 강조 포인트 수정 =====
class UpdateEmphasisPointsRequest(BaseModel):
    """강조 포인트 수정 요청"""

    emphasis_points: str = Field(
        ..., description="수정된 강조 포인트 내용 (선택 항목, 빈 문자열 허용)"
    )


class CompanyInsightResponse(BaseModel):
    """기업 분석 조회 응답"""

    company_insight: str = Field(..., description="기업 분석 내용")
