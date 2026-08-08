"""경험정리 에이전트 설정

환경변수는 API 명세 8절을 따른다. DB 연결(`DATABASE_URL`·`CHECKPOINT_DATABASE_URL`)은
`common/db`·`common/checkpointer`가 소유하므로 여기서 다루지 않는다.
"""

import os
from functools import lru_cache

from pydantic import BaseModel, Field

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

# ===== 블록 제약 (API 명세 4-2) =====
MAX_CONTENT_LENGTH = 500
MIN_CONTENT_LENGTH = 1
MAX_BLOCK_LEVEL = 5

# ===== 재시도·보정 (API 명세 2-4) =====
NODE_MAX_ATTEMPTS = 2
"""LangGraph RetryPolicy. 1회 자동 재시도를 의미한다."""

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


def _env_int(name: str, default: int) -> int:
    """정수 환경변수 조회 (빈 값·비정수는 기본값으로 대체)"""
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class NodeTimeouts(BaseModel):
    """노드별 제한 시간 (초)"""

    llm: int = Field(60, description="일반 LLM 노드")
    file: int = Field(120, description="파일처리 (파서·OCR)")
    gap: int = Field(30, description="gap 분석과 제안 생성")


class ExperienceMapSettings(BaseModel):
    """경험정리 기능 설정"""

    main_backend_url: str | None = Field(None, description="커밋·템플릿 API 호출 대상")
    api_key: str | None = Field(None, description="메인 ↔ AI 서버 간 인증 키")
    ticket_secret: str | None = Field(None, description="티켓 HS256 서명 키. api_key와 반드시 별도")
    upload_bucket: str | None = Field(None, description="임시 첨부 파일 GCS bucket")

    retry_ttl_seconds: int = Field(1800, description="사용자 재시도 허용 시간")
    file_ttl_seconds: int = Field(3600, description="추출 실패 파일 보관 시간")
    request_lease_seconds: int = Field(300, description="요청 실행 lease")
    timeouts: NodeTimeouts = Field(default_factory=NodeTimeouts)

    enabled: bool = Field(False, description="기능 노출 여부 (EXPERIENCE_MAP_ENABLED)")

    @property
    def ticket_secret_is_distinct(self) -> bool:
        """티켓 서명 키가 API 키와 분리돼 있는지 여부"""
        if not self.ticket_secret or not self.api_key:
            return True
        return self.ticket_secret != self.api_key


def load_settings() -> ExperienceMapSettings:
    """환경변수에서 설정을 읽는다."""
    return ExperienceMapSettings(
        main_backend_url=os.getenv("MAIN_BACKEND_URL") or None,
        api_key=os.getenv("AI_SERVICE_API_KEY") or None,
        ticket_secret=os.getenv("EXPMAP_TICKET_SECRET") or None,
        upload_bucket=os.getenv("EXPMAP_UPLOAD_BUCKET") or None,
        retry_ttl_seconds=_env_int("EXPMAP_RETRY_TTL_SECONDS", 1800),
        file_ttl_seconds=_env_int("EXPMAP_FILE_TTL_SECONDS", 3600),
        request_lease_seconds=_env_int("EXPMAP_REQUEST_LEASE_SECONDS", 300),
        timeouts=NodeTimeouts(
            llm=_env_int("EXPMAP_LLM_TIMEOUT_SECONDS", 60),
            file=_env_int("EXPMAP_FILE_TIMEOUT_SECONDS", 120),
            gap=_env_int("EXPMAP_GAP_TIMEOUT_SECONDS", 30),
        ),
        enabled=os.getenv("EXPERIENCE_MAP_ENABLED", "").strip().lower() in {"1", "true", "yes"},
    )


@lru_cache(maxsize=1)
def get_settings() -> ExperienceMapSettings:
    """설정 싱글톤 반환"""
    return load_settings()


def reset_settings() -> None:
    """설정 캐시 초기화 (테스트용)"""
    get_settings.cache_clear()
