"""첨삭 도메인 스키마 정의"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class CorrectionLine(BaseModel):
    """라인별 첨삭 결과"""

    line_number: int = Field(..., description="라인 번호")
    original_text: str = Field(..., description="원문 텍스트")
    type: Literal["reduce", "keep", "emphasize"] = Field(..., description="첨삭 타입")
    comment: str = Field(..., description="첨삭 코멘트")


class CorrectionField(BaseModel):
    """필드별 첨삭 결과"""

    field_name: Literal["description", "contributions", "achievements", "insights"] = Field(
        ..., description="첨삭 대상 필드명"
    )
    lines: list[CorrectionLine] = Field(..., description="라인별 첨삭 목록")


class CorrectionOutput(BaseModel):
    """LLM Structured Output 최종 스키마"""

    fields: list[CorrectionField] = Field(..., description="필드별 첨삭 결과")
    overall_summary: str = Field(..., description="전체 첨삭 요약")


class CorrectionStatus(str, Enum):
    """첨삭 상태"""

    NOT_STARTED = "not_started"
    DOING_RAG = "doing_rag"
    COMPANY_INSIGHT = "company_insight"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"
