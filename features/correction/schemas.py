"""첨삭 도메인 스키마 정의"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

REQUIRED_CORRECTION_FIELDS = ("description", "contributions", "achievements", "insights")


class CorrectionLine(BaseModel):
    """라인별 첨삭 결과"""

    line_number: int = Field(..., ge=1, description="라인 번호")
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

    @model_validator(mode="after")
    def validate_required_sections(self) -> "CorrectionOutput":
        """필수 섹션 4개가 정확히 1회씩 포함되는지 검증"""
        field_names = [field.field_name for field in self.fields]

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

        return self


class CorrectionStatus(str, Enum):
    """첨삭 상태"""

    NOT_STARTED = "not_started"
    DOING_RAG = "doing_rag"
    COMPANY_INSIGHT = "company_insight"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"
