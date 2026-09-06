"""메인 앱 인프라 설정 테스트"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from app import main
from app.middleware.auth import is_ticket_auth_path
from app.middleware.experience_map_ticket import ExperienceMapTicketMiddleware
from features.experience_map import config as experience_map_config


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
    import common.db.connection as db_connection
    import common.http_client as http_client
    import features.interview.agents.insight_store as insight_store_module
    import features.interview.agents.nodes.retriever as retriever_module

    class _DummyClient:
        async def close(self):
            return None

    get_http_client_mock = MagicMock(return_value=object())
    close_http_client_mock = AsyncMock()
    create_pool_mock = AsyncMock()
    close_pool_mock = AsyncMock()

    @asynccontextmanager
    async def _dummy_setup_checkpointer():
        yield object()

    monkeypatch.setattr(main, "setup_checkpointer", _dummy_setup_checkpointer)
    monkeypatch.setattr(db_connection, "create_pool", create_pool_mock)
    monkeypatch.setattr(db_connection, "close_pool", close_pool_mock)
    monkeypatch.setattr(http_client, "get_http_client", get_http_client_mock)
    monkeypatch.setattr(http_client, "close_http_client", close_http_client_mock)
    monkeypatch.setattr(correction_client_module, "init_correction_client", lambda: _DummyClient())
    monkeypatch.setattr(correction_client_module, "reset_correction_client", lambda: None)
    monkeypatch.setattr(portfolio_client_module, "init_portfolio_client", lambda: _DummyClient())
    monkeypatch.setattr(portfolio_client_module, "reset_portfolio_client", lambda: None)
    init_insight_store_mock = MagicMock()
    monkeypatch.setattr(insight_store_module, "MainServerInsightStore", lambda: object())
    monkeypatch.setattr(retriever_module, "init_insight_store", init_insight_store_mock)

    async with main.lifespan(FastAPI()):
        pass

    get_http_client_mock.assert_called_once()
    init_insight_store_mock.assert_called_once()
    close_http_client_mock.assert_awaited_once()
    create_pool_mock.assert_awaited_once()
    close_pool_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_swaps_upload_store_for_test_ui(monkeypatch, clean_experience_map_settings):
    """테스트 UI 모드는 파일 업로드도 in-memory 로 바꾼다.

    그래프·경험 맵만 바꾸고 업로드를 그대로 두면, GCS 버킷·인증이 없는
    로컬·데모 환경에서 파일을 첨부하는 즉시 `chat_stream` 이 못 잡는 예외로
    500 이 난다 (`ExperienceMapError` 만 잡기 때문). 실제로 그렇게 재현됐었다.
    """
    import common.clients.correction_client as correction_client_module
    import common.clients.portfolio_client as portfolio_client_module
    import common.db.connection as db_connection
    import common.http_client as http_client
    import features.interview.agents.insight_store as insight_store_module
    import features.interview.agents.nodes.retriever as retriever_module
    from features.experience_map.test_runtime import InMemoryObjectStore
    from features.experience_map.upload_store import get_upload_store

    class _DummyClient:
        async def close(self):
            return None

    class _DummyPool:
        async def acquire(self):
            raise AssertionError("이 테스트는 실제 쿼리를 실행하지 않는다.")

        async def close(self):
            return None

    monkeypatch.setenv("EXPERIENCE_MAP_TEST_UI_ENABLED", "true")

    @asynccontextmanager
    async def _dummy_setup_checkpointer():
        yield object()

    monkeypatch.setattr(main, "setup_checkpointer", _dummy_setup_checkpointer)
    monkeypatch.setattr(db_connection, "create_pool", AsyncMock(return_value=_DummyPool()))
    monkeypatch.setattr(db_connection, "close_pool", AsyncMock())
    monkeypatch.setattr(http_client, "get_http_client", MagicMock(return_value=object()))
    monkeypatch.setattr(http_client, "close_http_client", AsyncMock())
    monkeypatch.delenv("MAIN_BACKEND_URL", raising=False)
    monkeypatch.setattr(correction_client_module, "reset_correction_client", lambda: None)
    monkeypatch.setattr(portfolio_client_module, "reset_portfolio_client", lambda: None)
    monkeypatch.setattr(insight_store_module, "MainServerInsightStore", lambda: object())
    monkeypatch.setattr(retriever_module, "init_insight_store", MagicMock())

    async with main.lifespan(FastAPI()):
        store = get_upload_store()
        assert isinstance(store._store, InMemoryObjectStore)

        # GCS 인증 없이도 실제로 저장까지 되는지 — 배선만 확인하고 끝내지 않는다.
        class _Upload:
            def __init__(self) -> None:
                self._sent = False

            async def read(self, size: int = -1) -> bytes:
                if self._sent:
                    return b""
                self._sent = True
                return b"hello"

        stored = await store.store_files(
            "9000001", "550e8400-e29b-41d4-a716-446655440000", [("t.txt", "text/plain", _Upload())]
        )
        assert stored[0].file_size == 5

    with pytest.raises(RuntimeError, match="EXPMAP_UPLOAD_BUCKET"):
        get_upload_store()


@pytest.mark.asyncio
async def test_lifespan_continues_when_experience_map_db_missing(monkeypatch):
    """DATABASE_URL이 없어도 기동을 막지 않는다."""
    import common.clients.correction_client as correction_client_module
    import common.clients.portfolio_client as portfolio_client_module
    import common.db.connection as db_connection
    import common.http_client as http_client
    import features.interview.agents.insight_store as insight_store_module
    import features.interview.agents.nodes.retriever as retriever_module

    async def _raise_value_error():
        raise ValueError("DATABASE_URL 환경변수가 설정되지 않았습니다.")

    close_pool_mock = AsyncMock()

    @asynccontextmanager
    async def _dummy_setup_checkpointer():
        yield object()

    monkeypatch.setattr(main, "setup_checkpointer", _dummy_setup_checkpointer)
    monkeypatch.setattr(db_connection, "create_pool", _raise_value_error)
    monkeypatch.setattr(db_connection, "close_pool", close_pool_mock)
    monkeypatch.setattr(http_client, "get_http_client", MagicMock(return_value=object()))
    monkeypatch.setattr(http_client, "close_http_client", AsyncMock())
    monkeypatch.delenv("MAIN_BACKEND_URL", raising=False)
    monkeypatch.setattr(correction_client_module, "reset_correction_client", lambda: None)
    monkeypatch.setattr(portfolio_client_module, "reset_portfolio_client", lambda: None)
    monkeypatch.setattr(insight_store_module, "MainServerInsightStore", lambda: object())
    monkeypatch.setattr(retriever_module, "init_insight_store", MagicMock())

    async with main.lifespan(FastAPI()):
        pass

    close_pool_mock.assert_awaited_once()


def test_get_health_reports_experience_map_db_status(monkeypatch):
    """헬스체크가 경험 맵 DB 연결 상태를 포함한다."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret-key")
    monkeypatch.setattr(main, "get_checkpointer", lambda: object())
    monkeypatch.setattr(main, "_get_main_server_status", lambda: "connected")
    monkeypatch.setattr(main, "get_pool_status", lambda: "connected")

    assert main.get_health()["experience_map_db"] == "connected"


def test_get_health_reports_experience_map_db_disconnected(monkeypatch):
    """경험 맵 DB 풀이 없으면 disconnected를 반환한다."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret-key")
    monkeypatch.setattr(main, "get_checkpointer", lambda: object())
    monkeypatch.setattr(main, "_get_main_server_status", lambda: "connected")
    monkeypatch.setattr(main, "get_pool_status", lambda: "disconnected")

    health = main.get_health()

    assert health["experience_map_db"] == "disconnected"
    # 경험 맵 DB는 아직 선택 리소스이므로 전체 상태를 unhealthy로 만들지 않는다.
    assert health["status"] == "ok"


def test_openapi_includes_x_api_key_security_scheme():
    """OpenAPI 스키마에 `X-API-Key` 보안 스키마가 포함된다."""
    app = main.create_app()

    schema = app.openapi()

    assert schema["components"]["securitySchemes"][main.OPENAPI_API_KEY_SCHEME_NAME] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }


def _api_operations(schema, *, ticket_paths: bool):
    """`/api/*` 오퍼레이션을 인증 방식별로 모은다."""
    operations = []
    for path, path_item in schema["paths"].items():
        if not path.startswith("/api/"):
            continue
        if is_ticket_auth_path(path) is not ticket_paths:
            continue
        for method in main.OPENAPI_HTTP_METHODS:
            operation = path_item.get(method)
            if operation:
                operations.append(operation)
    return operations


def test_openapi_marks_api_routes_with_api_key_security():
    """티켓 경로가 아닌 `/api/*`는 API Key 보안 요구사항을 가진다."""
    schema = main.create_app().openapi()

    operations = _api_operations(schema, ticket_paths=False)

    assert operations
    assert all(
        operation["security"] == [{main.OPENAPI_API_KEY_SCHEME_NAME: []}]
        for operation in operations
    )


def test_openapi_marks_ticket_routes_with_bearer_security():
    """등록된 프론트 직결 경로는 Bearer 티켓 인증으로 표시한다.

    **살아 있는 설정을 다시 읽지 않는다.** 라우터 등록은 `app.api.v1` 을 import 하는
    시점의 feature flag 로 굳는데, 그 뒤 다른 테스트가 설정 캐시를 비우면 지금 읽은
    값이 실제 앱 상태와 어긋난다. 그래서 단독으로는 통과하고 전체 실행에서만 깨졌다.

    여기서 고정할 것은 "flag 가 켜져 있는가" 가 아니라 **등록된 티켓 경로가 전부
    TicketAuth 로 표시되는가** 다. flag 가 꺼져 있으면 검사 대상이 없을 뿐이고,
    그 경우의 표시 규칙은 아래 `test_ticket_paths_get_bearer_scheme` 가 직접 본다.
    """
    schema = main.create_app().openapi()

    ticket_scheme = schema["components"]["securitySchemes"][main.OPENAPI_TICKET_SCHEME_NAME]
    assert ticket_scheme["scheme"] == "bearer"

    for operation in _api_operations(schema, ticket_paths=True):
        assert operation["security"] == [{main.OPENAPI_TICKET_SCHEME_NAME: []}]


def test_ticket_paths_get_bearer_scheme():
    """경로별 인증 표시 규칙을 라우터 등록과 무관하게 검증한다.

    앞 테스트는 flag 가 꺼진 환경(CI 기본값)에서 티켓 분기를 한 번도 밟지 못한다.
    합성 스키마를 직접 넣어 규칙 자체를 고정한다.
    """
    ticket_path = "/api/v1/experience-map/sessions/{session_id}/state"
    schema = main._attach_api_key_security(
        {
            "paths": {
                ticket_path: {"get": {}},
                "/api/v1/experience-map/sessions": {"post": {}},
                "/health": {"get": {}},
            }
        }
    )

    assert is_ticket_auth_path(ticket_path), "티켓 경로 판정이 미들웨어와 어긋납니다."
    assert schema["paths"][ticket_path]["get"]["security"] == [
        {main.OPENAPI_TICKET_SCHEME_NAME: []}
    ]
    # 세션 생성은 메인 서버 호출이라 티켓이 아니라 X-API-Key 다.
    assert schema["paths"]["/api/v1/experience-map/sessions"]["post"]["security"] == [
        {main.OPENAPI_API_KEY_SCHEME_NAME: []}
    ]
    assert "security" not in schema["paths"]["/health"]["get"]


def test_openapi_keeps_health_route_public():
    """헬스체크 경로는 OpenAPI에서 보안 요구사항이 없다."""
    app = main.create_app()

    schema = app.openapi()

    assert "security" not in schema["paths"]["/health"]["get"]


def test_create_app_registers_test_ui_only_when_enabled(monkeypatch, clean_experience_map_settings):
    """내부 테스트 UI는 명시적 feature flag가 있을 때만 등록한다."""
    monkeypatch.delenv("EXPERIENCE_MAP_TEST_UI_ENABLED", raising=False)
    experience_map_config.reset_settings()
    disabled_app = main.create_app()

    monkeypatch.setenv("EXPERIENCE_MAP_TEST_UI_ENABLED", "true")
    experience_map_config.reset_settings()
    enabled_app = main.create_app()

    disabled_paths = {route.path for route in disabled_app.routes}
    enabled_paths = {route.path for route in enabled_app.routes}

    assert "/experience-map/test" not in disabled_paths
    assert {"/experience-map/test", "/experience-map/test/session"} <= enabled_paths


def test_create_app_registers_main_server_session_compatibility_path(
    monkeypatch, clean_experience_map_settings
):
    """경험정리가 켜졌을 때만 메인 서버 호환용 루트 `/sessions`를 노출한다."""
    monkeypatch.delenv("EXPERIENCE_MAP_ENABLED", raising=False)
    experience_map_config.reset_settings()
    disabled_paths = {route.path for route in main.create_app().routes}

    monkeypatch.setenv("EXPERIENCE_MAP_ENABLED", "true")
    experience_map_config.reset_settings()
    enabled_paths = {route.path for route in main.create_app().routes}

    assert "/sessions" not in disabled_paths
    assert "/sessions" in enabled_paths


@pytest.fixture
def clean_experience_map_settings():
    """설정 캐시를 앞뒤로 비운다. 안 비우면 뒤 테스트가 이 값을 물려받는다."""
    experience_map_config.reset_settings()
    yield
    experience_map_config.reset_settings()


def _ticket_rate_limiter(app: FastAPI):
    """앱에 등록된 경험정리 티켓 미들웨어의 rate limiter 를 꺼낸다.

    못 찾으면 여기서 끊는다. 그냥 `None` 을 돌려주면 호출부가 `AttributeError` 로
    죽어서 "설정이 안 맞다" 인지 "배선이 없다" 인지 구분되지 않는다.
    """
    for middleware in app.user_middleware:
        if middleware.cls is ExperienceMapTicketMiddleware:
            limiter = middleware.kwargs.get("rate_limiter")
            if limiter is None:
                raise AssertionError(
                    "티켓 미들웨어에 rate limiter 가 주입되지 않았습니다. "
                    "create_app() 이 EXPMAP_RATE_LIMIT_PER_MINUTE 을 전달하는지 확인하세요."
                )
            return limiter
    raise AssertionError("경험정리 티켓 미들웨어가 등록되지 않았습니다.")


def test_rate_limit_env_reaches_the_middleware(monkeypatch, clean_experience_map_settings):
    """`EXPMAP_RATE_LIMIT_PER_MINUTE` 가 실제 limiter 까지 전달된다.

    미들웨어 단위 테스트는 limiter 를 직접 넘겨서 검증하므로, `create_app()` 이
    설정을 주입하지 않아도 통과한다. 실제로 그 배선이 빠져 있었다.
    """
    monkeypatch.setenv("EXPMAP_RATE_LIMIT_PER_MINUTE", "7")
    experience_map_config.reset_settings()

    assert _ticket_rate_limiter(main.create_app())._max_requests == 7


def test_rate_limit_falls_back_to_default_in_app(monkeypatch, clean_experience_map_settings):
    """설정이 없으면 명세 기본값(20)으로 앱이 뜬다."""
    monkeypatch.delenv("EXPMAP_RATE_LIMIT_PER_MINUTE", raising=False)
    experience_map_config.reset_settings()

    assert _ticket_rate_limiter(main.create_app())._max_requests == 20
