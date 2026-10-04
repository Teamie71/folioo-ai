"""카카오톡 턴 설정

경험정리 공용 설정(`features/experience_map/config.py`)과 분리해 카톡 전용 값만 읽는다.
"""

import os
from urllib.parse import urlsplit

DEFAULT_CALLBACK_HOSTS = ("bot-api.kakao.com",)

RUN_BUDGET_SECONDS = 45
"""`expires_at` 이전에 새 작업을 멈추기 시작하는 여유 (명세 1절: 45초부터 중단·정리)"""

CALLBACK_SEND_TIMEOUT_SECONDS = 4.0
"""콜백 한 번에 기다리는 최대 시간. 기한 안에 끝내야 하므로 짧게 둔다."""

SESSION_WAIT_POLL_SECONDS = 0.5
"""같은 세션의 다른 턴이 끝나길 기다리며 다시 잡아 보는 간격"""

WORKER_LEASE_SECONDS = 60
"""카톡 worker 실행권. 죽은 worker 의 턴을 복구 루프가 이어받는 기준"""

RECOVERY_INTERVAL_SECONDS = 15
"""복구 루프 주기"""

RETENTION_HOURS = 48
"""종결된 턴 기록 보존 시간 (명세: 최소 24시간)"""


def get_web_experience_url() -> str:
    """웹 버튼이 가리킬 경험 맵 주소 (`KAKAO_WEB_EXPERIENCE_URL`)

    Raises:
        ValueError: 환경변수가 없거나 https 주소가 아닌 경우
    """
    url = os.getenv("KAKAO_WEB_EXPERIENCE_URL", "").strip()
    if not url:
        raise ValueError("KAKAO_WEB_EXPERIENCE_URL 환경변수가 설정되지 않았습니다.")
    if urlsplit(url).scheme != "https":
        raise ValueError("KAKAO_WEB_EXPERIENCE_URL 은 https 주소여야 합니다.")
    return url


def get_allowed_callback_hosts() -> tuple[str, ...]:
    """콜백을 허용할 호스트 (`KAKAO_CALLBACK_ALLOWED_HOSTS`, 쉼표 구분)"""
    raw = os.getenv("KAKAO_CALLBACK_ALLOWED_HOSTS", "")
    hosts = tuple(h.strip().lower() for h in raw.split(",") if h.strip())
    return hosts or DEFAULT_CALLBACK_HOSTS
