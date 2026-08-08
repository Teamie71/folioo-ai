"""경험정리 LangGraph state 테스트"""

import json

import pytest

from features.experience_map.config import CHECKPOINT_NAMESPACE
from features.experience_map.state import (
    CLEANUP_FIELDS,
    SESSION_FIELDS,
    TURN_FIELD_DEFAULTS,
    ExperienceMapState,
    build_thread_config,
    cleanup_after_success,
    record_node_failure,
    reset_turn_fields,
    start_turn,
)

SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"
NEXT_REQUEST_ID = "660e8400-e29b-41d4-a716-446655440001"


@pytest.fixture
def finished_turn() -> ExperienceMapState:
    """중간 산출물이 가득 찬 직전 턴 state"""
    return {
        "user_id": "123",
        "session_id": SESSION_ID,
        "turn_number": 3,
        "active_gap": {
            "gap_id": REQUEST_ID,
            "gap_type": "extend_block",
            "anchor_block_id": "3055",
            "message": "그 해결 방법을 고른 기준이 무엇이었나요?",
            "created_request_id": REQUEST_ID,
        },
        "request_id": REQUEST_ID,
        "request_hash": "hash-a",
        "user_message": "이전 턴 메시지",
        "intent": "chat_input",
        "structured_items": [{"item_id": "it_1"}],
        "refined_items": [{"item_id": "it_1", "refined_text": "정제됨"}],
        "commit_items": [{"item_id": "it_1"}],
        "repair_count": 2,
        "failed_node": "refine",
        "node_retry_count": {"refine": 1},
        "alias_to_block_id": {"b_20": "3020"},
    }


# ===== thread config =====


def test_build_thread_config_uses_session_id_as_thread():
    config = build_thread_config(SESSION_ID)

    assert config["configurable"]["thread_id"] == SESSION_ID
    assert config["configurable"]["checkpoint_ns"] == CHECKPOINT_NAMESPACE
    assert CHECKPOINT_NAMESPACE == "experience_map"


# ===== 턴 초기화 =====


def test_reset_keeps_session_fields(finished_turn):
    """세션 필드는 턴을 넘어 유지된다."""
    reset = reset_turn_fields(finished_turn)

    assert reset["user_id"] == "123"
    assert reset["session_id"] == SESSION_ID
    assert reset["turn_number"] == 3
    assert reset["active_gap"] is not None


def test_reset_clears_every_turn_field(finished_turn):
    """턴 필드는 하나도 남지 않는다."""
    reset = reset_turn_fields(finished_turn)

    for key, default in TURN_FIELD_DEFAULTS.items():
        assert reset[key] == default, f"{key}가 초기화되지 않았습니다."


def test_session_and_turn_fields_do_not_overlap():
    """세션 필드가 턴 초기화 대상에 들어가면 대화가 끊긴다."""
    assert SESSION_FIELDS.isdisjoint(TURN_FIELD_DEFAULTS)


def test_start_turn_does_not_leak_previous_intermediates(finished_turn):
    """이전 요청의 구조화 결과가 새 요청에 섞이지 않는다."""
    turn = start_turn(
        finished_turn,
        request_id=NEXT_REQUEST_ID,
        request_hash="hash-b",
        user_message="새 메시지",
    )

    assert turn["structured_items"] == []
    assert turn["refined_items"] == []
    assert turn["commit_items"] == []
    assert turn["repair_count"] == 0
    assert turn["failed_node"] is None
    assert turn["node_retry_count"] == {}
    assert turn["alias_to_block_id"] == {}


def test_start_turn_sets_input_and_increments_turn_number(finished_turn):
    turn = start_turn(
        finished_turn,
        request_id=NEXT_REQUEST_ID,
        request_hash="hash-b",
        user_message="새 메시지",
        context_experience_id="3021",
        view="map",
    )

    assert turn["request_id"] == NEXT_REQUEST_ID
    assert turn["user_message"] == "새 메시지"
    assert turn["context_experience_id"] == "3021"
    assert turn["view"] == "map"
    assert turn["turn_number"] == 4


def test_start_turn_preserves_active_gap(finished_turn):
    """직전 턴의 gap은 다음 턴의 필터링이 써야 하므로 유지된다."""
    turn = start_turn(finished_turn, request_id=NEXT_REQUEST_ID, request_hash="hash-b")

    assert turn["active_gap"]["gap_type"] == "extend_block"


def test_start_turn_from_empty_state():
    """첫 턴은 turn_number가 1이다."""
    turn = start_turn({}, request_id=REQUEST_ID, request_hash="hash-a")

    assert turn["turn_number"] == 1


def test_reset_does_not_share_mutable_defaults(finished_turn):
    """가변 기본값이 여러 state에 공유되면 한 요청이 다른 요청을 오염시킨다."""
    first = reset_turn_fields(finished_turn)
    second = reset_turn_fields(finished_turn)

    first["structured_items"].append({"item_id": "it_x"})

    assert second["structured_items"] == []
    assert TURN_FIELD_DEFAULTS["structured_items"] == []


def test_reset_returns_new_object(finished_turn):
    reset = reset_turn_fields(finished_turn)
    reset["user_message"] = "변경"

    assert finished_turn["user_message"] == "이전 턴 메시지"


# ===== 성공 후 정리 =====


def test_cleanup_clears_large_fields(finished_turn):
    cleaned = cleanup_after_success(finished_turn)

    for key in CLEANUP_FIELDS:
        assert cleaned[key] in ([], None, {}), f"{key}가 정리되지 않았습니다."


def test_cleanup_keeps_commit_result_and_gap(finished_turn):
    """커밋 결과와 gap은 남긴다."""
    cleaned = cleanup_after_success(finished_turn)

    assert cleaned["commit_items"] == [{"item_id": "it_1"}]
    assert cleaned["active_gap"] is not None
    assert cleaned["request_id"] == REQUEST_ID


def test_cleanup_targets_are_turn_fields():
    """정리 대상은 전부 턴 필드여야 한다. 세션 필드를 지우면 대화가 끊긴다."""
    for key in CLEANUP_FIELDS:
        assert key in TURN_FIELD_DEFAULTS
        assert key not in SESSION_FIELDS


# ===== 노드 실패 기록 =====


def test_record_node_failure_counts_per_node():
    state: ExperienceMapState = {"session_id": SESSION_ID}

    state = record_node_failure(state, "structure")
    state = record_node_failure(state, "structure")
    state = record_node_failure(state, "refine")

    assert state["failed_node"] == "refine"
    assert state["node_retry_count"] == {"structure": 2, "refine": 1}


def test_record_node_failure_does_not_mutate_input():
    original: ExperienceMapState = {"node_retry_count": {"structure": 1}}

    record_node_failure(original, "structure")

    assert original["node_retry_count"] == {"structure": 1}


# ===== 직렬화 =====


def test_state_is_json_serializable(finished_turn):
    """checkpoint에는 직렬화 가능한 값만 저장한다."""
    turn = start_turn(
        finished_turn,
        request_id=NEXT_REQUEST_ID,
        request_hash="hash-b",
        user_message="새 메시지",
    )
    turn["file_references"] = [
        {
            "file_id": "f_1",
            "filename": "portfolio.pdf",
            "content_type": "application/pdf",
            "file_size": 1024,
            "sha256": "abc",
            "gcs_object": "expmap/req/f_1",
        }
    ]
    turn["validation_errors"] = [
        {
            "item_id": "it_1",
            "code": "content_too_long",
            "message": "500자를 초과했습니다.",
            "repair_target": "refine",
        }
    ]

    restored = json.loads(json.dumps(turn, ensure_ascii=False))

    assert restored == turn


def test_turn_defaults_are_json_serializable():
    assert json.loads(json.dumps(TURN_FIELD_DEFAULTS)) == TURN_FIELD_DEFAULTS
