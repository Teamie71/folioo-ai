"""첨삭 도메인 스키마 정의"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REQUIRED_CORRECTION_FIELDS = ("description", "contributions", "achievements", "insights")


def _validate_required_correction_fields(field_names: list[str]) -> None:
    """필수 첨삭 필드가 정확히 1회씩 포함되는지 검증"""
    missing_fields = [name for name in REQUIRED_CORRECTION_FIELDS if name not in field_names]
    duplicated_fields = sorted({name for name in field_names if field_names.count(name) > 1})

    if missing_fields or duplicated_fields:
        problems: list[str] = []
        if missing_fields:
            problems.append(f"누락: {', '.join(missing_fields)}")
        if duplicated_fields:
            problems.append(f"중복: {', '.join(duplicated_fields)}")
        raise ValueError(
            "fields에는 description, contributions, achievements, insights가 "
            f"각각 정확히 1회씩 포함되어야 합니다. ({'; '.join(problems)})"
        )


class CorrectionDecisionLine(BaseModel):
    """LLM이 판단해야 하는 라인별 첨삭 결과"""

    model_config = ConfigDict(extra="forbid")

    line_number: int = Field(..., ge=1, description="라인 번호")
    type: Literal["reduce", "keep", "emphasize"] = Field(..., description="첨삭 타입")
    comment: str | None = Field(..., description="첨삭 코멘트 (keep인 경우 null 허용)")


class CorrectionDecisionField(BaseModel):
    """LLM이 판단해야 하는 필드별 첨삭 결과"""

    model_config = ConfigDict(extra="forbid")

    field_name: Literal["description", "contributions", "achievements", "insights"] = Field(
        ..., description="첨삭 대상 필드명"
    )
    lines: list[CorrectionDecisionLine] = Field(..., description="라인별 첨삭 목록")


class SingleCorrectionDecisionOutput(BaseModel):
    """단일 포트폴리오용 LLM decision 스키마"""

    model_config = ConfigDict(extra="forbid")

    fields: list[CorrectionDecisionField] = Field(..., description="필드별 첨삭 판단 결과")

    @model_validator(mode="after")
    def validate_required_sections(self) -> "SingleCorrectionDecisionOutput":
        """필수 섹션 4개가 정확히 1회씩 포함되는지 검증"""
        _validate_required_correction_fields([field.field_name for field in self.fields])
        return self


class CorrectionLine(BaseModel):
    """라인별 첨삭 결과"""

    line_number: int = Field(..., ge=1, description="라인 번호")
    original_text: str = Field(..., description="원문 텍스트")
    type: Literal["reduce", "keep", "emphasize"] = Field(..., description="첨삭 타입")
    comment: str | None = Field(..., description="첨삭 코멘트 (keep인 경우 null 허용)")


class CorrectionField(BaseModel):
    """필드별 첨삭 결과"""

    field_name: Literal["description", "contributions", "achievements", "insights"] = Field(
        ..., description="첨삭 대상 필드명"
    )
    lines: list[CorrectionLine] = Field(..., description="라인별 첨삭 목록")


class SingleCorrectionOutput(BaseModel):
    """단일 포트폴리오용 서버 저장 최종 스키마"""

    fields: list[CorrectionField] = Field(..., description="필드별 첨삭 결과")

    @model_validator(mode="after")
    def validate_required_sections(self) -> "SingleCorrectionOutput":
        """필수 섹션 4개가 정확히 1회씩 포함되는지 검증"""
        _validate_required_correction_fields([field.field_name for field in self.fields])
        return self


class PortfolioCorrectionResult(BaseModel):
    """포트폴리오별 첨삭 결과"""

    portfolio_id: int = Field(..., description="포트폴리오 ID")
    fields: list[CorrectionField] = Field(..., description="해당 포트폴리오의 필드별 첨삭 결과")


class CorrectionOutput(BaseModel):
    """다중 포트폴리오 첨삭 최종 스키마"""

    portfolio_corrections: list[PortfolioCorrectionResult] = Field(
        ..., description="포트폴리오별 첨삭 결과"
    )
    overall_summary: str = Field(..., description="전체 포트폴리오 총평")


class CorrectionStatus(str, Enum):
    """첨삭 상태"""

    NOT_STARTED = "not_started"
    DOING_RAG = "doing_rag"
    COMPANY_INSIGHT = "company_insight"
    GENERATING = "generating"
    DONE = "done"
    # 메인 서버가 AI 서버 호출 자체에 실패했을 때 넣는 상태다.
    # (folioo-server: portfolio-correction.facade.ts `delegateCompanyInsightCreation`)
    # 값이 없으면 상태 조회 응답 검증에서 터져 500 이 된다.
    RAG_FAILED = "rag_failed"
    FAILED = "failed"
