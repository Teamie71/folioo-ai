"""경험정리 설정 테스트"""

import pytest

from features.experience_map import config

ENV_VARS = (
    "MAIN_BACKEND_URL",
    "AI_SERVICE_API_KEY",
    "MAIN_BACKEND_API_KEY",
    "EXPMAP_TICKET_SECRET",
    "EXPMAP_UPLOAD_BUCKET",
    "EXPMAP_RETRY_TTL_SECONDS",
    "EXPMAP_FILE_TTL_SECONDS",
    "EXPMAP_REQUEST_LEASE_SECONDS",
    "EXPMAP_LLM_TIMEOUT_SECONDS",
    "EXPMAP_FILE_TIMEOUT_SECONDS",
    "EXPMAP_GAP_TIMEOUT_SECONDS",
    "EXPMAP_RATE_LIMIT_PER_MINUTE",
    "EXPERIENCE_MAP_ENABLED",
    "EXPERIENCE_MAP_TEST_UI_ENABLED",
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


def test_test_ui_flag_defaults_to_disabled_and_can_be_enabled(monkeypatch):
    """수동 테스트 UI는 명시적으로 켠 환경에서만 노출한다."""
    assert config.load_settings().test_ui_enabled is False

    monkeypatch.setenv("EXPERIENCE_MAP_TEST_UI_ENABLED", "true")
    assert config.load_settings().test_ui_enabled is True


def test_ticket_secret_must_be_distinct_from_api_key(monkeypatch):
    """티켓 서명 키를 인바운드 API 키와 재사용하면 안 된다 (API 명세 2-1)."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "shared-secret")
    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "shared-secret")

    assert config.load_settings().ticket_secret_is_distinct is False

    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "ticket-only-secret")
    assert config.load_settings().ticket_secret_is_distinct is True


def test_ticket_secret_must_be_distinct_from_outbound_key(monkeypatch):
    """아웃바운드 키와 겹쳐도 안 된다.

    티켓은 프론트가 직접 들고 다닌다. 서명 키가 서버 간 키와 같으면 둘 중 하나만
    새어도 임의 사용자의 세션 티켓을 위조할 수 있다. 방향은 관계없다.
    """
    monkeypatch.setenv("MAIN_BACKEND_API_KEY", "outbound-secret")
    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "outbound-secret")

    assert config.load_settings().ticket_secret_is_distinct is False

    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "ticket-only-secret")
    assert config.load_settings().ticket_secret_is_distinct is True


def test_api_keys_are_read_per_direction(monkeypatch):
    """방향마다 다른 변수를 읽는다 (명세 2-1)."""
    monkeypatch.setenv("AI_SERVICE_API_KEY", "inbound-key")
    monkeypatch.setenv("MAIN_BACKEND_API_KEY", "outbound-key")

    settings = config.load_settings()

    assert settings.service_api_key == "inbound-key"
    assert settings.main_backend_api_key == "outbound-key"


def test_ticket_secret_strength(monkeypatch):
    """HS256 키는 32바이트 이상이어야 한다 (RFC 7518 3.2)."""
    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "short-secret")
    assert config.load_settings().ticket_secret_is_strong is False

    monkeypatch.setenv("EXPMAP_TICKET_SECRET", "a" * config.MIN_TICKET_SECRET_BYTES)
    assert config.load_settings().ticket_secret_is_strong is True


def test_missing_ticket_secret_is_not_strong():
    assert config.load_settings().ticket_secret_is_strong is False


def test_llm_retry_is_zero():
    """자동 재시도는 LangGraph RetryPolicy 한 곳에서만 관리한다."""
    assert config.LLM_MAX_RETRIES == 0


def test_upload_limits_match_spec():
    assert config.MAX_UPLOAD_FILES == 1
    assert config.MAX_UPLOAD_FILE_BYTES == 10 * 1024 * 1024
    assert set(config.PARSER_MIME_TYPES) | set(config.OCR_MIME_TYPES) == set(
        config.ALLOWED_MIME_TYPES
    )


def test_get_settings_is_cached():
    assert config.get_settings() is config.get_settings()


def test_rate_limit_defaults_to_spec_value():
    """API 명세 8절 기본값은 분당 20건이다."""
    assert config.load_settings().rate_limit_per_minute == 20


def test_rate_limit_reads_from_env(monkeypatch):
    monkeypatch.setenv("EXPMAP_RATE_LIMIT_PER_MINUTE", "5")

    assert config.load_settings().rate_limit_per_minute == 5


@pytest.mark.parametrize("raw", ["0", "-1", "-100", "스물", "3.5", " "])
def test_rate_limit_rejects_unusable_values(monkeypatch, raw):
    """0·음수·비정수는 기본값으로 되돌린다.

    **0과 음수가 특히 위험하다.** 정수라 파싱은 되지만 한도로 쓰이면 모든 요청이
    막혀 기능이 통째로 죽는다. 오타 하나로 기능이 내려가면 안 된다.
    """
    monkeypatch.setenv("EXPMAP_RATE_LIMIT_PER_MINUTE", raw)

    assert config.load_settings().rate_limit_per_minute == 20


def test_rate_limit_warns_on_unusable_value(monkeypatch, caplog):
    """조용히 넘어가면 운영에서 설정이 안 먹는 걸 눈치채지 못한다."""
    monkeypatch.setenv("EXPMAP_RATE_LIMIT_PER_MINUTE", "0")

    with caplog.at_level("WARNING", logger=config.logger.name):
        config.load_settings()

    assert "EXPMAP_RATE_LIMIT_PER_MINUTE" in caplog.text
