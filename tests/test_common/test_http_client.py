"""HTTP 클라이언트 인프라 테스트"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from common.http_client.client import (
    LINEAR_BACKOFF_BASE,
    MAX_RETRIES,
    MainServerError,
    _parse_envelope,
    request_with_retry,
)


class TestParseEnvelope:
    """응답 envelope 파싱 테스트"""

    def test_success_envelope(self):
        """isSuccess=true 시 result 추출"""
        response = MagicMock(spec=httpx.Response)
        response.json.return_value = {
            "timestamp": "2026-03-06T00:00:00Z",
            "isSuccess": True,
            "error": None,
            "result": {"id": 1, "name": "test"},
        }
        response.status_code = 200

        result = _parse_envelope(response)
        assert result == {"id": 1, "name": "test"}

    def test_success_envelope_with_none_result(self):
        """isSuccess=true, result=None인 경우"""
        response = MagicMock(spec=httpx.Response)
        response.json.return_value = {
            "timestamp": "2026-03-06T00:00:00Z",
            "isSuccess": True,
            "error": None,
            "result": None,
        }
        response.status_code = 200

        result = _parse_envelope(response)
        assert result is None

    def test_failure_envelope_raises_error(self):
        """isSuccess=false 시 MainServerError 발생"""
        response = MagicMock(spec=httpx.Response)
        response.json.return_value = {
            "timestamp": "2026-03-06T00:00:00Z",
            "isSuccess": False,
            "error": {"code": "NOT_FOUND", "message": "리소스를 찾을 수 없습니다."},
            "result": None,
        }
        response.status_code = 404

        with pytest.raises(MainServerError) as exc_info:
            _parse_envelope(response)
        assert exc_info.value.status_code == 404
        assert "리소스를 찾을 수 없습니다" in exc_info.value.message

    def test_failure_envelope_with_string_error(self):
        """error가 문자열인 경우"""
        response = MagicMock(spec=httpx.Response)
        response.json.return_value = {
            "timestamp": "2026-03-06T00:00:00Z",
            "isSuccess": False,
            "error": "서버 내부 오류",
            "result": None,
        }
        response.status_code = 500

        with pytest.raises(MainServerError) as exc_info:
            _parse_envelope(response)
        assert exc_info.value.message == "서버 내부 오류"

    def test_invalid_envelope_format(self):
        """envelope 형식이 아닌 응답"""
        response = MagicMock(spec=httpx.Response)
        response.json.return_value = {"data": "unexpected"}
        response.status_code = 200

        with pytest.raises(MainServerError) as exc_info:
            _parse_envelope(response)
        assert "envelope 형식" in exc_info.value.message


class TestRequestWithRetry:
    """재시도 로직 테스트"""

    @pytest.fixture(autouse=True)
    def _cleanup_client(self):
        """각 테스트 후 클라이언트 정리 (모듈 변수 직접 리셋)"""
        import common.http_client.client as module

        yield
        module._client = None

    @pytest.mark.asyncio
    async def test_successful_request(self, monkeypatch):
        """정상 요청 시 result 반환"""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "isSuccess": True,
            "result": {"key": "value"},
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request = AsyncMock(return_value=mock_response)

        import common.http_client.client as module

        monkeypatch.setattr(module, "_client", mock_client)

        result = await request_with_retry("GET", "/test")
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_4xx_no_retry(self, monkeypatch):
        """4xx 에러 시 재시도 없이 envelope 파싱"""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        mock_response.json.return_value = {
            "isSuccess": False,
            "error": {"message": "Not Found"},
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request = AsyncMock(return_value=mock_response)

        import common.http_client.client as module

        monkeypatch.setattr(module, "_client", mock_client)

        with pytest.raises(MainServerError) as exc_info:
            await request_with_retry("GET", "/not-found")
        assert exc_info.value.status_code == 404
        assert mock_client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_5xx_retries_and_exhausts(self, monkeypatch):
        """5xx 에러 시 최대 재시도 후 예외 발생"""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 502

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request = AsyncMock(return_value=mock_response)

        import common.http_client.client as module

        monkeypatch.setattr(module, "_client", mock_client)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        with pytest.raises(MainServerError):
            await request_with_retry("GET", "/error")
        assert mock_client.request.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_5xx_then_success(self, monkeypatch):
        """5xx 후 재시도 시 성공"""
        fail_response = MagicMock(spec=httpx.Response)
        fail_response.status_code = 503

        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.json.return_value = {
            "isSuccess": True,
            "result": {"recovered": True},
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request = AsyncMock(side_effect=[fail_response, success_response])

        import common.http_client.client as module

        monkeypatch.setattr(module, "_client", mock_client)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        result = await request_with_retry("GET", "/recover")
        assert result == {"recovered": True}
        assert mock_client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_timeout_retries(self, monkeypatch):
        """타임아웃 시 재시도"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request = AsyncMock(side_effect=httpx.ReadTimeout("read timeout"))

        import common.http_client.client as module

        monkeypatch.setattr(module, "_client", mock_client)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        with pytest.raises(httpx.ReadTimeout):
            await request_with_retry("GET", "/timeout")
        assert mock_client.request.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_linear_backoff_timing(self, monkeypatch):
        """선형 백오프 대기 시간 검증 (1s, 2s, 3s)"""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request = AsyncMock(return_value=mock_response)

        sleep_calls: list[float] = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        import common.http_client.client as module

        monkeypatch.setattr(module, "_client", mock_client)
        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        with pytest.raises(MainServerError):
            await request_with_retry("GET", "/backoff")

        expected_waits = [LINEAR_BACKOFF_BASE * i for i in range(1, MAX_RETRIES)]
        assert sleep_calls == expected_waits
