"""경험정리 내부 스키마 테스트"""

import pytest
from pydantic import ValidationError

from features.experience_map.schemas import (
    ActiveGap,
    CommitAddItem,
    CommitResult,
    CommitUpdateItem,
    ContentFilterOutput,
    GapOutput,
    RefinedItem,
    RouterOutput,
    StructuredItem,
)

# ===== structured output =====


def test_router_output_rejects_file_input():
    """file_input은 코드로 판정하므로 LLM이 고를 수 없다."""
    with pytest.raises(ValidationError):
        RouterOutput(intent="file_input", reason="파일이 있음")


def test_content_filter_output_defaults_to_empty():
    """세 분류 모두 비어 있을 수 있다 (전부 반영 제외)."""
    output = ContentFilterOutput()

    assert output.gap_answer_items == []
    assert output.new_items == []
    assert output.excluded_reasons == []


def test_refined_item_has_no_assignment_field():
    """정제 노드가 배정을 바꾸지 못하도록 스키마에 배정 필드가 없다."""
    fields = set(RefinedItem.model_fields)

    assert fields == {"item_id", "refined_text"}
    for forbidden in ("parent_ref", "parent_item_id", "target_ref", "section_kind", "after_ref"):
        assert forbidden not in fields


def test_gap_output_allows_no_gap():
    """gap이 없어도 제안 문구는 있어야 한다."""
    output = GapOutput(message="더 정리하고 싶으신 내용이 있나요?")

    assert output.gap is None
    assert output.message


# ===== StructuredItem 부모 참조 =====


def test_structured_item_rejects_both_parent_refs():
    """parent_ref와 parent_item_id를 동시에 지정할 수 없다."""
    with pytest.raises(ValidationError, match="동시에 지정할 수 없습니다"):
        StructuredItem(
            item_id="it_1",
            action="add",
            parent_ref="b_20",
            parent_item_id="it_0",
        )


def test_structured_item_add_requires_a_parent():
    """add는 부모 참조가 하나 필요하다."""
    with pytest.raises(ValidationError, match="parent_ref 또는 parent_item_id"):
        StructuredItem(item_id="it_1", action="add")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"parent_ref": "b_20"},
        {"parent_item_id": "it_0"},
    ],
)
def test_structured_item_add_accepts_either_parent(kwargs):
    """기존 블록은 parent_ref, 같은 요청의 신규 블록은 parent_item_id를 쓴다."""
    item = StructuredItem(item_id="it_1", action="add", text="내용", **kwargs)

    assert item.action == "add"


def test_structured_item_update_requires_target():
    with pytest.raises(ValidationError, match="target_ref가 필요합니다"):
        StructuredItem(item_id="it_1", action="update", text="내용")


def test_structured_item_update_cannot_change_parent():
    """update는 배정을 바꿀 수 없다."""
    with pytest.raises(ValidationError, match="부모를 바꿀 수 없습니다"):
        StructuredItem(
            item_id="it_1",
            action="update",
            target_ref="b_55",
            parent_ref="b_20",
        )


def test_structured_item_has_no_level_or_position():
    """level·position은 메인 서버가 계산하므로 LLM 출력에 없다."""
    fields = set(StructuredItem.model_fields)

    assert "level" not in fields
    assert "position" not in fields


# ===== 커밋 operation =====


def test_commit_add_requires_exactly_one_parent():
    with pytest.raises(ValidationError, match="정확히 하나"):
        CommitAddItem(item_id="it_1", parent_id="3021", parent_item_id="it_0")

    with pytest.raises(ValidationError, match="정확히 하나"):
        CommitAddItem(item_id="it_1")


def test_commit_add_allows_empty_content_for_template_slot():
    """템플릿 빈 슬롯과 카테고리 컨테이너는 content 없이 보낸다."""
    item = CommitAddItem(
        item_id="it_1",
        parent_id="3021",
        slot_id="PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE",
    )

    assert item.content is None


def test_commit_content_length_limit():
    """content는 공백 제외 1~500자다."""
    CommitAddItem(item_id="it_1", parent_id="3021", content="가" * 500)

    with pytest.raises(ValidationError, match="500자"):
        CommitAddItem(item_id="it_1", parent_id="3021", content="가" * 501)


def test_commit_content_rejects_whitespace_only():
    with pytest.raises(ValidationError, match="공백만"):
        CommitUpdateItem(item_id="it_2", target_id="3055", content="   ")


def test_commit_content_length_ignores_surrounding_whitespace():
    """공백을 제외하고 센다."""
    item = CommitAddItem(item_id="it_1", parent_id="3021", content="  " + "가" * 500 + "  ")

    assert item.content is not None


# ===== API 명세 4-2 예시와 대조 =====


def test_commit_items_match_spec_example():
    """API 명세 4-2의 예시 JSON이 그대로 직렬화된다."""
    add = CommitAddItem(
        item_id="it_1",
        parent_id="3021",
        parent_item_id=None,
        section_kind=None,
        slot_id="PROBLEM_SOLVING.SUMMARY",
        content="결제 모듈 타임아웃으로 주문 실패율이 12%까지 올랐다.",
        after_id=None,
    )
    update = CommitUpdateItem(
        item_id="it_2",
        target_id="3055",
        content="원인은 외부 PG사 응답 지연이었고 로그 분석으로 확인했다.",
    )

    assert add.model_dump() == {
        "item_id": "it_1",
        "action": "add",
        "parent_id": "3021",
        "parent_item_id": None,
        "section_kind": None,
        "slot_id": "PROBLEM_SOLVING.SUMMARY",
        "content": "결제 모듈 타임아웃으로 주문 실패율이 12%까지 올랐다.",
        "after_id": None,
    }
    assert update.model_dump() == {
        "item_id": "it_2",
        "action": "update",
        "target_id": "3055",
        "content": "원인은 외부 PG사 응답 지연이었고 로그 분석으로 확인했다.",
    }


def test_commit_result_carries_dropped_items():
    """dropped는 AI 서버가 채운다. 커밋 API 응답에는 없다."""
    result = CommitResult(
        request_id="550e8400-e29b-41d4-a716-446655440000",
        previous_version=42,
        map_version=43,
        revert_to_version=42,
        can_revert=True,
        applied=[{"item_id": "it_1", "block_id": "3701", "path": "교내 커머스 리뉴얼 > 문제해결"}],
        dropped=[{"item_id": "it_9", "reason": "validation_retry_exceeded"}],
    )

    assert result.dropped[0].reason == "validation_retry_exceeded"
    assert result.applied[0].path == "교내 커머스 리뉴얼 > 문제해결"


def test_active_gap_matches_spec_example():
    """API 명세 3-2의 active_gap 예시와 일치한다."""
    gap = ActiveGap(
        gap_id="550e8400-e29b-41d4-a716-446655440000",
        gap_type="extend_block",
        anchor_block_id="3055",
        message="그 해결 방법을 고른 기준이 무엇이었나요?",
        created_request_id="550e8400-e29b-41d4-a716-446655440000",
    )

    assert gap.model_dump() == {
        "gap_id": "550e8400-e29b-41d4-a716-446655440000",
        "gap_type": "extend_block",
        "anchor_block_id": "3055",
        "message": "그 해결 방법을 고른 기준이 무엇이었나요?",
        "created_request_id": "550e8400-e29b-41d4-a716-446655440000",
    }


def test_active_gap_rejects_unknown_type():
    with pytest.raises(ValidationError):
        ActiveGap(
            gap_id="550e8400-e29b-41d4-a716-446655440000",
            gap_type="rewrite_block",
            anchor_block_id="3055",
            message="?",
            created_request_id="550e8400-e29b-41d4-a716-446655440000",
        )
