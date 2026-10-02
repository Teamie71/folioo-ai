"""카카오 최종 콜백 본문 구성과 전송

콜백 URL은 1회용 비밀이다. 로그·예외 메시지에 URL을 싣지 않는다.
"""

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from features.experience_map.kakao.config import (
    CALLBACK_SEND_TIMEOUT_SECONDS,
    get_allowed_callback_hosts,
)
from features.experience_map.kakao.schemas import DeliveryStatus

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 1000

FAILURE_TEXT = "앗, 작업 중 오류가 발생했어요.\n웹에서 다시 시도해주세요."
SUCCESS_BUTTON_DESCRIPTION = "Folioo 웹에서 정리한 경험을 확인할 수 있어요."
SUCCESS_BUTTON_LABEL = "웹에서 확인하기"
FAILURE_BUTTON_LABEL = "웹에서 다시 시도하기"
EMPTY_SUMMARY = "경험 정리를 마쳤어요."


def validate_callback_url(url: str) -> None:
    """실제 카카오 HTTPS 콜백 대상만 허용한다.

    Raises:
        ValueError: https 가 아니거나 허용 호스트가 아니거나 자격증명·포트가 섞인 경우.
            메시지에 URL 을 싣지 않는다.
    """
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise ValueError("callback_url 형식이 올바르지 않습니다.") from exc

    if parts.scheme != "https":
        raise ValueError("callback_url 은 https 여야 합니다.")
    if parts.username or parts.password or port not in (None, 443):
        raise ValueError("callback_url 에 허용되지 않는 구성이 있습니다.")
    if host not in get_allowed_callback_hosts():
        raise ValueError("callback_url 호스트가 허용 목록에 없습니다.")


def build_summary(*texts: str | None) -> str:
    """AI 답변들을 카톡 simpleText 한도(1000자)에 맞춰 합친다."""
    joined = "\n\n".join(t.strip() for t in texts if t and t.strip())
    if not joined:
        return EMPTY_SUMMARY
    if len(joined) <= MAX_SUMMARY_CHARS:
        return joined
    return joined[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"


def build_success_payload(summary: str, web_url: str) -> dict[str, Any]:
    """요약 simpleText 와 웹 버튼 textCard 로 구성한다 (outputs 2개)."""
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {"simpleText": {"text": summary[:MAX_SUMMARY_CHARS]}},
                {
                    "textCard": {
                        "description": SUCCESS_BUTTON_DESCRIPTION,
                        "buttons": [
                            {
                                "action": "webLink",
                                "label": SUCCESS_BUTTON_LABEL,
                                "webLinkUrl": web_url,
                            }
                        ],
                    }
                },
            ]
        },
    }


def build_failure_payload(web_url: str) -> dict[str, Any]:
    """확정 실패 문구. 되돌리기 버튼은 넣지 않는다."""
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "textCard": {
                        "description": FAILURE_TEXT,
                        "buttons": [
                            {
                                "action": "webLink",
                                "label": FAILURE_BUTTON_LABEL,
                                "webLinkUrl": web_url,
                            }
                        ],
                    }
                }
            ]
        },
    }


async def send_callback(
    url: str,
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = CALLBACK_SEND_TIMEOUT_SECONDS,
) -> DeliveryStatus:
    """콜백을 **한 번만** 전송하고 전달 상태를 판정한다.

    - HTTP 2xx 이고 본문 `status` 가 SUCCESS: SUCCESS
    - 본문 `status` 가 FAIL/ERROR, 또는 4xx, 또는 연결 전 실패: FAIL
    - 타임아웃·5xx·본문을 읽을 수 없음 등 도달 여부를 알 수 없는 경우: UNKNOWN

    HTTP 200 만으로 성공이라고 판정하지 않는다. 재전송하지 않는다 — URL 은 한 번만
    쓸 수 있고, UNKNOWN 은 이미 전달됐을 수 있다.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(follow_redirects=False)
    try:
        response = await http.post(url, json=payload, timeout=timeout)
    except httpx.ConnectError:
        logger.warning("카카오 콜백 연결 실패")
        return "FAIL"
    except Exception as exc:
        # URL 이 메시지에 섞일 수 있어 예외 클래스만 남긴다.
        logger.warning("카카오 콜백 전달 결과 불명 (%s)", type(exc).__name__)
        return "UNKNOWN"
    finally:
        if owns_client:
            await http.aclose()

    if response.is_redirect or 300 <= response.status_code < 400:
        logger.warning("카카오 콜백이 리다이렉트를 응답했습니다 (HTTP %s)", response.status_code)
        return "FAIL"
    if 400 <= response.status_code < 500:
        logger.warning("카카오 콜백이 거절됐습니다 (HTTP %s)", response.status_code)
        return "FAIL"
    if response.status_code >= 500:
        logger.warning("카카오 콜백 서버 오류 (HTTP %s)", response.status_code)
        return "UNKNOWN"

    try:
        body = response.json()
    except Exception:
        return "UNKNOWN"
    body_status = str(body.get("status", "")).upper() if isinstance(body, dict) else ""
    if body_status == "SUCCESS":
        return "SUCCESS"
    if body_status in {"FAIL", "ERROR"}:
        logger.warning("카카오 콜백 본문 status=%s", body_status)
        return "FAIL"
    return "UNKNOWN"


UNKNOWN_TEXT = "처리 결과를 확인하고 있어요.\n잠시 후 웹에서 확인해 주세요."


def build_unknown_payload(web_url: str) -> dict[str, Any]:
    """커밋 결과가 확정되지 않았을 때의 안내. 성공·실패를 단정하지 않는다."""
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "textCard": {
                        "description": UNKNOWN_TEXT,
                        "buttons": [
                            {
                                "action": "webLink",
                                "label": SUCCESS_BUTTON_LABEL,
                                "webLinkUrl": web_url,
                            }
                        ],
                    }
                }
            ]
        },
    }
