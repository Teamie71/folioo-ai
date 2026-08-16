"""validate 노드와 보정 loop 테스트 (에이전트 문서 5-7)."""

from features.experience_map.nodes.validate import next_node, validate_operations
from features.experience_map.state import start_turn

SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"


def make_state(**overrides):
    """정제 결과와 구조화 metadata가 일치하는 기본 state."""
    state = start_turn(
        {"user_id": "1", "session_id": SESSION_ID},
        request_id=REQUEST_ID,
        request_hash="a" * 64,
    )
    state.update(
        target_experience_alias="exp_1",
        alias_to_block_id={"exp_1": "101", "b_1": "305"},
        structured_items=[
            {
                "item_id": "it_1",
                "action": "add",
                "parent_ref": "b_1",
                "text": "원문",
            }
        ],
        refined_items=[{"item_id": "it_1", "refined_text": "정제된 내용"}],
    )
    state.update(overrides)
    return state


def test_valid_operations_are_ready_for_coordinator():
    result = validate_operations(make_state())

    assert result["commit_items"] == [
        {"item_id": "it_1", "action": "add", "parent_ref": "b_1", "text": "정제된 내용"}
    ]
    assert result["validation_errors"] == []
    assert next_node(result) == "coordinator"


def test_unknown_parent_routes_back_to_structure():
    result = validate_operations(
        make_state(
            structured_items=[
                {"item_id": "it_1", "action": "add", "parent_ref": "exp_999", "text": "원문"}
            ]
        )
    )

    assert result["repair_count"] == 1
    assert result["validation_errors"][0]["code"] == "unknown_parent"
    assert next_node(result) == "structure"


def test_too_long_content_routes_back_to_refine():
    result = validate_operations(
        make_state(refined_items=[{"item_id": "it_1", "refined_text": "가" * 501}])
    )

    assert result["validation_errors"][0]["repair_target"] == "refine"
    assert next_node(result) == "refine"


def test_item_set_mismatch_routes_back_to_structure():
    result = validate_operations(make_state(refined_items=[]))

    assert result["validation_errors"][0]["code"] == "item_set_mismatch"
    assert next_node(result) == "structure"


def test_third_validation_drops_only_invalid_item():
    result = validate_operations(
        make_state(
            repair_count=2,
            structured_items=[
                {"item_id": "good", "action": "add", "parent_ref": "b_1", "text": "원문"},
                {"item_id": "bad", "action": "add", "parent_ref": "exp_999", "text": "원문"},
            ],
            refined_items=[
                {"item_id": "good", "refined_text": "정상"},
                {"item_id": "bad", "refined_text": "오류"},
            ],
        )
    )

    assert [item["item_id"] for item in result["commit_items"]] == ["good"]
    assert result["dropped_items"] == [{"item_id": "bad", "reason": "validation_retry_exceeded"}]
    assert next_node(result) == "coordinator"


def test_gap_update_metadata_is_validated_with_structure_items():
    result = validate_operations(
        make_state(
            gap_update_item={
                "item_id": "gap_update:305",
                "action": "update",
                "target_ref": "b_1",
                "text": "기존 내용과 답변",
            },
            refined_items=[
                {"item_id": "it_1", "refined_text": "정제된 내용"},
                {"item_id": "gap_update:305", "refined_text": "결합 정제 결과"},
            ],
        )
    )

    assert result["commit_items"][-1] == {
        "item_id": "gap_update:305",
        "action": "update",
        "target_ref": "b_1",
        "text": "결합 정제 결과",
    }
