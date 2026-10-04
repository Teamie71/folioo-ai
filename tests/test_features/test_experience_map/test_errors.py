"""경험정리 오류 매핑 테스트"""

import pytest

from features.experience_map import errors


@pytest.mark.parametrize(
    "exc_class,status,code",
    [
        (errors.UnauthorizedError, 401, "unauthorized"),
        (errors.TicketInvalidError, 401, "ticket_invalid"),
        (errors.TicketExpiredError, 401, "ticket_expired"),
        (errors.SessionForbiddenError, 403, "session_forbidden"),
        (errors.SessionNotFoundError, 404, "session_not_found"),
        (errors.RequestNotFoundError, 404, "request_not_found"),
        (errors.MapNotInitializedError, 404, "map_not_initialized"),
        (errors.SessionBusyError, 409, "session_busy"),
        (errors.IdempotencyKeyReusedError, 409, "idempotency_key_reused"),
        (errors.RetryNotAllowedError, 409, "retry_not_allowed"),
        (errors.RetryExpiredError, 410, "retry_expired"),
        (errors.FileTooLargeError, 413, "file_too_large"),
        (errors.UnsupportedFileTypeError, 415, "unsupported_file_type"),
        (errors.InvalidRequestError, 422, "invalid_request"),
    ],
)
def test_http_error_mapping(exc_class, status, code):
    """API 명세 2-3의 HTTP 상태 코드와 code가 일치한다."""
    response = exc_class().to_response()

    assert response.statusCode == status
    assert response.code == code
    assert response.message


def test_error_response_matches_spec_example():
    """API 명세 2-3 예시 JSON 형식과 일치한다."""
    response = errors.SessionBusyError().to_response()

    assert response.model_dump() == {
        "statusCode": 409,
        "code": "session_busy",
        "message": "다른 요청을 처리 중입니다.",
    }


@pytest.mark.parametrize(
    "exc_class,sse_code,retryable",
    [
        (errors.ValidationFailedError, "validation_failed", True),
        (errors.CommitConflictError, "commit_conflict", True),
        (errors.LlmError, "llm_error", True),
        (errors.NodeTimeoutError, "node_timeout", True),
        (errors.DbConstraintViolationError, "db_constraint_violation", False),
    ],
)
def test_sse_error_mapping(exc_class, sse_code, retryable):
    """API 명세 6절의 SSE code와 재시도 가능 여부가 일치한다."""
    payload = exc_class(failed_node="refine").to_sse_error()

    assert payload.code == sse_code
    assert payload.retryable is retryable
    assert payload.failed_node == "refine"


def test_sse_error_matches_spec_example():
    payload = errors.LlmError("문장 정제에 실패했습니다.", failed_node="refine").to_sse_error()

    assert payload.model_dump() == {
        "code": "llm_error",
        "failed_node": "refine",
        "retryable": True,
        "message": "문장 정제에 실패했습니다.",
    }


def test_db_constraint_violation_is_not_retryable():
    """재시도해도 같은 결과라 재시도 버튼을 노출하지 않는다."""
    assert errors.DbConstraintViolationError().retryable is False


def test_http_only_errors_are_not_retryable():
    """SSE code가 없는 오류는 재시도 대상이 아니다."""
    assert errors.SessionBusyError().retryable is False


def test_custom_message_overrides_default():
    exc = errors.InvalidRequestError("request_id는 UUID 문자열이어야 합니다.")

    assert exc.to_response().message == "request_id는 UUID 문자열이어야 합니다."


def test_all_errors_inherit_base():
    """미들웨어가 한 타입으로 잡을 수 있어야 한다."""
    assert issubclass(errors.TicketExpiredError, errors.ExperienceMapError)
    assert issubclass(errors.LlmError, errors.ExperienceMapError)
