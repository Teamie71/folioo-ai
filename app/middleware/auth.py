"""서비스 간 API Key 인증 미들웨어"""

import logging
import os
import secrets

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

PUBLIC_EXEMPT_PATHS = {
    "/health",
    "/openapi.json",
}

DOCS_EXEMPT_PATHS = {
    "/docs",
    "/docs/",
    "/docs/oauth2-redirect",
    "/docs/oauth2-redirect/",
}

# 티켓(Bearer)으로 인증하는 경로. 프론트가 직접 호출하므로 X-API-Key를 요구하지 않는다.
# `POST /api/v1/experience-map/sessions`(끝에 슬래시 없음)는 메인 서버 호출이라 제외된다.
TICKET_AUTH_PATH_PREFIXES = ("/api/v1/experience-map/sessions/",)


def is_ticket_auth_path(path: str) -> bool:
    """티켓 미들웨어가 담당하는 경로인지 판정한다."""
    return path.startswith(TICKET_AUTH_PATH_PREFIXES)


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """`X-API-Key` 헤더를 검증하는 미들웨어"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)

        if path in PUBLIC_EXEMPT_PATHS or path in DOCS_EXEMPT_PATHS:
            return await call_next(request)

        # 티켓 미들웨어가 이미 검증했다. X-API-Key를 중복으로 요구하지 않는다.
        if is_ticket_auth_path(path):
            return await call_next(request)

        expected_api_key = os.getenv("AI_SERVICE_API_KEY", "")
        if not expected_api_key:
            logger.error(
                "AI_SERVICE_API_KEY 환경변수가 설정되지 않았습니다. [%s %s]",
                request.method,
                path,
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "AI_SERVICE_API_KEY is not configured"},
            )

        provided_api_key = request.headers.get("X-API-Key")
        is_valid = bool(provided_api_key) and secrets.compare_digest(
            provided_api_key, expected_api_key
        )

        if not is_valid:
            if not provided_api_key:
                logger.warning(
                    "401 Unauthorized: X-API-Key 헤더 누락 [%s %s] client=%s",
                    request.method,
                    path,
                    request.client.host if request.client else "unknown",
                )
            else:
                logger.warning(
                    "401 Unauthorized: X-API-Key 불일치 [%s %s] client=%s provided_key_prefix=%s",
                    request.method,
                    path,
                    request.client.host if request.client else "unknown",
                    provided_api_key[:4] + "****" if len(provided_api_key) >= 4 else "****",
                )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Unauthorized"},
            )

        return await call_next(request)
