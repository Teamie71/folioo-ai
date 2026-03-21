"""텍스트 유틸 테스트"""

from common.utils import (
    count_chars,
    get_char_overflow,
    is_within_char_limit,
    truncate_to_char_limit,
)


def test_count_chars_handles_none() -> None:
    """None 입력은 0자로 계산한다."""
    assert count_chars(None) == 0


def test_get_char_overflow_returns_positive_overflow() -> None:
    """제한 초과분을 정확히 반환한다."""
    assert get_char_overflow("가나다라마", 3) == 2


def test_is_within_char_limit_returns_true_at_limit() -> None:
    """제한과 동일한 길이는 통과한다."""
    assert is_within_char_limit("가나다", 3) is True


def test_truncate_to_char_limit_returns_trimmed_text() -> None:
    """truncate는 지정 길이까지만 반환한다."""
    assert truncate_to_char_limit("가나다라마바사", 4) == "가나다라"
