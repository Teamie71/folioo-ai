"""경험정리 에이전트 설정

환경변수는 API 명세 8절을 따른다. DB 연결(`DATABASE_URL`·`CHECKPOINT_DATABASE_URL`)은
`common/db`·`common/checkpointer`가 소유하므로 여기서 다루지 않는다.

**서버 간 인증 키는 방향마다 다르다** (명세 2-1).

| 방향 | 변수 | 누가 쓰나 |
| --- | --- | --- |
| 메인 → AI | `AI_SERVICE_API_KEY` | `app/middleware/auth.py` 가 검증 |
| AI → 메인 | `MAIN_BACKEND_API_KEY` | `common/http_client` 가 헤더에 실음 |

여기 있는 두 키 값은 **HTTP 호출에 쓰이지 않는다.** 티켓 서명 키가 둘 중 어느
쪽과도 겹치지 않는지 확인하는 용도뿐이다 (`ticket_secret_is_distinct`).
아웃바운드 헤더는 `common/http_client` 가 직접 환경변수를 읽어 붙인다.
"""

import logging
import os
from functools import lru_cache

from pydantic import BaseModel, Field

# 한도 기본값의 출처는 limiter 쪽 하나로 둔다. rate_limit 은 이 모듈을 import 하지
# 않으므로 순환하지 않는다.
from features.experience_map.rate_limit import DEFAULT_MAX_REQUESTS

logger = logging.getLogger(__name__)

# ===== 첨부 파일 제한 (API 명세 5절) =====
MAX_UPLOAD_FILES = 3
MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024

PARSER_MIME_TYPES: dict[str, tuple[str, ...]] = {
    "text/plain": (".txt",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (".docx",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (".pptx",),
}
OCR_MIME_TYPES: dict[str, tuple[str, ...]] = {
    "application/pdf": (".pdf",),
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
}
ALLOWED_MIME_TYPES: dict[str, tuple[str, ...]] = {**PARSER_MIME_TYPES, **OCR_MIME_TYPES}

# ===== 파일 추출 =====
MAX_FILE_TEXT_CHARS = 40_000
"""파일 하나에서 가져올 최대 글자 수. 넘으면 앞부분만 쓰고 잘렸다고 알린다."""

MAX_TOTAL_TEXT_CHARS = 80_000
"""요청 전체 추출 텍스트 상한. 프롬프트 토큰과 비용을 묶어 둔다."""

MAX_PDF_PAGES = 10
"""PDF에서 OCR할 최대 페이지 수. 넘으면 앞쪽 페이지만 쓴다."""

PDF_RENDER_SCALE = 2.0
"""PDF→이미지 렌더링 배율. 한글처럼 획이 많은 글자가 뭉개지지 않을 정도로 키운다."""

PDF_OCR_CONCURRENCY = 3
"""PDF의 페이지별 OCR 최대 동시 호출 수."""

# ===== 블록 제약 (API 명세 4-2) =====
MAX_CONTENT_LENGTH = 500
MIN_CONTENT_LENGTH = 1
MAX_BLOCK_LEVEL = 5

# ===== 재시도·보정 (API 명세 2-4) =====
NODE_MAX_ATTEMPTS = 2
"""LangGraph RetryPolicy. 1회 자동 재시도를 의미한다."""

MAX_SOURCE_ITEMS_PER_STRUCTURE_BATCH = 3
"""구조화 노드가 LLM 한 번에 배정을 맡기는 최대 원문 item 수.

채팅 원문은 짧은 문장 여러 개가 한 주제를 구성하는 경우가 많아 최대 3개를 함께
배정한다. 파일 원문은 아래의 더 작은 별도 한도를 적용한다."""

MAX_FILE_SOURCE_ITEMS_PER_STRUCTURE_BATCH = 1
"""PDF·문서에서 추출한 원문을 구조화 LLM 한 번에 맡기는 최대 item 수.

파일 원문을 2~3개씩 맡기면 서로 다른 카테고리·하위 템플릿 판단이 한 응답에
섞이면서 60초 제한을 넘거나 계약을 어기는 경우가 반복돼 하나씩 처리한다."""

MAX_SOURCE_ITEM_CHARS = MAX_CONTENT_LENGTH
"""구조화에 넘기는 원문 item 하나의 최대 글자 수.

PDF OCR 결과처럼 긴 문단 하나가 통째로 분류되면 item 개수 제한만으로는 구조화
프롬프트와 structured output 크기를 제어할 수 없다. 최종 블록의 500자 제한과
같은 크기로 원문을 나누되, 내용은 고치지 않고 문장·줄바꿈 경계만 사용한다.
"""

MAX_SOURCE_CHARS_PER_STRUCTURE_BATCH = 1_200
"""구조화 LLM 한 번에 전달하는 원문 text 총 글자 수 상한."""

MAX_FILE_SOURCE_CHARS_PER_STRUCTURE_BATCH = MAX_SOURCE_ITEM_CHARS
"""파일 원문 구조화 호출의 text 총 글자 수 상한."""

MAX_VALIDATION_REPAIRS = 2
"""validate → structure/refine 회귀 최대 횟수."""

COMMIT_MAX_ATTEMPTS = 3
COMMIT_BACKOFF_SECONDS = (1, 2, 3)

LLM_MAX_RETRIES = 0
"""LLM client 내장 retry. 0으로 고정해 LangGraph RetryPolicy와 중복되지 않게 한다."""

LEASE_RENEW_INTERVAL_SECONDS = 30
SSE_HEARTBEAT_INTERVAL_SECONDS = 10
TEMPLATE_CACHE_TTL_SECONDS = 3600
COMPLETED_REQUEST_RETENTION_DAYS = 30

CHECKPOINT_NAMESPACE = "experience_map"

# HS256 서명 키 최소 길이. RFC 7518 3.2는 해시 출력 길이(32바이트) 이상을 요구한다.
MIN_TICKET_SECRET_BYTES = 32


def _env_int(name: str, default: int) -> int:
    """정수 환경변수 조회 (빈 값·비정수는 기본값으로 대체)"""
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_positive_int(name: str, default: int) -> int:
    """1 이상인 정수 환경변수 조회. 그 밖의 값은 기본값으로 되돌린다.

    `_env_int`와 달리 **0과 음수도 거른다.** 둘 다 정수라 파싱은 되지만 한도로
    쓰이면 "모든 요청 차단"이 되어 기능이 통째로 죽는다. 오타 하나로 기능이
    내려가면 안 되므로 기본값으로 되돌리고 경고를 남긴다.

    끄고 싶을 때 쓰는 스위치는 따로 있다 (`EXPERIENCE_MAP_ENABLED=false`).
    """
    raw = os.getenv(name, "")
    if not raw.strip():
        return default

    try:
        value = int(raw)
    except ValueError:
        value = None

    if value is None or value < 1:
        logger.warning(
            "%s 값이 올바르지 않아 기본값 %d 를 사용합니다 (입력=%r)", name, default, raw
        )
        return default
    return value


class NodeTimeouts(BaseModel):
    """노드별 제한 시간 (초)"""

    llm: int = Field(60, description="일반 LLM 노드")
    file: int = Field(120, description="파일처리 (파서·OCR)")
    gap: int = Field(30, description="gap 분석과 제안 생성")


class ExperienceMapSettings(BaseModel):
    """경험정리 기능 설정"""

    main_backend_url: str | None = Field(None, description="커밋·템플릿 API 호출 대상")
    service_api_key: str | None = Field(
        None, description="인바운드 전용 (메인 → AI). AI_SERVICE_API_KEY"
    )
    main_backend_api_key: str | None = Field(
        None, description="아웃바운드 전용 (AI → 메인). MAIN_BACKEND_API_KEY"
    )
    ticket_secret: str | None = Field(None, description="티켓 HS256 서명 키. 위 두 키와 모두 별도")
    upload_bucket: str | None = Field(None, description="임시 첨부 파일 GCS bucket")

    retry_ttl_seconds: int = Field(1800, description="사용자 재시도 허용 시간")
    file_ttl_seconds: int = Field(3600, description="추출 실패 파일 보관 시간")
    request_lease_seconds: int = Field(300, description="요청 실행 lease")
    rate_limit_per_minute: int = Field(
        DEFAULT_MAX_REQUESTS, description="티켓 sub 단위 분당 요청 수"
    )
    timeouts: NodeTimeouts = Field(default_factory=NodeTimeouts)

    enabled: bool = Field(False, description="기능 노출 여부 (EXPERIENCE_MAP_ENABLED)")
    demo_mode: bool = Field(
        False, description="로컬 Swagger 데모 노출 여부 (EXPERIENCE_MAP_DEMO_MODE)"
    )
    test_ui_enabled: bool = Field(
        False,
        description="내부 수동 테스트 UI 노출 여부 (EXPERIENCE_MAP_TEST_UI_ENABLED)",
    )

    @property
    def ticket_secret_is_distinct(self) -> bool:
        """티켓 서명 키가 **양방향 API 키 어느 쪽과도** 겹치지 않는지 여부

        티켓은 프론트가 직접 들고 다닌다. 서명 키가 서버 간 API 키와 같으면
        둘 중 하나만 새어도 임의 사용자의 세션 티켓을 위조할 수 있다.
        """
        if not self.ticket_secret:
            return True
        return self.ticket_secret not in {
            key for key in (self.service_api_key, self.main_backend_api_key) if key
        }

    @property
    def ticket_secret_is_strong(self) -> bool:
        """티켓 서명 키가 HS256 최소 길이를 만족하는지 여부

        짧은 키는 HMAC 강도를 떨어뜨린다. 티켓이 위조되면 임의 사용자의 세션에
        접근할 수 있으므로 운영 배포 전에 확인해야 한다.
        """
        if not self.ticket_secret:
            return False
        return len(self.ticket_secret.encode("utf-8")) >= MIN_TICKET_SECRET_BYTES


def load_settings() -> ExperienceMapSettings:
    """환경변수에서 설정을 읽는다."""
    return ExperienceMapSettings(
        main_backend_url=os.getenv("MAIN_BACKEND_URL") or None,
        service_api_key=os.getenv("AI_SERVICE_API_KEY") or None,
        main_backend_api_key=os.getenv("MAIN_BACKEND_API_KEY") or None,
        ticket_secret=os.getenv("EXPMAP_TICKET_SECRET") or None,
        upload_bucket=os.getenv("EXPMAP_UPLOAD_BUCKET") or None,
        retry_ttl_seconds=_env_int("EXPMAP_RETRY_TTL_SECONDS", 1800),
        file_ttl_seconds=_env_int("EXPMAP_FILE_TTL_SECONDS", 3600),
        request_lease_seconds=_env_int("EXPMAP_REQUEST_LEASE_SECONDS", 300),
        rate_limit_per_minute=_env_positive_int(
            "EXPMAP_RATE_LIMIT_PER_MINUTE", DEFAULT_MAX_REQUESTS
        ),
        timeouts=NodeTimeouts(
            llm=_env_int("EXPMAP_LLM_TIMEOUT_SECONDS", 60),
            file=_env_int("EXPMAP_FILE_TIMEOUT_SECONDS", 120),
            gap=_env_int("EXPMAP_GAP_TIMEOUT_SECONDS", 30),
        ),
        enabled=os.getenv("EXPERIENCE_MAP_ENABLED", "").strip().lower() in {"1", "true", "yes"},
        demo_mode=os.getenv("EXPERIENCE_MAP_DEMO_MODE", "").strip().lower() in {"1", "true", "yes"},
        test_ui_enabled=os.getenv("EXPERIENCE_MAP_TEST_UI_ENABLED", "").strip().lower()
        in {"1", "true", "yes"},
    )


@lru_cache(maxsize=1)
def get_settings() -> ExperienceMapSettings:
    """설정 싱글톤 반환"""
    return load_settings()


def reset_settings() -> None:
    """설정 캐시 초기화 (테스트용)"""
    get_settings.cache_clear()
