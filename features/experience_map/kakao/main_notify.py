"""카톡 턴의 메인 서버 호출 (완료 통지·실패 한도 복구)

호출 성공 판정은 HTTP 상태와 `CommonResponse.isSuccess` 를 **함께** 본다. 실패해도
예외를 올리지 않고 `False` 를 돌려준다 — 호출자가 영속 재시도 대상으로 남긴다.
두 API 모두 멱등이라 여러 번 불러도 안전하다.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from common.http_client import get_http_client
from features.experience_map.main_client import USAGE_FAILED_PATH

logger = logging.getLogger(__name__)

TURN_COMPLETE_PATH = "/api/v1/kakao/turn-complete"

HttpRequest = Callable[..., Awaitable[httpx.Response]]


class KakaoMainNotifier:
    """메인 서버에 턴 결과를 알린다."""

    def __init__(self, *, request: HttpRequest | None = None) -> None:
        self._request = request

    async def report_failed_usage(self, *, user_id: str, request_id: str) -> bool:
        """선차감한 사용량을 되돌린다. 성공 여부만 돌려준다."""
        return await self._post(
            USAGE_FAILED_PATH, {"user_id": user_id, "request_id": request_id}, request_id
        )

    async def notify_turn_complete(
        self, *, user_id: str, request_id: str, outcome: str, delivery_status: str
    ) -> bool:
        """턴 완료를 알린다. `EXPIRED` 는 호출 전에 `FAILED` 로 바꿔 넘긴다."""
        return await self._post(
            TURN_COMPLETE_PATH,
            {
                "user_id": user_id,
                "request_id": request_id,
                "outcome": outcome,
                "delivery_status": delivery_status,
            },
            request_id,
        )

    async def _post(self, path: str, body: dict[str, Any], request_id: str) -> bool:
        try:
            request = self._request or get_http_client().request
            response = await request("POST", path, json=body)
        except Exception as exc:
            logger.warning(
                "메인 서버 호출 실패 (path=%s, request_id=%s, %s)",
                path,
                request_id,
                type(exc).__name__,
            )
            return False

        if not response.is_success:
            logger.warning(
                "메인 서버 호출 거절 (path=%s, request_id=%s, HTTP %s)",
                path,
                request_id,
                response.status_code,
            )
            return False
        try:
            parsed = response.json()
        except Exception:
            # 본문 없는 2xx 는 envelope 계약 위반이라 성공으로 보지 않는다.
            logger.warning("메인 서버 응답을 해석할 수 없습니다 (path=%s)", path)
            return False
        if isinstance(parsed, dict) and parsed.get("isSuccess") is False:
            logger.warning("메인 서버 isSuccess=false (path=%s, request_id=%s)", path, request_id)
            return False
        return True
