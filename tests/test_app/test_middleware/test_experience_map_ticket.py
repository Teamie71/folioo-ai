"""경험정리 티켓 미들웨어 테스트"""

import time

import jwt
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.auth import is_ticket_auth_path
from app.middleware.experience_map_ticket import (
    ExperienceMapTicketMiddleware,
    extract_session_id,
    requires_ticket,
)
from features.experience_map.rate_limit import SlidingWindowRateLimiter

SECRET = "ticket-signing-secret-with-32plus-bytes"
SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
OTHER_SESSION_ID = "11111111-2222-3333-4444-555555555555"
STREAM_PATH = f"/api/v1/experience-map/sessions/{SESSION_ID}/chat/stream"


def make_ticket(*, sub="123", sid=SESSION_ID, secret=SECRET, expires_in=300) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "sid": sid, "iat": now, "exp": now + expires_in},
        secret,
        algorithm="HS256",
    )


async def _echo(request: Request) -> JSONResponse:
    """인증을 통과했을 때만 도달한다. body를 읽는다."""
    body = await request.body()
    return JSONResponse(
        {
            "user_id": request.scope["state"]["experience_map_user_id"],
            "body_size": len(body),
        }
    )


def build_client(
    *,
    secret: str | None = SECRET,
    rate_limiter: SlidingWindowRateLimiter | None = None,
) -> TestClient:
    app = Starlette(
        routes=[
            Route(STREAM_PATH, _echo, methods=["POST"]),
            Route("/api/v1/experience-map/sessions", _echo, methods=["POST"]),
        ]
    )
    app.add_middleware(
        ExperienceMapTicketMiddleware,
        secret_provider=lambda: secret,
        rate_limiter=rate_limiter,
    )
    return TestClient(app)


# ===== 경로 판정 =====


def test_requires_ticket_for_session_scoped_paths():
    assert requires_ticket(STREAM_PATH)
    assert requires_ticket(f"/api/v1/experience-map/sessions/{SESSION_ID}/state")


def test_create_session_does_not_require_ticket():
    """POST /sessions 는 메인 서버가 X-API-Key 로 호출한다."""
    assert not requires_ticket("/api/v1/experience-map/sessions")


def test_other_paths_are_untouched():
    assert not requires_ticket("/health")
    assert not requires_ticket("/api/v1/interview/sessions/abc")


def test_extract_session_id():
    assert extract_session_id(STREAM_PATH) == SESSION_ID


def test_api_key_middleware_defers_ticket_paths():
    """두 미들웨어의 담당 경로가 일치해야 한다."""
    assert is_ticket_auth_path(STREAM_PATH)
    assert not is_ticket_auth_path("/api/v1/experience-map/sessions")


# ===== 검증 결과 =====


def test_valid_ticket_passes_and_exposes_user_id():
    client = build_client()

    response = client.post(
        STREAM_PATH,
        headers={"Authorization": f"Bearer {make_ticket()}"},
        content=b"payload",
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": "123", "body_size": 7}


@pytest.mark.parametrize(
    "headers,status,code",
    [
        ({}, 401, "ticket_invalid"),
        ({"Authorization": "Bearer garbage"}, 401, "ticket_invalid"),
        ({"Authorization": "Basic dXNlcg=="}, 401, "ticket_invalid"),
    ],
)
def test_rejects_missing_or_invalid_ticket(headers, status, code):
    client = build_client()

    response = client.post(STREAM_PATH, headers=headers, content=b"payload")

    assert response.status_code == status
    assert response.json()["code"] == code


def test_rejects_forged_signature():
    client = build_client()

    response = client.post(
        STREAM_PATH,
        headers={
            "Authorization": f"Bearer {make_ticket(secret='wrong-secret-with-32plus-bytes!!!')}"
        },
        content=b"payload",
    )

    assert response.status_code == 401
    assert response.json()["code"] == "ticket_invalid"


def test_rejects_expired_ticket():
    client = build_client()

    response = client.post(
        STREAM_PATH,
        headers={"Authorization": f"Bearer {make_ticket(expires_in=-1)}"},
        content=b"payload",
    )

    assert response.status_code == 401
    assert response.json()["code"] == "ticket_expired"


def test_rejects_session_mismatch():
    """서명이 유효해도 다른 세션 경로에는 쓸 수 없다."""
    client = build_client()

    response = client.post(
        STREAM_PATH,
        headers={"Authorization": f"Bearer {make_ticket(sid=OTHER_SESSION_ID)}"},
        content=b"payload",
    )

    assert response.status_code == 403
    assert response.json()["code"] == "session_forbidden"


def test_error_response_uses_spec_format():
    """API 명세 2-3 의 오류 JSON 형식을 쓴다."""
    client = build_client()

    response = client.post(STREAM_PATH, content=b"payload")

    assert set(response.json()) == {"statusCode", "code", "message"}
    assert response.json()["statusCode"] == 401


def test_missing_secret_is_server_error():
    client = build_client(secret=None)

    response = client.post(
        STREAM_PATH, headers={"Authorization": f"Bearer {make_ticket()}"}, content=b"x"
    )

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"


# ===== body 를 읽기 전에 거부 =====


def test_rejects_without_reading_body():
    """파일 업로드가 프론트 직결이라 검증 전에 버퍼링하면 안 된다.

    거부 경로에서 `receive`가 호출되면 AssertionError 가 난다.
    """
    client = build_client()
    huge_body = b"0" * (5 * 1024 * 1024)

    response = client.post(
        STREAM_PATH,
        headers={"Authorization": "Bearer forged"},
        content=huge_body,
    )

    assert response.status_code == 401
    assert response.json()["code"] == "ticket_invalid"


# ===== rate limit =====


def test_rate_limit_returns_429_with_retry_after():
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
    client = build_client(rate_limiter=limiter)
    headers = {"Authorization": f"Bearer {make_ticket()}"}

    for _ in range(2):
        assert client.post(STREAM_PATH, headers=headers, content=b"x").status_code == 200

    response = client.post(STREAM_PATH, headers=headers, content=b"x")

    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) > 0


def test_rate_limit_is_per_user():
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    client = build_client(rate_limiter=limiter)

    first = client.post(
        STREAM_PATH, headers={"Authorization": f"Bearer {make_ticket(sub='123')}"}, content=b"x"
    )
    second = client.post(
        STREAM_PATH, headers={"Authorization": f"Bearer {make_ticket(sub='456')}"}, content=b"x"
    )

    assert first.status_code == 200
    assert second.status_code == 200


def test_invalid_ticket_does_not_consume_rate_limit():
    """인증 실패가 정상 사용자의 한도를 깎으면 안 된다."""
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    client = build_client(rate_limiter=limiter)

    client.post(STREAM_PATH, headers={"Authorization": "Bearer forged"}, content=b"x")
    response = client.post(
        STREAM_PATH, headers={"Authorization": f"Bearer {make_ticket()}"}, content=b"x"
    )

    assert response.status_code == 200
