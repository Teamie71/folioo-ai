"""메인 앱 인프라 설정 테스트"""

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

    health = main.get_health()

    assert health["status"] == "ok"
    assert health["version"] == "0.1.0"
    assert health["checkpointer"] == "connected"
    assert health["api_key"] == "configured"


def test_get_health_returns_disconnected_when_checkpointer_missing(monkeypatch):
    """checkpointer가 없으면 disconnected 상태를 반환한다."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret-key")

    def _simulate_checkpointer_error():
        raise RuntimeError("checkpointer not initialized")

    monkeypatch.setattr(main, "get_checkpointer", _simulate_checkpointer_error)

    health = main.get_health()

    assert health["status"] == "ok"
    assert health["checkpointer"] == "disconnected"
    assert health["api_key"] == "configured"


def test_get_health_returns_unhealthy_when_api_key_missing(monkeypatch):
    """서비스 API Key 설정이 없으면 unhealthy 상태를 반환한다."""
    monkeypatch.delenv("AI_SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(main, "get_checkpointer", lambda: object())

    health = main.get_health()

    assert health["status"] == "unhealthy"
    assert health["api_key"] == "missing"


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
