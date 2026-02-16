"""SSE 이벤트 타입 상수 및 스트리밍 노드 설정"""

from enum import StrEnum


class SSEEventType(StrEnum):
    """SSE 이벤트 타입 (event 필드에 사용)"""

    CONTENT_BLOCK_DELTA = "content_block_delta"
    RETRIEVER_RESULT = "retriever_result"
    MESSAGE_COMPLETE = "message_complete"
    ERROR = "error"
    PING = "ping"


class SSEDeltaType(StrEnum):
    """SSE 델타 내부 타입"""

    TEXT_DELTA = "text_delta"


class SSEErrorCode(StrEnum):
    """SSE 에러 코드"""

    SESSION_NOT_FOUND = "session_not_found"
    FINAL_STATE_MISSING = "final_state_missing"
    LLM_ERROR = "llm_error"


class LangGraphEventType(StrEnum):
    """LangGraph astream_events에서 사용하는 이벤트 타입"""

    ON_CHAT_MODEL_STREAM = "on_chat_model_stream"
    ON_CHAIN_END = "on_chain_end"


# 스트리밍 대상 노드 (다중 노드 지원)
STREAMING_TARGET_NODES: frozenset[str] = frozenset({"question_generator"})
