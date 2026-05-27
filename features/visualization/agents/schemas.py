"""시각화 생성 LLM 출력 스키마."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


class SlidePlanItemOutput(BaseModel):
    """LLM Call #1 의 단일 slide_plan 항목."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    order: int = Field(ge=1)
    selected_slide_id: str | None = None
    source_slide_id: str | None = None
    reason: str = ""
    content_brief: str = Field(min_length=1)

    @property
    def resolved_source_slide_id(self) -> str:
        """selected_slide_id/source_slide_id 중 실제 Source Slide id 를 반환한다."""
        source_slide_id = self.selected_slide_id or self.source_slide_id
        if not source_slide_id:
            raise ValueError("selected_slide_id 또는 source_slide_id 가 필요합니다.")
        return source_slide_id


class SlidePlanOutput(BaseModel):
    """LLM Call #1 전체 출력."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    total_slides: int | None = Field(default=None, ge=1)
    slide_plan: list[SlidePlanItemOutput] | None = None
    selected_slides: list[SlidePlanItemOutput] | None = None

    @model_validator(mode="after")
    def validate_items(self) -> "SlidePlanOutput":
        """slide_plan 또는 selected_slides 중 하나를 요구한다."""
        if not self.items:
            raise ValueError("slide_plan 또는 selected_slides 배열이 필요합니다.")
        return self

    @property
    def items(self) -> list[SlidePlanItemOutput]:
        """표준화된 slide_plan 항목 목록."""
        return self.slide_plan or self.selected_slides or []


class FillOutput(BaseModel):
    """단일 shape_id 에 대한 Fill 데이터."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    action: Literal["text", "remove", "chart"] = "text"
    text: str | None = None
    font_size_override: float | None = None
    is_title: bool | None = None
    data: dict[str, Any] | None = None


class FillMapOutput(RootModel[dict[str, FillOutput]]):
    """shape_id -> Fill 데이터 맵."""


class FillPayloadOutput(BaseModel):
    """LLM Call #2 전체 출력."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    fills: dict[str, FillOutput]

    @classmethod
    def from_payload(cls, payload: Any) -> "FillPayloadOutput":
        """fills 래퍼가 있거나 없는 LLM 응답을 표준 스키마로 변환한다."""
        if isinstance(payload, dict) and "fills" in payload:
            return cls.model_validate(payload)
        root = FillMapOutput.model_validate(payload)
        return cls(fills=root.root)


__all__ = [
    "FillOutput",
    "FillPayloadOutput",
    "SlidePlanItemOutput",
    "SlidePlanOutput",
]
