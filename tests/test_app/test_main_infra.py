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
    monkeypatch.setattr(main, "get_checkpointer", lambda: object())

    health = main.get_health()

    assert health["status"] == "ok"
    assert health["version"] == "0.1.0"
    assert health["checkpointer"] == "connected"


def test_get_health_returns_disconnected_when_checkpointer_missing(monkeypatch):
    """checkpointer가 없으면 disconnected 상태를 반환한다."""

    def _raise_runtime_error():
        raise RuntimeError("checkpointer not initialized")

    monkeypatch.setattr(main, "get_checkpointer", _raise_runtime_error)

    health = main.get_health()

    assert health["checkpointer"] == "disconnected"
