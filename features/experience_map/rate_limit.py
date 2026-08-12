"""경험정리 rate limit

티켓 `sub`(사용자) 단위로 제한한다. 티켓 발급 자체의 제한은 메인 서버 책임이다
(API 명세 2-1).

**프로세스 메모리 기반이라 worker마다 따로 센다.** worker가 N개면 실질 한도는
N배가 된다. 남용을 완전히 막는 장치가 아니라 한 사용자가 실수로 폭주하는 것을
막는 수준이며, 엄밀한 제한이 필요해지면 Redis 등 공유 저장소로 옮긴다.
"""

import time
from collections import deque

DEFAULT_MAX_REQUESTS = 20
DEFAULT_WINDOW_SECONDS = 60


class SlidingWindowRateLimiter:
    """사용자별 sliding window 제한기"""

    def __init__(
        self,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, *, now: float | None = None) -> int | None:
        """요청을 기록하고 초과 여부를 판정한다.

        Args:
            key: 티켓 `sub`
            now: 현재 시각 (테스트용)

        Returns:
            int | None: 한도 내면 None, 초과하면 재시도까지 남은 초
        """
        current = time.monotonic() if now is None else now
        window = self._hits.setdefault(key, deque())

        cutoff = current - self._window_seconds
        while window and window[0] <= cutoff:
            window.popleft()

        if len(window) >= self._max_requests:
            # 가장 오래된 기록이 창을 벗어나야 한 자리가 난다.
            retry_after = window[0] + self._window_seconds - current
            return max(1, int(retry_after) + 1)

        window.append(current)
        return None

    def reset(self, key: str | None = None) -> None:
        """기록 초기화 (테스트용)"""
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)
