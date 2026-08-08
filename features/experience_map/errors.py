"""경험정리 오류 정의와 HTTP/SSE 매핑

두 경로가 있다 (API 명세 2-3).

- 스트림 **시작 전** 오류 → JSON 응답 (`to_response()`)
- 스트림 **시작 후** 오류 → `error` SSE 이벤트 (`to_sse_error()`)

같은 예외가 시점에 따라 다른 형태로 나가므로 한 곳에서 정의한다.
"""

from typing import Literal

from pydantic import BaseModel, Field

SseErrorCode = Literal[
    "validation_failed",
    "commit_conflict",
    "llm_error",
    "node_timeout",
    "db_constraint_violation",
]

# SSE error code별 사용자 재시도 가능 여부 (API 명세 6절)
SSE_RETRYABLE: dict[str, bool] = {
    "validation_failed": True,
    "commit_conflict": True,
    "llm_error": True,
    "node_timeout": True,
    "db_constraint_violation": False,
}


class ErrorResponse(BaseModel):
    """스트림 시작 전 JSON 오류 응답"""

    statusCode: int = Field(..., description="HTTP 상태 코드")  # noqa: N815
    code: str = Field(..., description="기계 판독용 오류 코드")
    message: str = Field(..., description="사용자에게 보여줄 메시지")


class SseErrorPayload(BaseModel):
    """`error` SSE 이벤트의 error 필드"""

    code: str
    failed_node: str | None = None
    retryable: bool = False
    message: str


class ExperienceMapError(Exception):
    """경험정리 기능 예외의 기반 클래스"""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "요청 처리 중 오류가 발생했습니다."
    sse_code: SseErrorCode | None = None
    failed_node: str | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        failed_node: str | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.failed_node = failed_node or self.__class__.failed_node
        super().__init__(self.message)

    @property
    def retryable(self) -> bool:
        """사용자 재시도 버튼 노출 여부"""
        if self.sse_code is None:
            return False
        return SSE_RETRYABLE.get(self.sse_code, False)

    def to_response(self) -> ErrorResponse:
        """스트림 시작 전 JSON 응답으로 변환"""
        return ErrorResponse(statusCode=self.status_code, code=self.code, message=self.message)

    def to_sse_error(self) -> SseErrorPayload:
        """`error` SSE 이벤트 payload로 변환"""
        return SseErrorPayload(
            code=self.sse_code or self.code,
            failed_node=self.failed_node,
            retryable=self.retryable,
            message=self.message,
        )


# ===== 401 / 403 인증 =====


class UnauthorizedError(ExperienceMapError):
    """X-API-Key 검증 실패"""

    status_code = 401
    code = "unauthorized"
    message = "인증에 실패했습니다."


class TicketInvalidError(ExperienceMapError):
    """티켓 서명 불일치"""

    status_code = 401
    code = "ticket_invalid"
    message = "유효하지 않은 티켓입니다."


class TicketExpiredError(ExperienceMapError):
    """티켓 만료"""

    status_code = 401
    code = "ticket_expired"
    message = "티켓이 만료되었습니다. 다시 시도해 주세요."


class SessionForbiddenError(ExperienceMapError):
    """티켓 `sid`와 path `session_id` 불일치. 세션 탈취를 막는다."""

    status_code = 403
    code = "session_forbidden"
    message = "해당 세션에 접근할 수 없습니다."


# ===== 404 =====


class SessionNotFoundError(ExperienceMapError):
    status_code = 404
    code = "session_not_found"
    message = "세션을 찾을 수 없습니다."


class RequestNotFoundError(ExperienceMapError):
    status_code = 404
    code = "request_not_found"
    message = "요청을 찾을 수 없습니다."


class MapNotInitializedError(ExperienceMapError):
    """메인 서버가 기본 제공 데이터를 아직 만들지 않은 상태"""

    status_code = 404
    code = "map_not_initialized"
    message = "경험 맵이 아직 준비되지 않았습니다."


# ===== 409 / 410 요청 상태 =====


class SessionBusyError(ExperienceMapError):
    """같은 세션에서 다른 요청이 실행 중"""

    status_code = 409
    code = "session_busy"
    message = "다른 요청을 처리 중입니다."


class IdempotencyKeyReusedError(ExperienceMapError):
    """같은 request_id에 다른 request_hash"""

    status_code = 409
    code = "idempotency_key_reused"
    message = "같은 요청 ID로 다른 내용이 전달되었습니다."


class RetryNotAllowedError(ExperienceMapError):
    """마지막 요청이 아니거나 failed 상태가 아님"""

    status_code = 409
    code = "retry_not_allowed"
    message = "재시도할 수 없는 요청입니다."


class RetryExpiredError(ExperienceMapError):
    """재시도 TTL 초과"""

    status_code = 410
    code = "retry_expired"
    message = "재시도 가능 시간이 지났습니다."


# ===== 413 / 415 / 422 업로드와 입력 =====


class FileTooLargeError(ExperienceMapError):
    status_code = 413
    code = "file_too_large"
    message = "파일 크기가 제한을 초과했습니다."


class UnsupportedFileTypeError(ExperienceMapError):
    status_code = 415
    code = "unsupported_file_type"
    message = "지원하지 않는 파일 형식입니다."


class InvalidRequestError(ExperienceMapError):
    status_code = 422
    code = "invalid_request"
    message = "요청 형식이 올바르지 않습니다."


# ===== 스트림 진행 중 실패 (SSE error 이벤트) =====


class ValidationFailedError(ExperienceMapError):
    """validate 보정 한도를 넘겨 요청 전체가 실패"""

    status_code = 422
    code = "validation_failed"
    sse_code = "validation_failed"
    message = "정리 결과 검증에 실패했습니다."


class CommitConflictError(ExperienceMapError):
    """map version 충돌 재구성이 두 번째로 실패"""

    status_code = 409
    code = "commit_conflict"
    sse_code = "commit_conflict"
    message = "경험 맵이 변경되어 반영하지 못했습니다. 다시 시도해 주세요."


class LlmError(ExperienceMapError):
    """LLM 호출 실패"""

    status_code = 502
    code = "llm_error"
    sse_code = "llm_error"
    message = "AI 처리 중 오류가 발생했습니다."


class NodeTimeoutError(ExperienceMapError):
    """노드 제한 시간 초과"""

    status_code = 504
    code = "node_timeout"
    sse_code = "node_timeout"
    message = "처리 시간이 초과되었습니다."


class DbConstraintViolationError(ExperienceMapError):
    """DB 제약 위반. 재시도해도 같은 결과라 재시도 버튼을 노출하지 않는다."""

    status_code = 500
    code = "db_constraint_violation"
    sse_code = "db_constraint_violation"
    message = "데이터 제약 조건을 위반했습니다."
