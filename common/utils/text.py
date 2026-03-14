"""텍스트 길이 유틸리티"""


def count_chars(text: str | None) -> int:
    """문자 수를 반환"""
    return len(text or "")


def is_within_char_limit(text: str | None, limit: int) -> bool:
    """문자 수 제한 이내 여부 반환"""
    return count_chars(text) <= limit


def get_char_overflow(text: str | None, limit: int) -> int:
    """문자 수 초과량 반환"""
    return max(count_chars(text) - limit, 0)


def truncate_to_char_limit(text: str | None, limit: int) -> str:
    """문자 수 제한에 맞춰 잘라낸 텍스트 반환"""
    return (text or "")[:limit]


__all__ = [
    "count_chars",
    "get_char_overflow",
    "is_within_char_limit",
    "truncate_to_char_limit",
]
