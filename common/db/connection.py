"""공유 DB 커넥션 풀 관리 (asyncpg)"""

import logging
import os

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def create_pool() -> asyncpg.Pool:
    """
    DATABASE_URL 환경변수로 asyncpg 커넥션 풀 생성

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

    _pool = await asyncpg.create_pool(db_url)
    logger.info("DB 커넥션 풀 생성 완료")
    return _pool


async def close_pool() -> None:
    """커넥션 풀 종료"""
    global _pool

    if _pool:
        await _pool.close()
        _pool = None
        logger.info("DB 커넥션 풀 종료 완료")


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
