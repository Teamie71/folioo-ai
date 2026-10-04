"""경험 맵 DB 커넥션 풀 관리 (asyncpg)

LangGraph checkpoint DB(`CHECKPOINT_DATABASE_URL`)와 분리된 별도 풀이다.
경험 맵 DB는 메인 서버가 소유하며 AI 서버 계정은 읽기 전용이다.
"""

import logging
import os

import asyncpg

logger = logging.getLogger(__name__)

DEFAULT_POOL_MIN_SIZE = 1
DEFAULT_POOL_MAX_SIZE = 3
DEFAULT_STATEMENT_TIMEOUT_MS = 5_000

_pool: asyncpg.Pool | None = None


def _env_int(name: str, default: int) -> int:
    """
    정수 환경변수 조회 (빈 값·비정수는 기본값으로 대체)

    Args:
        name: 환경변수 이름
        default: 값이 없거나 정수가 아닐 때 사용할 기본값

    Returns:
        int: 환경변수 정수값 또는 기본값
    """
    raw = os.getenv(name, "")
    if not raw.strip():
        return default

    try:
        return int(raw)
    except ValueError:
        logger.warning("%s 값이 정수가 아니라 기본값 %d을(를) 사용합니다: %r", name, default, raw)
        return default


async def create_pool() -> asyncpg.Pool:
    """
    DATABASE_URL 환경변수로 경험 맵 DB asyncpg 커넥션 풀 생성

    Returns:
        asyncpg.Pool: 생성된 커넥션 풀

    Raises:
        ValueError: DATABASE_URL 환경변수가 설정되지 않은 경우
    """
    global _pool

    # 이미 유효한 풀이 있으면 재사용 (멱등성 보장)
    if _pool is not None and not _pool.is_closing():
        return _pool

    # 닫힌 풀이 남아 있으면 정리
    if _pool is not None:
        await _pool.close()
        _pool = None

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL 환경변수가 설정되지 않았습니다.")

    min_size = _env_int("DB_POOL_MIN_SIZE", DEFAULT_POOL_MIN_SIZE)
    max_size = _env_int("DB_POOL_MAX_SIZE", DEFAULT_POOL_MAX_SIZE)
    statement_timeout_ms = _env_int("DB_STATEMENT_TIMEOUT_MS", DEFAULT_STATEMENT_TIMEOUT_MS)

    _pool = await asyncpg.create_pool(
        db_url,
        min_size=min_size,
        max_size=max_size,
        # 서버 측 statement timeout. 장시간 질의가 읽기 전용 계정을 붙잡지 않게 한다.
        server_settings={"statement_timeout": str(statement_timeout_ms)},
        # 클라이언트 측 상한. 서버 timeout이 동작하지 않는 경우의 이중 방어.
        command_timeout=statement_timeout_ms / 1000,
    )
    logger.info(
        "경험 맵 DB 커넥션 풀 생성 완료 (min=%d, max=%d, statement_timeout=%dms)",
        min_size,
        max_size,
        statement_timeout_ms,
    )
    return _pool


async def close_pool() -> None:
    """커넥션 풀 종료"""
    global _pool

    if _pool:
        await _pool.close()
        _pool = None
        logger.info("경험 맵 DB 커넥션 풀 종료 완료")


def get_pool() -> asyncpg.Pool:
    """
    현재 커넥션 풀 반환 (싱글톤)

    Returns:
        asyncpg.Pool: 현재 커넥션 풀

    Raises:
        RuntimeError: 풀이 초기화되지 않은 경우
    """
    if _pool is None:
        raise RuntimeError(
            "DB 커넥션 풀이 초기화되지 않았습니다. 애플리케이션 시작 시 create_pool()을 호출하세요."
        )
    return _pool


def get_pool_status() -> str:
    """
    헬스체크용 커넥션 풀 상태 반환

    Returns:
        str: 사용 가능하면 "connected", 아니면 "disconnected"
    """
    if _pool is None or _pool.is_closing():
        return "disconnected"
    return "connected"
