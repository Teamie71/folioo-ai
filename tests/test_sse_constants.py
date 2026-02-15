"""SSE 상수 모듈 테스트"""

from common.sse import (
    STREAMING_TARGET_NODES,
    LangGraphEventType,
    SSEDeltaType,
    SSEErrorCode,
    SSEEventType,
)


# ===== SSEEventType 테스트 =====


def test_sse_event_type_content_block_delta():
    """content_block_delta 이벤트 타입 값 테스트"""
    assert SSEEventType.CONTENT_BLOCK_DELTA == "content_block_delta"


def test_sse_event_type_message_complete():
    """message_complete 이벤트 타입 값 테스트"""
    assert SSEEventType.MESSAGE_COMPLETE == "message_complete"


def test_sse_event_type_error():
    """error 이벤트 타입 값 테스트"""
    assert SSEEventType.ERROR == "error"


def test_sse_event_type_ping():
    """ping 이벤트 타입 값 테스트"""
    assert SSEEventType.PING == "ping"


def test_sse_event_type_is_str():
    """SSEEventType은 str과 비교 가능해야 함"""
    assert isinstance(SSEEventType.CONTENT_BLOCK_DELTA, str)
    assert isinstance(SSEEventType.ERROR, str)


# ===== SSEDeltaType 테스트 =====


def test_sse_delta_type_text_delta():
    """text_delta 델타 타입 값 테스트"""
    assert SSEDeltaType.TEXT_DELTA == "text_delta"


# ===== SSEErrorCode 테스트 =====


def test_sse_error_code_session_not_found():
    """session_not_found 에러 코드 테스트"""
    assert SSEErrorCode.SESSION_NOT_FOUND == "session_not_found"


def test_sse_error_code_final_state_missing():
    """final_state_missing 에러 코드 테스트"""
    assert SSEErrorCode.FINAL_STATE_MISSING == "final_state_missing"


def test_sse_error_code_llm_error():
    """llm_error 에러 코드 테스트"""
    assert SSEErrorCode.LLM_ERROR == "llm_error"


# ===== LangGraphEventType 테스트 =====


def test_langgraph_event_type_on_chat_model_stream():
    """on_chat_model_stream LangGraph 이벤트 타입 테스트"""
    assert LangGraphEventType.ON_CHAT_MODEL_STREAM == "on_chat_model_stream"


# ===== STREAMING_TARGET_NODES 테스트 =====


def test_streaming_target_nodes_contains_question_generator():
    """스트리밍 대상 노드에 question_generator가 포함되어 있어야 함"""
    assert "question_generator" in STREAMING_TARGET_NODES


def test_streaming_target_nodes_is_frozenset():
    """STREAMING_TARGET_NODES는 frozenset이어야 함 (불변)"""
    assert isinstance(STREAMING_TARGET_NODES, frozenset)


def test_streaming_target_nodes_membership_check():
    """노드 멤버십 검사가 동작해야 함 (다중 노드 지원 구조 검증)"""
    assert "question_generator" in STREAMING_TARGET_NODES
    assert "nonexistent_node" not in STREAMING_TARGET_NODES
