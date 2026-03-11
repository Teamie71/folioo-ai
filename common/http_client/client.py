"""메인 서버 HTTP 클라이언트 (싱글톤, 재시도, envelope 파싱)"""

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

MAX_RETRIES = 3
LINEAR_BACKOFF_BASE = 1.0


class MainServerError(Exception):
    """메인 서버 API 호출 실패 시 발생하는 예외"""

    def __init__(self, status_code: int, message: str, error: dict[str, Any] | None = None):
        self.status_code = status_code
        self.message = message
        self.error = error
        super().__init__(f"[{status_code}] {message}")


def _get_response_text_excerpt(response: httpx.Response, limit: int = 300) -> str:
    """응답 본문을 로그/예외 메시지용으로 짧게 추출"""
    try:
        body_text = response.text
    except Exception:
        return ""

    if not isinstance(body_text, str):
        return ""

    body_text = body_text.strip()
    if not body_text:
        return ""

    if len(body_text) > limit:
        return f"{body_text[:limit]}..."
    return body_text


def _build_5xx_error_message(response: httpx.Response) -> str:
    """5xx 응답에서 디버깅 가능한 에러 메시지 구성"""
    base_message = f"서버 에러 (HTTP {response.status_code})"

    detail_message = ""
    try:
        body = response.json()
    except Exception:
        body = None

    if isinstance(body, dict):
        error_info = body.get("error")
        if isinstance(error_info, dict):
            detail_message = str(
                error_info.get("message")
                or error_info.get("reason")
                or error_info.get("errorCode")
                or ""
            ).strip()
        elif isinstance(error_info, str):
            detail_message = error_info.strip()

    response_excerpt = _get_response_text_excerpt(response)

    if detail_message and response_excerpt:
        return f"{base_message}: {detail_message} (응답 본문: {response_excerpt!r})"
    if detail_message:
        return f"{base_message}: {detail_message}"
    if response_excerpt:
        return f"{base_message} (응답 본문: {response_excerpt!r})"
    return base_message


def get_http_client() -> httpx.AsyncClient:
    """
    싱글톤 httpx.AsyncClient 반환

    Returns:
        httpx.AsyncClient: 설정된 HTTP 클라이언트

    Raises:
        RuntimeError: 클라이언트가 초기화되지 않은 경우
    """
    global _client

    if _client is not None and not _client.is_closed:
        return _client

    base_url = os.getenv("MAIN_BACKEND_URL")
    api_key = os.getenv("MAIN_BACKEND_API_KEY")

    if not base_url:
        raise RuntimeError("MAIN_BACKEND_URL 환경변수가 설정되지 않았습니다.")
    if not api_key:
        raise RuntimeError("MAIN_BACKEND_API_KEY 환경변수가 설정되지 않았습니다.")

    _client = httpx.AsyncClient(
        base_url=base_url,
        headers={"X-API-Key": api_key},
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
    )
    logger.info("HTTP 클라이언트 생성 완료 (base_url=%s)", base_url)
    return _client


async def close_http_client() -> None:
    """HTTP 클라이언트 종료"""
    global _client

    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("HTTP 클라이언트 종료 완료")


def _parse_envelope(response: httpx.Response) -> Any:
    """
    메인 서버 응답 envelope 파싱

    메인 서버 응답 형식: { timestamp, isSuccess, error, result }

    Args:
        response: httpx 응답 객체

    Returns:
        envelope 내부의 result 데이터

    Raises:
        MainServerError: isSuccess=false이거나 파싱 실패 시
    """
    if response.status_code == 401:
        raise MainServerError(
            status_code=401,
            message=f"인증 실패: X-API-Key를 확인하세요. (응답 본문: {response.text!r})",
        )

    if response.status_code == 403:
        raise MainServerError(
            status_code=403,
            message=f"권한 없음: 해당 리소스에 접근할 수 없습니다. (응답 본문: {response.text!r})",
        )

    try:
        body = response.json()
    except Exception as e:
        raise MainServerError(
            status_code=response.status_code,
            message=f"메인 서버 응답을 JSON으로 파싱할 수 없습니다: {e}",
        ) from e

    if not isinstance(body, dict) or "isSuccess" not in body:
        raise MainServerError(
            status_code=response.status_code,
            message="메인 서버 응답이 예상된 envelope 형식이 아닙니다.",
        )

    if not body["isSuccess"]:
        error_info = body.get("error")
        error_message = "메인 서버 요청 실패"
        if isinstance(error_info, dict):
            error_message = error_info.get("message", error_message)
        elif isinstance(error_info, str):
            error_message = error_info

        raise MainServerError(
            status_code=response.status_code,
            message=error_message,
            error=error_info if isinstance(error_info, dict) else None,
        )

    return body.get("result")


async def request_with_retry(
    method: str,
    url: str,
    **kwargs: Any,
) -> Any:
    """
    재시도 로직이 포함된 HTTP 요청 후 envelope에서 result를 추출

    5xx / 타임아웃: 최대 3회 재시도, 선형 백오프 (1s, 2s, 3s)
    4xx: 즉시 실패 (재시도 불가)

    Args:
        method: HTTP 메서드 (GET, POST, PATCH, DELETE 등)
        url: 요청 URL 경로
        **kwargs: httpx.AsyncClient.request()에 전달할 추가 인자

    Returns:
        envelope의 result 필드 데이터

    Raises:
        MainServerError: 4xx 에러 또는 재시도 소진 시
        httpx.TimeoutException: 재시도 소진 후에도 타임아웃인 경우
    """
    client = get_http_client()
    last_exception: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.request(method, url, **kwargs)

            if response.status_code >= 500:
                error_message = _build_5xx_error_message(response)
                last_exception = MainServerError(
                    status_code=response.status_code,
                    message=error_message,
                )
                if attempt < MAX_RETRIES:
                    wait = LINEAR_BACKOFF_BASE * attempt
                    logger.warning(
                        "5xx 에러 (HTTP %d), %d/%d 재시도 (%.1fs 대기): %s",
                        response.status_code,
                        attempt,
                        MAX_RETRIES,
                        wait,
                        error_message,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise last_exception

            return _parse_envelope(response)

        except httpx.TimeoutException as e:
            last_exception = e
            if attempt < MAX_RETRIES:
                wait = LINEAR_BACKOFF_BASE * attempt
                logger.warning(
                    "타임아웃, %d/%d 재시도 (%.1fs 대기)",
                    attempt,
                    MAX_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            raise

    raise last_exception  # type: ignore[misc]
