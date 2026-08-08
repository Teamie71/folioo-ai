"""경험정리 티켓 검증 미들웨어

프론트 → AI 직결 경로를 보호한다. `POST /sessions`(메인 서버 호출)는 대상이 아니며
`X-API-Key`를 그대로 쓴다.

**순수 ASGI 미들웨어다.** `BaseHTTPMiddleware`는 요청 body를 감싸는 과정에서 스트림에
관여하는데, 여기서는 `receive`를 **한 번도 호출하지 않고** 거부할 수 있어야 한다.
파일 업로드가 프론트 → AI 직결이라 요청당 최대 30MB가 직접 들어온다. 검증 전에
버퍼링하면 인증되지 않은 호출자가 그만큼 밀어넣을 수 있다 (API 명세 2-1).
"""

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from features.experience_map.errors import (
    ExperienceMapError,
    TicketInvalidError,
)
from features.experience_map.rate_limit import SlidingWindowRateLimiter
from features.experience_map.ticket import extract_bearer_token, verify_ticket

logger = logging.getLogger(__name__)

# 이 prefix 아래 경로는 티켓으로 인증한다.
# `POST /api/v1/experience-map/sessions`(끝에 슬래시 없음)는 포함되지 않는다.
TICKET_PATH_PREFIX = "/api/v1/experience-map/sessions/"

RATE_LIMIT_STATUS = 429
RATE_LIMIT_CODE = "rate_limited"


def requires_ticket(path: str) -> bool:
    """티켓 검증 대상 경로인지 판정한다."""
    return path.startswith(TICKET_PATH_PREFIX)


def extract_session_id(path: str) -> str | None:
    """경로에서 `{session_id}`를 꺼낸다."""
    remainder = path[len(TICKET_PATH_PREFIX) :]
    session_id = remainder.split("/", 1)[0]
    return session_id or None


class ExperienceMapTicketMiddleware:
    """티켓 서명·만료·`sid` 일치를 검증하고 사용자 단위 rate limit을 건다."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        secret_provider,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self.app = app
        self._secret_provider = secret_provider
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not requires_ticket(path):
            await self.app(scope, receive, send)
            return

        # preflight는 인증 헤더를 싣지 않는다. CORS 미들웨어가 처리한다.
        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        session_id = extract_session_id(path)
        if session_id is None:
            await self.app(scope, receive, send)
            return

        secret = self._secret_provider()
        if not secret:
            logger.error("EXPMAP_TICKET_SECRET이 설정되지 않았습니다.")
            await self._send_error(
                scope,
                send,
                status_code=500,
                code="internal_error",
                message="티켓 검증을 사용할 수 없습니다.",
            )
            return

        try:
            authorization = _header(scope, b"authorization")
            token = extract_bearer_token(authorization)
            payload = verify_ticket(token, secret, session_id)
        except ExperienceMapError as exc:
            await self._send_error(
                scope,
                send,
                status_code=exc.status_code,
                code=exc.code,
                message=exc.message,
            )
            return
        except Exception:
            logger.exception("티켓 검증 중 예기치 못한 오류")
            fallback = TicketInvalidError()
            await self._send_error(
                scope,
                send,
                status_code=fallback.status_code,
                code=fallback.code,
                message=fallback.message,
            )
            return

        retry_after = self._rate_limiter.check(payload.sub)
        if retry_after is not None:
            await self._send_error(
                scope,
                send,
                status_code=RATE_LIMIT_STATUS,
                code=RATE_LIMIT_CODE,
                message=f"요청이 너무 잦습니다. {retry_after}초 후 다시 시도해 주세요.",
                headers={"retry-after": str(retry_after)},
            )
            return

        # 엔드포인트가 세션 테이블을 역조회하지 않고 바로 쓸 수 있게 한다.
        scope.setdefault("state", {})
        scope["state"]["experience_map_user_id"] = payload.sub
        scope["state"]["experience_map_session_id"] = payload.sid

        await self.app(scope, receive, send)

    async def _send_error(
        self,
        scope: Scope,
        send: Send,
        *,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        """`receive`를 호출하지 않고 오류 응답을 보낸다."""
        response = JSONResponse(
            status_code=status_code,
            content={"statusCode": status_code, "code": code, "message": message},
            headers=headers,
        )
        await response(scope, _never_receive, send)


def _header(scope: Scope, name: bytes) -> str | None:
    """ASGI scope에서 헤더 값을 읽는다."""
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


async def _never_receive() -> dict:
    """오류 응답 경로에서 body를 읽지 않는다는 것을 명시한다."""
    raise AssertionError("오류 응답은 요청 body를 읽지 않습니다.")
