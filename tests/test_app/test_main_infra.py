"""메인 앱 인프라 설정 테스트"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from app import main


def test_load_allowed_origins_from_env(monkeypatch):
    """ALLOWED_ORIGINS 환경변수에서 허용 오리진을 로드한다."""
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000, https://api.folioo.kr ,https://app.folioo.kr",
    )

    origins = main._load_allowed_origins()

    assert origins == [
        "http://localhost:3000",
        "https://api.folioo.kr",
        "https://app.folioo.kr",
    ]


def test_load_allowed_origins_uses_default(monkeypatch):
    """ALLOWED_ORIGINS가 없으면 기본값을 사용한다."""
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

    assert main._load_allowed_origins() == ["http://localhost:3000"]


def test_get_health_returns_connected_when_checkpointer_exists(monkeypatch):
    """checkpointer가 있으면 connected 상태를 반환한다."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret-key")
    monkeypatch.setattr(main, "get_checkpointer", lambda: object())
    monkeypatch.setattr(main, "_get_main_server_status", lambda: "connected")

    health = main.get_health()

    assert health["status"] == "ok"
    assert health["version"] == "0.1.0"
    assert health["checkpointer"] == "connected"
    assert health["main_server"] == "connected"
    assert health["api_key"] == "configured"


def test_get_health_returns_disconnected_when_checkpointer_missing(monkeypatch):
    """checkpointer가 없으면 disconnected 상태를 반환한다."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret-key")
    monkeypatch.setattr(main, "_get_main_server_status", lambda: "connected")

    def _simulate_checkpointer_error():
        raise RuntimeError("checkpointer not initialized")

    monkeypatch.setattr(main, "get_checkpointer", _simulate_checkpointer_error)

    health = main.get_health()

    assert health["status"] == "ok"
    assert health["checkpointer"] == "disconnected"
    assert health["main_server"] == "connected"
    assert health["api_key"] == "configured"


def test_get_health_returns_unhealthy_when_api_key_missing(monkeypatch):
    """서비스 API Key 설정이 없으면 unhealthy 상태를 반환한다."""
    monkeypatch.delenv("AI_SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(main, "get_checkpointer", lambda: object())
    monkeypatch.setattr(main, "_get_main_server_status", lambda: "disconnected")

    health = main.get_health()

    assert health["status"] == "unhealthy"
    assert health["main_server"] == "disconnected"
    assert health["api_key"] == "missing"


def test_get_main_server_status_returns_connected(monkeypatch):
    """메인 서버 HTTP 클라이언트가 초기화되면 connected를 반환한다."""
    import common.http_client as http_client

    monkeypatch.setattr(http_client, "get_http_client", lambda: object())

    assert main._get_main_server_status() == "connected"


def test_get_main_server_status_returns_disconnected(monkeypatch):
    """메인 서버 HTTP 클라이언트 초기화 실패 시 disconnected를 반환한다."""
    import common.http_client as http_client

    def _raise_runtime_error():
        raise RuntimeError("client is not configured")

    monkeypatch.setattr(http_client, "get_http_client", _raise_runtime_error)

    assert main._get_main_server_status() == "disconnected"


@pytest.mark.asyncio
async def test_lifespan_initializes_and_closes_http_client(monkeypatch):
    """lifespan 시작/종료 시 HTTP 클라이언트 초기화와 정리를 수행한다."""
    import common.clients.correction_client as correction_client_module
    import common.clients.portfolio_client as portfolio_client_module
    import common.http_client as http_client

    class _DummyClient:
        async def close(self):
            return None

    get_http_client_mock = MagicMock(return_value=object())
    close_http_client_mock = AsyncMock()

    @asynccontextmanager
    async def _dummy_setup_checkpointer():
        yield object()

    monkeypatch.setattr(main, "setup_checkpointer", _dummy_setup_checkpointer)
    monkeypatch.setattr(http_client, "get_http_client", get_http_client_mock)
    monkeypatch.setattr(http_client, "close_http_client", close_http_client_mock)
    monkeypatch.setattr(correction_client_module, "init_correction_client", lambda: _DummyClient())
    monkeypatch.setattr(correction_client_module, "reset_correction_client", lambda: None)
    monkeypatch.setattr(portfolio_client_module, "init_portfolio_client", lambda: _DummyClient())
    monkeypatch.setattr(portfolio_client_module, "reset_portfolio_client", lambda: None)

    async with main.lifespan(FastAPI()):
        pass

    get_http_client_mock.assert_called_once()
    close_http_client_mock.assert_awaited_once()


def test_openapi_includes_x_api_key_security_scheme():
    """OpenAPI 스키마에 `X-API-Key` 보안 스키마가 포함된다."""
    app = main.create_app()

    schema = app.openapi()

    assert schema["components"]["securitySchemes"][main.OPENAPI_API_KEY_SCHEME_NAME] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }


def test_openapi_marks_api_routes_with_api_key_security():
    """`/api/*` 경로는 OpenAPI에서 API Key 보안 요구사항을 가진다."""
    app = main.create_app()

    schema = app.openapi()
    api_operations = []

    for path, path_item in schema["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method in main.OPENAPI_HTTP_METHODS:
            operation = path_item.get(method)
            if operation:
                api_operations.append(operation)

    assert api_operations
    assert all(
        operation["security"] == [{main.OPENAPI_API_KEY_SCHEME_NAME: []}]
        for operation in api_operations
    )


def test_openapi_keeps_health_route_public():
    """헬스체크 경로는 OpenAPI에서 보안 요구사항이 없다."""
    app = main.create_app()

    schema = app.openapi()

    assert "security" not in schema["paths"]["/health"]["get"]
