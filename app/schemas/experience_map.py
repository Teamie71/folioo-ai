"""경험정리 API 스키마 정의

경로와 payload는 API 명세 5절(AI 서버 API)과 6절(SSE 이벤트)을 따른다.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from features.experience_map.schemas import (
    CommitResult,
    GapType,
    NodeName,
)

DECIMAL_ID_PATTERN = re.compile(r"^[1-9][0-9]*$")
UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

SessionStatus = Literal["ready", "running", "failed"]
RequestStatus = Literal["running", "completed", "failed"]
ResponseKind = Literal["result", "suggestion", "fallback"]
ViewKind = Literal["map", "list"]


def _require_uuid(value: str, field: str) -> str:
    if not UUID_PATTERN.match(value):
        raise ValueError(f"{field}는 UUID 문자열이어야 합니다.")
    return value


def _require_decimal_id(value: str, field: str) -> str:
    if not DECIMAL_ID_PATTERN.match(value):
        raise ValueError(f"{field}는 십진 문자열 ID여야 합니다.")
    return value


# ===== POST /sessions =====


class CreateSessionRequest(BaseModel):
    """메인 서버가 티켓 발급 과정에서 호출한다."""

    user_id: str = Field(..., description="십진 문자열 사용자 ID")

    @field_validator("user_id")
    @classmethod
    def _check_user_id(cls, v: str) -> str:
        return _require_decimal_id(v, "user_id")


class CreateSessionResponse(BaseModel):
    session_id: str
    status: SessionStatus


# ===== GET /sessions/{session_id}/state =====


class SessionStateResponse(BaseModel):
    """화면 새로고침·재접속 시 조회한다."""

    session_id: str
    status: SessionStatus
    active_request_id: str | None = None
    retryable: bool = False
    failed_node: NodeName | None = None


# ===== POST /sessions/{session_id}/chat/stream =====


class ChatStreamRequest(BaseModel):
    """multipart의 `request` part에 담기는 JSON"""

    request_id: str = Field(..., description="티켓과 함께 받은 UUID")
    user_message: str | None = Field(None, description="파일이 없으면 필수")
    context_experience_id: str | None = Field(
        None, description="현재 보고 있는 level 2 활동 block ID"
    )
    view: ViewKind | None = None

    @field_validator("request_id")
    @classmethod
    def _check_request_id(cls, v: str) -> str:
        return _require_uuid(v, "request_id")

    @field_validator("context_experience_id")
    @classmethod
    def _check_experience_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _require_decimal_id(v, "context_experience_id")

    def require_message_or_files(self, file_count: int) -> None:
        """메시지와 파일 중 하나 이상이 있어야 한다.

        파일 개수는 multipart 파싱 뒤에야 알 수 있어 모델 검증과 분리한다.
        """
        if file_count == 0 and not (self.user_message or "").strip():
            raise ValueError("메시지와 파일 중 하나 이상이 필요합니다.")


# ===== POST /sessions/{session_id}/retry/stream =====


class RetryStreamRequest(BaseModel):
    request_id: str

    @field_validator("request_id")
    @classmethod
    def _check_request_id(cls, v: str) -> str:
        return _require_uuid(v, "request_id")


# ===== GET /sessions/{session_id}/requests/{request_id} =====


class RequestErrorInfo(BaseModel):
    code: str
    failed_node: NodeName | None = None
    retryable: bool = False
    message: str


class SuggestionGap(BaseModel):
    """제안 gap. **`path`가 붙는 점이 `ActiveGap`과 다르다.**

    `ActiveGap`은 다음 턴을 위해 세션에 저장하는 형태고, 이쪽은 사용자에게 보내는
    형태다. 화면에 "어디에 대한 제안인지"를 보여주려면 경로가 필요하다.
    """

    gap_id: str
    gap_type: GapType
    anchor_block_id: str
    path: str
    message: str


class SuggestionInfo(BaseModel):
    """저장된 gap 제안. gap이 없으면 gap 필드만 null이다.

    SSE `suggestion_ready`로 보냈던 것과 같은 형태여야 한다. 단절 뒤 복구가
    스트림으로 받았을 내용과 달라지면 안 된다.
    """

    gap: SuggestionGap | None = None
    message: str


class RequestStateResponse(BaseModel):
    """SSE 단절 뒤 요청 상태와 저장 결과를 복구한다."""

    request_id: str
    status: RequestStatus
    result: CommitResult | None = None
    suggestion: SuggestionInfo | None = None
    error: RequestErrorInfo | None = None


# ===== SSE 이벤트 (API 명세 6절) =====


class ProcessingStartedEvent(BaseModel):
    type: Literal["processing_started"] = "processing_started"
    request_id: str


class NodeStatusEvent(BaseModel):
    type: Literal["node_status"] = "node_status"
    node: NodeName
    status: Literal["running", "completed", "failed"]
    phrase: str | None = Field(
        None, description="에이전트 문서 4절 노드별 고정 문구. running 상태에만 채운다."
    )


class CommitResultEvent(BaseModel):
    type: Literal["commit_result"] = "commit_result"
    result: CommitResult


class CompletedMessage(BaseModel):
    """`message_complete`의 message 필드"""

    request_id: str
    session_id: str
    response_kind: ResponseKind
    ai_response: str
    committed: bool
    map_version: int | None = None
    can_revert: bool = False


class MessageCompleteEvent(BaseModel):
    """**메시지 단위 종료**이지 스트림 종료가 아니다."""

    type: Literal["message_complete"] = "message_complete"
    message: CompletedMessage


class SuggestionReadyEvent(BaseModel):
    """gap이 없어도 분석에 성공했으면 `gap: null`로 전송한다."""

    type: Literal["suggestion_ready"] = "suggestion_ready"
    gap: SuggestionGap | None = None


class ProcessingCompleteEvent(BaseModel):
    type: Literal["processing_complete"] = "processing_complete"
    request_id: str
    status: RequestStatus


class ErrorEventPayload(BaseModel):
    code: str
    failed_node: NodeName | None = None
    retryable: bool = False
    message: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    error: ErrorEventPayload


class PingEvent(BaseModel):
    type: Literal["ping"] = "ping"


ExperienceMapEvent = (
    ProcessingStartedEvent
    | NodeStatusEvent
    | CommitResultEvent
    | MessageCompleteEvent
    | SuggestionReadyEvent
    | ProcessingCompleteEvent
    | ErrorEvent
    | PingEvent
)
