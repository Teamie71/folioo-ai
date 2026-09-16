"""PDF 추출 설정 로더 테스트"""

from features.portfolio.pdf_extraction.config import (
    PdfExtractionLimitsConfig,
    get_pdf_extraction_limits,
    load_pdf_extraction_config,
)


def test_load_pdf_extraction_config_returns_v35_limits():
    """YAML 로 정의한 화면설계서 v.3.5 상한을 반환한다."""
    limits = load_pdf_extraction_config().limits

    assert limits.max_activity_count == 4
    assert limits.detail_max_length == 300
    assert limits.responsibility_max_length == 700
    assert limits.problem_solving_max_length == 700
    assert limits.learning_max_length == 300


def test_get_pdf_extraction_limits_matches_loaded_config():
    """상한 접근자는 로드된 설정과 같은 값을 반환한다."""
    assert get_pdf_extraction_limits() == load_pdf_extraction_config().limits


def test_pdf_extraction_limits_defaults_match_yaml():
    """스키마 기본값도 YAML 과 동일해 설정 누락 시 동작이 바뀌지 않는다."""
    assert PdfExtractionLimitsConfig() == load_pdf_extraction_config().limits
