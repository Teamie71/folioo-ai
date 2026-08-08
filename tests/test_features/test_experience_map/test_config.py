"""경험정리 설정 테스트"""

import pytest

from features.experience_map import config

ENV_VARS = (
    "MAIN_BACKEND_URL",
    "AI_SERVICE_API_KEY",
    "EXPMAP_TICKET_SECRET",
    "EXPMAP_UPLOAD_BUCKET",
    "EXPMAP_RETRY_TTL_SECONDS",
    "EXPMAP_FILE_TTL_SECONDS",
    "EXPMAP_REQUEST_LEASE_SECONDS",
    "EXPMAP_LLM_TIMEOUT_SECONDS",
    "EXPMAP_FILE_TIMEOUT_SECONDS",
    "EXPMAP_GAP_TIMEOUT_SECONDS",
    "EXPERIENCE_MAP_ENABLED",
)


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    """테스트마다 관련 환경변수와 설정 캐시 초기화"""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    config.reset_settings()
    yield
    config.reset_settings()


def test_defaults_match_api_spec():
    """API 명세 8절의 기본값을 따른다."""
    settings = config.load_settings()

    assert settings.retry_ttl_seconds == 1800
    assert settings.file_ttl_seconds == 3600
    assert settings.request_lease_seconds == 300
    assert settings.timeouts.llm == 60
    assert settings.timeouts.file == 120
    assert settings.timeouts.gap == 30


def test_reads_values_from_env(monkeypatch):
    monkeypatch.setenv("EXPMAP_RETRY_TTL_SECONDS", "600")
    monkeypatch.setenv("EXPMAP_LLM_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("EXPMAP_UPLOAD_BUCKET", "folioo-expmap-uploads")

    settings = config.load_settings()

    assert settings.retry_ttl_seconds == 600
    assert settings.timeouts.llm == 90
    assert settings.upload_bucket == "folioo-expmap-uploads"


def test_invalid_int_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("EXPMAP_GAP_TIMEOUT_SECONDS", "곧바로")

    assert config.load_settings().timeouts.gap == 30


def test_feature_flag_defaults_to_disabled():
    """기능은 시나리오 검증(3.23) 전까지 꺼져 있다."""
    assert config.load_settings().enabled is False


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("no", False), ("", False)])
def test_feature_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("EXPERIENCE_MAP_ENABLED", raw)

    assert config.load_settings().enabled is expected


def test_ticket_secret_must_be_distinct_from_api_key(monkeypatch):
    """티켓 서명 키를 API 키와 재사용하면 안 된다 (API 명세 2-1)."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret")
    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "shared-secret")

    assert config.load_settings().ticket_secret_is_distinct is False

    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "ticket-only-secret")
    assert config.load_settings().ticket_secret_is_distinct is True


def test_llm_retry_is_zero():
    """자동 재시도는 LangGraph RetryPolicy 한 곳에서만 관리한다."""
    assert config.LLM_MAX_RETRIES == 0


def test_upload_limits_match_spec():
    assert config.MAX_UPLOAD_FILES == 3
    assert config.MAX_UPLOAD_FILE_BYTES == 10 * 1024 * 1024
    assert set(config.PARSER_MIME_TYPES) | set(config.OCR_MIME_TYPES) == set(
        config.ALLOWED_MIME_TYPES
    )


def test_get_settings_is_cached():
    assert config.get_settings() is config.get_settings()
