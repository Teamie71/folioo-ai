"""경험정리 rate limit 테스트"""

from features.experience_map.rate_limit import SlidingWindowRateLimiter


def test_allows_requests_within_limit():
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)

    assert [limiter.check("123", now=0.0) for _ in range(3)] == [None, None, None]


def test_blocks_over_limit():
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check("123", now=0.0)

    retry_after = limiter.check("123", now=0.0)

    assert retry_after is not None
    assert retry_after > 0


def test_limits_are_per_user():
    """티켓 sub 단위로 센다. 한 사용자의 폭주가 다른 사용자를 막지 않는다."""
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
    limiter.check("123", now=0.0)
    limiter.check("123", now=0.0)

    assert limiter.check("123", now=0.0) is not None
    assert limiter.check("456", now=0.0) is None


def test_window_slides():
    """창이 지나면 다시 허용한다."""
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
    limiter.check("123", now=0.0)
    limiter.check("123", now=10.0)

    assert limiter.check("123", now=20.0) is not None
    # 첫 요청(0초)이 창을 벗어난다.
    assert limiter.check("123", now=61.0) is None


def test_retry_after_counts_until_oldest_hit_expires():
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    limiter.check("123", now=0.0)

    retry_after = limiter.check("123", now=30.0)

    # 30초 남았고 올림해서 최소 30 이상이다.
    assert retry_after is not None
    assert 30 <= retry_after <= 31


def test_reset_clears_user():
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    limiter.check("123", now=0.0)

    limiter.reset("123")

    assert limiter.check("123", now=0.0) is None


def test_reset_all():
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    limiter.check("123", now=0.0)
    limiter.check("456", now=0.0)

    limiter.reset()

    assert limiter.check("123", now=0.0) is None
    assert limiter.check("456", now=0.0) is None
