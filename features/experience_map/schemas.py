"""경험정리 내부 스키마

세 묶음으로 나뉜다.

1. LLM structured output — 노드가 LLM에게 받는 형식 (에이전트 문서 8-1)
2. 커밋 operation — 메인 서버 커밋 API로 보내는 items (API 명세 4-2)
3. gap — 턴 사이에 유지되는 제안 상태 (API 명세 3-2)

LLM에는 실제 block ID를 노출하지 않는다. `*_ref` 필드는 요청 안에서만 유효한
별칭(`exp_1`, `b_1`)이며, 실제 ID 변환은 AI 서버가 수행한다.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from features.experience_map.config import MAX_CONTENT_LENGTH, MIN_CONTENT_LENGTH

# ===== 공통 타입 =====

SectionKind = Literal["DETAIL", "ACHIEVEMENT", "TASK", "PROBLEM_SOLVING", "LEARNING"]
"""level 3 카테고리 종류. API 전용 식별자이며 메인이 DB enum으로 매핑한다."""

SECTION_LABELS: dict[str, str] = {
    "DETAIL": "상세정보",
    "ACHIEVEMENT": "주요성과",
    "TASK": "담당업무",
    "PROBLEM_SOLVING": "문제해결",
    "LEARNING": "배운 점",
}

Intent = Literal["file_input", "chat_input", "out_of_scope"]
GapType = Literal["extend_block", "new_child_block"]
ItemAction = Literal["add", "update"]
FallbackReason = Literal["out_of_scope", "file_unreadable", "nothing_to_apply", "ambiguous_target"]
NodeName = Literal[
    "router",
    "file_processor",
    "content_filter",
    "gap_resolver",
    "structure",
    "refine",
    "validate",
    "commit",
]


def _validate_content(value: str | None) -> str | None:
    """content 길이 검증. 값이 있으면 공백 제외 1~500자."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("content가 공백만으로 이루어질 수 없습니다.")
    if not MIN_CONTENT_LENGTH <= len(stripped) <= MAX_CONTENT_LENGTH:
        raise ValueError(
            f"content는 공백 제외 {MIN_CONTENT_LENGTH}~{MAX_CONTENT_LENGTH}자여야 합니다. "
            f"(현재 {len(stripped)}자)"
        )
    return value


# ===== 1. LLM structured output =====


class RouterOutput(BaseModel):
    """Router 노드 출력. `file_input`은 코드로 판정하므로 LLM이 고르지 않는다."""

    intent: Literal["chat_input", "out_of_scope"] = Field(..., description="분류 결과")
    reason: str = Field(..., description="판정 근거")


class FilteredItem(BaseModel):
    """반영 내용 필터링이 분류한 텍스트 조각"""

    item_id: str = Field(..., description="요청 안에서 유일한 식별자")
    text: str = Field(..., description="원문 텍스트")
    source: Literal["message", "file"] = Field(..., description="출처")


class ContentFilterOutput(BaseModel):
    """반영 내용 필터링 노드 출력"""

    gap_answer_items: list[FilteredItem] = Field(
        default_factory=list, description="활성 gap에 답한 내용"
    )
    new_items: list[FilteredItem] = Field(default_factory=list, description="새로 반영할 내용")
    excluded_reasons: list[str] = Field(default_factory=list, description="반영 제외 사유")


class StructuredItem(BaseModel):
    """블록 단위 구조화 노드 출력.

    `level`·`position`은 메인 서버가 계산하므로 받지 않는다.
    """

    item_id: str = Field(..., description="요청 안에서 유일한 식별자")
    action: ItemAction = Field(..., description="add 또는 update")
    parent_ref: str | None = Field(None, description="기존 블록 별칭. 커밋의 parent_id로 변환된다")
    parent_item_id: str | None = Field(
        None, description="같은 요청에서 앞서 정의한 add item의 item_id"
    )
    target_ref: str | None = Field(None, description="update 대상 블록 별칭")
    section_kind: SectionKind | None = Field(None, description="level 3 카테고리를 생성할 때만")
    slot_id: str | None = Field(None, description="템플릿 슬롯 식별자")
    text: str | None = Field(None, description="블록 내용. 템플릿 빈 슬롯은 None")
    after_ref: str | None = Field(None, description="같은 부모의 형제 블록 별칭")

    @model_validator(mode="after")
    def _check_refs(self) -> "StructuredItem":
        """부모 참조와 action별 필수 필드를 검증한다."""
        if self.parent_ref is not None and self.parent_item_id is not None:
            raise ValueError(
                "parent_ref와 parent_item_id를 동시에 지정할 수 없습니다. "
                "기존 블록이면 parent_ref, 같은 요청의 신규 블록이면 parent_item_id를 씁니다."
            )

        if self.action == "add":
            if self.parent_ref is None and self.parent_item_id is None:
                raise ValueError("add는 parent_ref 또는 parent_item_id가 필요합니다.")
            if self.target_ref is not None:
                raise ValueError("add는 target_ref를 가질 수 없습니다.")
        else:
            if self.target_ref is None:
                raise ValueError("update는 target_ref가 필요합니다.")
            if self.parent_ref is not None or self.parent_item_id is not None:
                raise ValueError("update는 부모를 바꿀 수 없습니다.")
            if self.section_kind is not None:
                raise ValueError("update는 section_kind를 가질 수 없습니다.")

        return self


class RefinedItem(BaseModel):
    """문장 정제 노드 출력.

    **배정 필드가 없다.** 정제 노드가 블록 배정을 바꾸지 못하도록 스키마로 막는다.
    """

    item_id: str = Field(..., description="구조화 결과의 item_id")
    refined_text: str | None = Field(None, description="정제된 텍스트")


class GapCandidate(BaseModel):
    """gap 분석 노드가 고른 gap. 별칭 기준이며 실제 ID 변환 전이다."""

    gap_type: GapType = Field(..., description="후속 노드를 결정한다")
    anchor_ref: str = Field(..., description="기준 블록 별칭")
    reason: str | None = Field(None, description="선정 근거")


class GapOutput(BaseModel):
    """gap 분석 노드 출력. gap이 없어도 제안 문구는 보낸다."""

    gap: GapCandidate | None = Field(None, description="보완이 필요하면 최대 1개")
    message: str = Field(..., description="사용자에게 보여줄 제안 문구")


# ===== 2. 커밋 operation (API 명세 4-2) =====


class CommitAddItem(BaseModel):
    """블록 생성 operation"""

    item_id: str
    action: Literal["add"] = "add"
    parent_id: str | None = Field(None, description="기존 블록의 실제 ID (십진 문자열)")
    parent_item_id: str | None = Field(None, description="같은 요청의 앞선 add item")
    section_kind: SectionKind | None = None
    slot_id: str | None = None
    content: str | None = Field(None, description="카테고리 컨테이너와 템플릿 빈 슬롯은 생략")
    after_id: str | None = Field(None, description="null이면 형제 목록 끝에 추가")

    @model_validator(mode="after")
    def _check(self) -> "CommitAddItem":
        if (self.parent_id is None) == (self.parent_item_id is None):
            raise ValueError("add는 parent_id와 parent_item_id 중 정확히 하나가 필요합니다.")
        _validate_content(self.content)
        return self


class CommitUpdateItem(BaseModel):
    """블록 수정 operation"""

    item_id: str
    action: Literal["update"] = "update"
    target_id: str = Field(..., description="수정 대상 블록의 실제 ID")
    content: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "CommitUpdateItem":
        _validate_content(self.content)
        return self


CommitItem = CommitAddItem | CommitUpdateItem


class DroppedItem(BaseModel):
    """커밋 items에서 제외된 항목.

    validate 보정을 초과해 AI 서버가 떨어뜨린 것이므로 메인 서버는 존재를 모른다.
    """

    item_id: str
    reason: str = Field(..., description="예: validation_retry_exceeded")


class AppliedItem(BaseModel):
    """커밋 API가 반영한 블록"""

    item_id: str
    block_id: str
    path: str = Field(..., description="예: 교내 커머스 리뉴얼 > 문제해결")


class CommitResult(BaseModel):
    """커밋 결과. `dropped`만 AI 서버가 채운다."""

    request_id: str
    previous_version: int
    map_version: int
    revert_to_version: int
    can_revert: bool
    applied: list[AppliedItem] = Field(default_factory=list)
    dropped: list[DroppedItem] = Field(default_factory=list)


# ===== 3. gap 상태 (API 명세 3-2) =====


class ActiveGap(BaseModel):
    """`ai_experience_session.active_gap`에 저장되는 형태.

    다음 턴의 반영 내용 필터링이 gap 답변 분류에 사용한다.
    """

    gap_id: str = Field(..., description="UUID 문자열")
    gap_type: GapType
    anchor_block_id: str = Field(..., description="기준 블록의 실제 ID (십진 문자열)")
    message: str
    created_request_id: str = Field(..., description="이 gap을 만든 요청")
