"""
중앙집중 로깅 설정

- uvicorn 로그와 동일한 컬러/포맷으로 통일
- 모든 모듈의 getLogger(__name__) 로거가 자동으로 이 설정을 상속
- 앱 시작 시 setup_logging() 1회 호출
"""

import logging
import sys


# ─── ANSI Color Codes ─────────────────────────────────────────────
class _Colors:
    """ANSI 이스케이프 코드 (uvicorn 스타일 매칭)"""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # 레벨별 색상 (uvicorn과 동일)
    DEBUG = "\033[36m"  # cyan
    INFO = "\033[32m"  # green
    WARNING = "\033[33m"  # yellow
    ERROR = "\033[31m"  # red
    CRITICAL = "\033[1;31m"  # bold red

    # 기타
    LOGGER_NAME = "\033[35m"  # magenta (로거 이름)
    TIMESTAMP = "\033[90m"  # gray (타임스탬프)
    MESSAGE = "\033[0m"  # default (메시지)


_LEVEL_COLORS = {
    logging.DEBUG: _Colors.DEBUG,
    logging.INFO: _Colors.INFO,
    logging.WARNING: _Colors.WARNING,
    logging.ERROR: _Colors.ERROR,
    logging.CRITICAL: _Colors.CRITICAL,
}


# ─── Custom Formatter ─────────────────────────────────────────────
class ColorFormatter(logging.Formatter):
    """
    uvicorn 스타일 컬러 로그 포매터

    출력 형식:
      INFO:     features.interview.service - 메시지 내용
    (레벨 색상) (마젠타 로거명)            (기본색 메시지)
    """

    def format(self, record: logging.LogRecord) -> str:
        level_color = _LEVEL_COLORS.get(record.levelno, _Colors.RESET)
        level_name = record.levelname

        # uvicorn 스타일: 레벨명을 고정폭으로 패딩 (좌측 정렬)
        padded_level = f"{level_name}:".ljust(10)

        # 로거 이름 축약 (너무 길면 마지막 2세그먼트만)
        logger_name = record.name
        parts = logger_name.split(".")
        if len(parts) > 2:
            logger_name = ".".join(parts[-2:])

        # 메시지 포맷
        message = record.getMessage()

        # 예외 정보가 있으면 추가
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)

        formatted = (
            f"{level_color}{padded_level}{_Colors.RESET}"
            f"{_Colors.LOGGER_NAME}{logger_name}{_Colors.RESET} - "
            f"{message}"
        )

        if record.exc_text:
            formatted += f"\n{_Colors.ERROR}{record.exc_text}{_Colors.RESET}"

        return formatted


class PlainFormatter(logging.Formatter):
    """
    비-TTY 환경을 위한 컬러 없는 포매터
    """

    def format(self, record: logging.LogRecord) -> str:
        padded_level = f"{record.levelname}:".ljust(10)

        logger_name = record.name
        parts = logger_name.split(".")
        if len(parts) > 2:
            logger_name = ".".join(parts[-2:])

        message = record.getMessage()

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)

        formatted = f"{padded_level}{logger_name} - {message}"

        if record.exc_text:
            formatted += f"\n{record.exc_text}"

        return formatted


# ─── Setup Function ────────────────────────────────────────────────
def setup_logging(level: int = logging.INFO) -> None:
    """
    애플리케이션 로깅 설정 초기화

    - Root logger에 컬러 핸들러 등록
    - uvicorn 로거 포맷을 동일한 스타일로 통일
    - 이미 핸들러가 등록된 경우 중복 방지

    Args:
        level: 로그 레벨 (기본: INFO)
    """

    root_logger = logging.getLogger()

    # 이미 설정되었으면 스킵 (중복 호출 방지)
    if getattr(root_logger, "_folioo_configured", False):
        return

    # 기존 핸들러 제거 (uvicorn이 미리 설정한 것 포함)
    root_logger.handlers.clear()

    # 핸들러 생성
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    # TTY 여부에 따라 포매터 선택
    if hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
        formatter = ColorFormatter()
    else:
        formatter = ColorFormatter()  # CI/Docker에서도 색상 출력 (대부분 지원)

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # uvicorn 로거들도 동일한 포매터 사용
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(uvicorn_logger_name)
        uv_logger.handlers.clear()
        uv_logger.addHandler(handler)
        uv_logger.propagate = False  # root로 중복 전파 방지

    # 너무 시끄러운 라이브러리 로거 레벨 조정
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)

    # 마커 설정 (중복 호출 방지)
    root_logger._folioo_configured = True  # noqa: SLF001

    # 초기화 완료 로그
    app_logger = logging.getLogger("folioo-ai")
    app_logger.info("🎨 Folioo AI 로깅 초기화 완료 (level=%s)", logging.getLevelName(level))
