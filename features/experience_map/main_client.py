"""경험정리 메인 서버 커밋 API 클라이언트

경험 맵 쓰기는 메인 서버만 수행한다. 이 모듈은 HTTP 계약과 오류 분류만 담당하며,
version 충돌 뒤 그래프를 어디서부터 다시 실행할지는 3.18 coordinator의 책임이다.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from common.http_client import MainServerError, get_http_client
from features.experience_map.errors import (
    CommitRequestIdReusedError,
    MapNotInitializedError,
    MapVersionConflictError,
    UnknownSlotIdError,
)
from features.experience_map.schemas import CommitItem, CommitResult
from features.experience_map.templates import TemplateCatalogClient, get_template_catalog_client

logger = logging.getLogger(__name__)

COMMIT_PATH = "/api/v1/experience-map/commit"
COMMIT_RECOVERY_PATH = "/api/v1/experience-map/commit/{request_id}"
USAGE_FAILED_PATH = "/api/v1/experience-map/usage/failed"

HttpRequest = Callable[..., Awaitable[httpx.Response]]

SERVER_ERROR_CODE_MAP = {
    "EXPERIENCE_MAP4091": "map_version_conflict",
    "EXPERIENCE_MAP4092": "request_id_reused",
    "EXPERIENCE_MAP4223": "unknown_slot_id",
    "EXPERIENCE_MAP404": "map_not_initialized",
}


class CommitRecoveryResult:
    """커밋 응답 유실 뒤 조회한 결과"""

    def __init__(self, committed: bool, result: CommitResult | None = None) -> None:
        self.committed = committed
        self.result = result


class ExperienceMapMainClient:
    """커밋·커밋 복구 API 클라이언트

    일반 HTTP 재시도 래퍼를 쓰지 않는다. 커밋은 쓰기 요청이라 네트워크 오류를
    자동 재전송하면 중복 반영 여부가 불분명해진다. 응답 유실은 `get_commit()`으로
    확인한다.
    """

    def __init__(
        self,
        *,
        request: HttpRequest | None = None,
        catalog_client: TemplateCatalogClient | None = None,
    ) -> None:
        self._request = request
        self._catalog_client = catalog_client or get_template_catalog_client()

    async def commit(
        self,
        *,
        user_id: str,
        request_id: str,
        base_map_version: int,
        items: list[CommitItem],
    ) -> CommitResult:
        """커밋 요청을 보낸다.

        `unknown_slot_id`만 카탈로그 강제 갱신 뒤 정확히 한 번 재시도한다.
        나머지 오류와 네트워크 오류는 호출자에게 그대로 넘긴다.
        """
        payload = {
            "user_id": user_id,
            "request_id": request_id,
            "base_map_version": base_map_version,
            "items": [item.model_dump(exclude_none=True) for item in items],
        }

        try:
            return await self._commit_once(payload)
        except UnknownSlotIdError:
            logger.warning("unknown_slot_id 수신 - 템플릿 카탈로그 강제 갱신")
            await self._catalog_client.refresh()
            return await self._commit_once(payload)

    async def report_failed_usage(self, *, user_id: str, request_id: str) -> None:
        """턴 실패를 메인 서버에 알려 티켓 발급 시점에 미리 차감한 사용량을 되돌린다.

        메인 서버는 티켓 발급 시점에 1회 선차감하고, 턴이 실패하면 이 API로
        알려줘야만 되돌린다 — 실패를 자체 감지하지 않는다. 멱등이라 여러 번
        불러도 안전하다.

        응답 본문 형식을 계약으로 삼지 않는다 — 성공(2xx)인지만 본다. 실패
        보고 자체가 실패해도 예외를 올리지 않는다 — 사용자에게 보여줄 오류
        이벤트(`_fail`)를 이 호출 하나 때문에 놓치면 안 된다. 대신 로그만
        남긴다.
        """
        try:
            request = self._request or get_http_client().request
            response = await request(
                "POST",
                USAGE_FAILED_PATH,
                json={"user_id": user_id, "request_id": request_id},
            )
            if not response.is_success:
                raise MainServerError(
                    response.status_code,
                    f"실패 사용량 보고 응답이 실패입니다: {response.text[:300]!r}",
                )
        except Exception:
            logger.exception(
                "실패 사용량 보고 실패 (user_id=%s, request_id=%s) — "
                "메인 서버 티켓 차감이 되돌려지지 않았을 수 있습니다.",
                user_id,
                request_id,
            )

    async def get_commit(self, request_id: str) -> CommitRecoveryResult:
        """응답 유실 뒤 request_id의 실제 커밋 여부를 조회한다."""
        raw_result = await self._request_result(
            "GET", COMMIT_RECOVERY_PATH.format(request_id=request_id)
        )
        if not isinstance(raw_result, dict):
            raise MainServerError(502, "커밋 복구 응답이 객체가 아닙니다.")

        committed = raw_result.get("committed")
        if not isinstance(committed, bool):
            raise MainServerError(502, "커밋 복구 응답에 committed가 없습니다.")
        if not committed:
            return CommitRecoveryResult(committed=False)

        raw_commit = raw_result.get("result")
        if not isinstance(raw_commit, dict):
            raise MainServerError(502, "완료된 커밋 복구 응답에 result가 없습니다.")
        return CommitRecoveryResult(
            committed=True, result=_to_commit_result(raw_commit, request_id)
        )

    async def _commit_once(self, payload: dict[str, Any]) -> CommitResult:
        result = await self._request_result("POST", COMMIT_PATH, json=payload)
        if not isinstance(result, dict):
            raise MainServerError(502, "커밋 응답이 객체가 아닙니다.")
        return _to_commit_result(result, str(payload["request_id"]))

    async def _request_result(self, method: str, path: str, **kwargs: Any) -> Any:
        request = self._request or get_http_client().request
        try:
            response = await request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise
        except httpx.NetworkError:
            raise

        body = _response_body(response)
        if response.is_success:
            return _success_result(body, response.status_code)
        _raise_commit_error(response.status_code, body)


def _response_body(response: httpx.Response) -> Any:
    """응답 JSON을 읽고 실패 시 공통 HTTP 오류로 바꾼다."""
    try:
        return response.json()
    except Exception as exc:
        raise MainServerError(
            response.status_code, "메인 서버 응답을 JSON으로 파싱할 수 없습니다."
        ) from exc


def _success_result(body: Any, status_code: int) -> Any:
    """기존 메인 서버 envelope과 명세의 직접 응답을 모두 수용한다."""
    if isinstance(body, dict) and "isSuccess" in body:
        if not body["isSuccess"]:
            _raise_commit_error(status_code, body)
        return body.get("result")
    return body


def _raise_commit_error(status_code: int, body: Any) -> None:
    """커밋 계약의 오류 코드를 타입 있는 예외로 올린다."""
    error = _error_body(body)
    raw_code = error.get("code") or error.get("errorCode")
    code = SERVER_ERROR_CODE_MAP.get(raw_code, raw_code) if isinstance(raw_code, str) else None
    if code == "map_version_conflict":
        details = error.get("details")
        current_version = error.get("current_map_version")
        if current_version is None and isinstance(details, dict):
            current_version = details.get("currentMapVersion")
        try:
            parsed_version = int(current_version) if current_version is not None else None
        except (TypeError, ValueError):
            parsed_version = None
        raise MapVersionConflictError(parsed_version)
    if code == "request_id_reused":
        raise CommitRequestIdReusedError()
    if code == "unknown_slot_id":
        raise UnknownSlotIdError()
    if code == "map_not_initialized":
        raise MapNotInitializedError()
    raise MainServerError(
        status_code,
        str(error.get("message") or error.get("reason") or "메인 서버 커밋 요청 실패"),
        error,
    )


def _error_body(body: Any) -> dict[str, Any]:
    """직접 오류와 기존 envelope 오류에서 code/message를 꺼낸다."""
    if not isinstance(body, dict):
        return {}
    error = body.get("error")
    if isinstance(error, dict):
        return error
    return body


def _to_commit_result(raw: dict[str, Any], request_id: str) -> CommitResult:
    """메인 응답에 SSE 전용 되돌리기 필드를 보완한다."""
    payload = {
        **raw,
        "request_id": raw.get("request_id", request_id),
        "revert_to_version": raw.get("revert_to_version", raw.get("previous_version")),
        "can_revert": raw.get("can_revert", True),
    }
    try:
        return CommitResult.model_validate(payload)
    except Exception as exc:
        raise MainServerError(502, "메인 서버 커밋 응답 형식이 올바르지 않습니다.") from exc


__all__ = [
    "COMMIT_PATH",
    "COMMIT_RECOVERY_PATH",
    "CommitRecoveryResult",
    "ExperienceMapMainClient",
]
