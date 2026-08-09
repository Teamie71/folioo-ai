"""경험정리 API 스키마 테스트"""

import pytest
from pydantic import ValidationError

from app.schemas.experience_map import (
    ChatStreamRequest,
    CommitResultEvent,
    CreateSessionRequest,
    ErrorEvent,
    MessageCompleteEvent,
    ProcessingCompleteEvent,
    ProcessingStartedEvent,
    RequestStateResponse,
    RetryStreamRequest,
    SessionStateResponse,
    SuggestionReadyEvent,
)

REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"
SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"


# ===== 식별자 검증 (API 명세 2-2) =====


def test_create_session_requires_decimal_user_id():
    assert CreateSessionRequest(user_id="123").user_id == "123"

    with pytest.raises(ValidationError, match="십진 문자열"):
        CreateSessionRequest(user_id="user-123")


def test_chat_request_requires_uuid_request_id():
    with pytest.raises(ValidationError, match="UUID"):
        ChatStreamRequest(request_id="not-a-uuid", user_message="안녕")


def test_chat_request_requires_decimal_experience_id():
    with pytest.raises(ValidationError, match="십진 문자열"):
        ChatStreamRequest(
            request_id=REQUEST_ID,
            user_message="안녕",
            context_experience_id="exp_101",
        )


def test_chat_request_allows_null_experience_id():
    request = ChatStreamRequest(request_id=REQUEST_ID, user_message="안녕")

    assert request.context_experience_id is None
    assert request.view is None


def test_retry_request_requires_uuid():
    assert RetryStreamRequest(request_id=REQUEST_ID).request_id == REQUEST_ID

    with pytest.raises(ValidationError):
        RetryStreamRequest(request_id="1")


# ===== 조건부 필수: 메시지와 파일 =====


def test_chat_request_requires_message_when_no_files():
    request = ChatStreamRequest(request_id=REQUEST_ID)

    with pytest.raises(ValueError, match="하나 이상"):
        request.require_message_or_files(file_count=0)


def test_chat_request_allows_empty_message_with_files():
    """파일만 올리는 것도 허용한다."""
    request = ChatStreamRequest(request_id=REQUEST_ID)

    request.require_message_or_files(file_count=1)


def test_chat_request_rejects_whitespace_only_message():
    request = ChatStreamRequest(request_id=REQUEST_ID, user_message="   ")

    with pytest.raises(ValueError, match="하나 이상"):
        request.require_message_or_files(file_count=0)


# ===== SSE 이벤트가 명세 예시와 일치 (API 명세 6절) =====


def test_processing_started_event_matches_spec():
    event = ProcessingStartedEvent(request_id=REQUEST_ID)

    assert event.model_dump() == {"type": "processing_started", "request_id": REQUEST_ID}


def test_commit_result_event_matches_spec():
    event = CommitResultEvent(
        result={
            "request_id": REQUEST_ID,
            "previous_version": 42,
            "map_version": 43,
            "revert_to_version": 42,
            "can_revert": True,
            "applied": [
                {"item_id": "it_1", "block_id": "3701", "path": "교내 커머스 리뉴얼 > 문제해결"}
            ],
            "dropped": [{"item_id": "it_9", "reason": "validation_retry_exceeded"}],
        }
    )

    dumped = event.model_dump()
    assert dumped["type"] == "commit_result"
    assert dumped["result"]["map_version"] == 43
    assert dumped["result"]["applied"][0]["path"] == "교내 커머스 리뉴얼 > 문제해결"
    assert dumped["result"]["dropped"][0]["reason"] == "validation_retry_exceeded"


def test_message_complete_event_matches_spec():
    event = MessageCompleteEvent(
        message={
            "request_id": REQUEST_ID,
            "session_id": SESSION_ID,
            "response_kind": "result",
            "ai_response": "교내 커머스 리뉴얼 > 문제해결에 블록 1개를 추가했습니다.",
            "committed": True,
            "map_version": 43,
            "can_revert": True,
        }
    )

    assert event.model_dump()["message"]["response_kind"] == "result"


def test_fallback_message_is_not_committed():
    event = MessageCompleteEvent(
        message={
            "request_id": REQUEST_ID,
            "session_id": SESSION_ID,
            "response_kind": "fallback",
            "ai_response": "아직 지원하지 않는 기능이에요.",
            "committed": False,
        }
    )

    message = event.model_dump()["message"]
    assert message["committed"] is False
    assert message["map_version"] is None
    assert message["can_revert"] is False


def test_suggestion_ready_allows_null_gap():
    """분석에 성공했고 보완할 것이 없으면 gap이 null이다."""
    event = SuggestionReadyEvent()

    assert event.model_dump() == {"type": "suggestion_ready", "gap": None}


def test_suggestion_ready_with_gap_matches_spec():
    event = SuggestionReadyEvent(
        gap={
            "gap_id": REQUEST_ID,
            "gap_type": "extend_block",
            "anchor_block_id": "3055",
            "path": "교내 커머스 리뉴얼 > 문제해결",
            "message": "그 해결 방법을 고른 기준이 무엇이었나요?",
        }
    )

    assert event.model_dump()["gap"]["gap_type"] == "extend_block"


def test_error_event_matches_spec():
    event = ErrorEvent(
        error={
            "code": "llm_error",
            "failed_node": "refine",
            "retryable": True,
            "message": "문장 정제에 실패했습니다.",
        }
    )

    assert event.model_dump() == {
        "type": "error",
        "error": {
            "code": "llm_error",
            "failed_node": "refine",
            "retryable": True,
            "message": "문장 정제에 실패했습니다.",
        },
    }


def test_error_event_rejects_unknown_node():
    with pytest.raises(ValidationError):
        ErrorEvent(error={"code": "llm_error", "failed_node": "reranker", "message": "실패"})


def test_processing_complete_event_matches_spec():
    event = ProcessingCompleteEvent(request_id=REQUEST_ID, status="completed")

    assert event.model_dump() == {
        "type": "processing_complete",
        "request_id": REQUEST_ID,
        "status": "completed",
    }


# ===== 상태 조회 응답 =====


def test_session_state_response_matches_spec():
    response = SessionStateResponse(
        session_id=SESSION_ID,
        status="failed",
        active_request_id=REQUEST_ID,
        retryable=True,
        failed_node="structure",
    )

    assert response.model_dump() == {
        "session_id": SESSION_ID,
        "status": "failed",
        "active_request_id": REQUEST_ID,
        "retryable": True,
        "failed_node": "structure",
    }


def test_request_state_response_allows_running_with_result():
    """커밋 직후에는 running이면서 result가 있을 수 있다."""
    response = RequestStateResponse(
        request_id=REQUEST_ID,
        status="running",
        result={
            "request_id": REQUEST_ID,
            "previous_version": 42,
            "map_version": 43,
            "revert_to_version": 42,
            "can_revert": True,
        },
    )

    assert response.status == "running"
    assert response.result is not None
    assert response.suggestion is None
