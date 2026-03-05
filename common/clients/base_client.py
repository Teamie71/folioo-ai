"""메인 서버 API 호출을 위한 httpx 기반 베이스 클라이언트"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)


class MainServerError(Exception):
    """메인 서버 API 호출 실패 시 발생하는 예외"""

    def __init__(self, status_code: int, detail: str, error_code: str | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        super().__init__(f"[{status_code}] {detail}")


class BaseClient:
    """
    메인 서버(NestJS) 호출을 위한 httpx 비동기 클라이언트 베이스 클래스

    - 인증 헤더(X-API-Key) 자동 첨부
    - 응답 에러를 MainServerError로 변환
    - 생명주기(open/close) 관리 지원
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base_url = (base_url or os.getenv("MAIN_BACKEND_URL", "")).rstrip("/")
        self._api_key = api_key or os.getenv("MAIN_BACKEND_API_KEY", "")
        self._timeout = timeout or float(os.getenv("MAIN_BACKEND_TIMEOUT", "30"))

        if not self._base_url:
            raise ValueError("MAIN_BACKEND_URL이 설정되지 않았습니다.")
        if not self._api_key:
            raise ValueError("MAIN_BACKEND_API_KEY가 설정되지 않았습니다.")

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "X-API-Key": self._api_key,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self._timeout),
        )

    async def close(self) -> None:
        """httpx 클라이언트 리소스 정리"""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | list | None = None,
        params: dict | None = None,
    ) -> dict | list | None:
        """
        HTTP 요청을 보내고 응답을 반환한다.

        Args:
            method: HTTP 메서드 (GET, POST, PATCH, PUT, DELETE)
            path: 요청 경로 (base_url 이후)
            json: 요청 본문 (JSON)
            params: 쿼리 파라미터

        Returns:
            응답 JSON (dict/list) 또는 None (204 등)

        Raises:
            MainServerError: HTTP 에러 응답 시
        """
        try:
            response = await self._client.request(
                method,
                path,
                json=json,
                params=params,
            )
        except httpx.TimeoutException as exc:
            raise MainServerError(
                status_code=504,
                detail="메인 서버 요청 시간이 초과되었습니다.",
            ) from exc
        except httpx.ConnectError as exc:
            raise MainServerError(
                status_code=502,
                detail="메인 서버에 연결할 수 없습니다.",
            ) from exc

        if response.status_code == 204:
            return None

        if response.is_success:
            return response.json()

        error_body = {}
        try:
            error_body = response.json()
        except Exception:
            pass

        error_code = error_body.get("errorCode") or error_body.get("code")
        detail = error_body.get("message") or error_body.get("detail") or response.text

        raise MainServerError(
            status_code=response.status_code,
            detail=detail,
            error_code=error_code,
        )

    async def get(self, path: str, *, params: dict | None = None) -> dict | list | None:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, *, json: dict | list | None = None) -> dict | list | None:
        return await self._request("POST", path, json=json)

    async def patch(self, path: str, *, json: dict | list | None = None) -> dict | list | None:
        return await self._request("PATCH", path, json=json)

    async def put(self, path: str, *, json: dict | list | None = None) -> dict | list | None:
        return await self._request("PUT", path, json=json)

    async def delete(self, path: str) -> dict | list | None:
        return await self._request("DELETE", path)
