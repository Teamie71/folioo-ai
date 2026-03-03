"""API Key 인증 미들웨어 테스트"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.auth import ApiKeyAuthMiddleware


def _create_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(ApiKeyAuthMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/protected")
    async def protected():
        return {"ok": True}

    @app.options("/api/v1/protected")
    async def protected_options():
        return {"ok": True}

    return TestClient(app)


def test_health_is_exempt_from_api_key(monkeypatch):
    """헬스체크 경로는 API Key 없이 접근 가능하다."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret-key")
    client = _create_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs_route_is_exempt_from_api_key(monkeypatch):
    """문서 경로는 API Key 없이 접근 가능하다."""
    monkeypatch.delenv("AI_SERVICE_API_KEY", raising=False)
    client = _create_client()

    response = client.get("/docs")

    assert response.status_code == 200


def test_docs_oauth2_redirect_is_exempt_from_api_key(monkeypatch):
    """문서 OAuth 리다이렉트 경로는 API Key 없이 접근 가능하다."""
    monkeypatch.delenv("AI_SERVICE_API_KEY", raising=False)
    client = _create_client()

    response = client.get("/docs/oauth2-redirect")

    assert response.status_code == 200


def test_openapi_json_is_exempt_from_api_key(monkeypatch):
    """OpenAPI 스키마 경로는 API Key 없이 접근 가능하다."""
    monkeypatch.delenv("AI_SERVICE_API_KEY", raising=False)
    client = _create_client()

    response = client.get("/openapi.json")

    assert response.status_code == 200


def test_docs_prefix_route_is_not_exempt(monkeypatch):
    """`/docs` 접두사만 일치하는 경로는 예외 처리하지 않는다."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret-key")
    client = _create_client()

    response = client.get("/docs-private")

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def test_protected_route_returns_401_without_api_key(monkeypatch):
    """보호 경로는 API Key 없으면 401을 반환한다."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret-key")
    client = _create_client()

    response = client.get("/api/v1/protected")

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def test_protected_route_returns_500_when_api_key_not_configured(monkeypatch):
    """서버 API Key 설정이 없으면 500을 반환한다."""
    monkeypatch.delenv("AI_SERVICE_API_KEY", raising=False)
    client = _create_client()

    response = client.get("/api/v1/protected")

    assert response.status_code == 500
    assert response.json() == {"detail": "AI_SERVICE_API_KEY is not configured"}


def test_protected_route_allows_valid_api_key(monkeypatch):
    """보호 경로는 유효한 API Key가 있으면 통과한다."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret-key")
    client = _create_client()

    response = client.get("/api/v1/protected", headers={"X-API-Key": "shared-secret-key"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_options_request_is_exempt_from_api_key(monkeypatch):
    """OPTIONS 프리플라이트 요청은 API Key 없이 접근 가능하다."""
    monkeypatch.delenv("AI_SERVICE_API_KEY", raising=False)
    client = _create_client()

    response = client.options("/api/v1/protected")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
