"""서비스 간 API Key 인증 미들웨어"""

import os
import secrets

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """`X-API-Key` 헤더를 검증하는 미들웨어"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)

        if path in {"/health", "/openapi.json"} or path.startswith("/docs"):
            return await call_next(request)

        expected_api_key = os.getenv("AI_SERVICE_API_KEY", "")
        if not expected_api_key:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "AI_SERVICE_API_KEY is not configured"},
            )

        provided_api_key = request.headers.get("X-API-Key")
        is_valid = (
            bool(provided_api_key)
            and secrets.compare_digest(provided_api_key, expected_api_key)
        )

        if not is_valid:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Unauthorized"},
            )

        return await call_next(request)
