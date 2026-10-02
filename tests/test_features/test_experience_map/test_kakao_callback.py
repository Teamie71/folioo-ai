"""카카오 콜백 본문 구성·URL 검증·전송 판정 테스트"""

import httpx
import pytest

from features.experience_map.kakao import callback
from features.experience_map.kakao.schemas import KakaoTurnRequest

WEB_URL = "https://folioo.example/experience-map"
VALID_URL = "https://bot-api.kakao.com/v1/callback/abc"


# ===== URL 검증 =====


def test_validate_callback_url_accepts_kakao_https():
    callback.validate_callback_url(VALID_URL)


@pytest.mark.parametrize(
    "url",
    [
        "http://bot-api.kakao.com/v1/x",  # https 아님
        "https://evil.example.com/v1/x",  # 허용 호스트 아님
        "https://bot-api.kakao.com.evil.com/x",  # 접미사 위장
        "https://user:pw@bot-api.kakao.com/x",  # 자격증명
        "https://bot-api.kakao.com:8443/x",  # 비표준 포트
        "https://bot-api.kakao.com@evil.com/x",
        "not a url",
    ],
)
def test_validate_callback_url_rejects(url):
    with pytest.raises(ValueError) as exc_info:
        callback.validate_callback_url(url)
    assert url not in str(exc_info.value)  # 오류 메시지에 URL 을 싣지 않는다


def test_allowed_hosts_are_configurable(monkeypatch):
    monkeypatch.setenv("KAKAO_CALLBACK_ALLOWED_HOSTS", "stub.local, bot-api.kakao.com")
    callback.validate_callback_url("https://stub.local/cb")


# ===== 본문 구성 =====


def test_summary_is_truncated_to_1000_chars():
    summary = callback.build_summary("가" * 1500)
    assert len(summary) == 1000
    assert summary.endswith("…")


def test_summary_joins_and_skips_empty():
    assert callback.build_summary("첫째", None, " ", "둘째") == "첫째\n\n둘째"


def test_summary_has_default_when_empty():
    assert callback.build_summary(None, "") == callback.EMPTY_SUMMARY


def test_success_payload_splits_summary_and_button():
    payload = callback.build_success_payload("요약", WEB_URL)
    outputs = payload["template"]["outputs"]

    assert len(outputs) == 2
    assert outputs[0] == {"simpleText": {"text": "요약"}}
    button = outputs[1]["textCard"]["buttons"][0]
    assert button["action"] == "webLink"
    assert button["webLinkUrl"] == WEB_URL
    # 긴 요약이 textCard.description 으로 들어가면 안 된다.
    assert outputs[1]["textCard"]["description"] == callback.SUCCESS_BUTTON_DESCRIPTION


def test_failure_payload_has_fixed_text_and_no_revert_button():
    payload = callback.build_failure_payload(WEB_URL)
    card = payload["template"]["outputs"][0]["textCard"]

    assert card["description"] == callback.FAILURE_TEXT
    assert [b["label"] for b in card["buttons"]] == [callback.FAILURE_BUTTON_LABEL]
    assert "되돌리기" not in str(payload)


def test_unknown_payload_does_not_claim_success_or_failure():
    text = callback.build_unknown_payload(WEB_URL)["template"]["outputs"][0]["textCard"][
        "description"
    ]
    assert "오류" not in text and "정리했어요" not in text


# ===== 전송 판정 =====


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (200, {"status": "SUCCESS"}, "SUCCESS"),
        (200, {"status": "FAIL"}, "FAIL"),  # HTTP 200 만으로 성공이라 보지 않는다
        (200, {"status": "ERROR"}, "FAIL"),
        (200, {"status": "???"}, "UNKNOWN"),
        (200, None, "UNKNOWN"),  # 본문을 읽을 수 없음
        (400, {"status": "FAIL"}, "FAIL"),
        (302, None, "FAIL"),  # 리다이렉트는 따라가지 않는다
        (500, None, "UNKNOWN"),
    ],
)
async def test_send_callback_status(status_code, body, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status_code, content=b"not json")
        return httpx.Response(status_code, json=body)

    async with _client(handler) as client:
        assert await callback.send_callback(VALID_URL, {"a": 1}, client=client) == expected


@pytest.mark.asyncio
async def test_send_callback_timeout_is_unknown_and_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    async with _client(handler) as client:
        assert await callback.send_callback(VALID_URL, {}, client=client) == "UNKNOWN"
    assert calls == 1  # 도달했을 수 있으므로 재전송하지 않는다


@pytest.mark.asyncio
async def test_send_callback_connect_error_is_fail():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _client(handler) as client:
        assert await callback.send_callback(VALID_URL, {}, client=client) == "FAIL"


@pytest.mark.asyncio
async def test_send_callback_never_logs_url(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timeout for {request.url}", request=request)

    with caplog.at_level("DEBUG"):
        async with _client(handler) as client:
            await callback.send_callback(VALID_URL, {}, client=client)
    assert "abc" not in caplog.text


# ===== 요청 스키마 =====


def _request(**overrides) -> dict:
    data = {
        "user_id": "123",
        "block_id": "12",
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "utterance": "동아리에서 축제 부스 운영했어요",
        "callback_url": VALID_URL,
        "expires_at": "2026-09-30T12:00:50.000Z",
    }
    return {**data, **overrides}


def test_request_accepts_spec_example():
    parsed = KakaoTurnRequest.model_validate(_request())
    assert parsed.expires_at.tzinfo is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"utterance": ""},
        {"utterance": "   "},
        {"utterance": "가" * 501},
        {"request_id": "not-a-uuid"},
        {"expires_at": "2026-09-30T12:00:50"},  # 시간대 없음
    ],
)
def test_request_rejects_invalid(overrides):
    with pytest.raises(ValueError):
        KakaoTurnRequest.model_validate(_request(**overrides))


def test_request_allows_500_chars():
    KakaoTurnRequest.model_validate(_request(utterance="가" * 500))
