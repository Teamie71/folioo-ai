"""Checkpointer 팩토리 (비동기 PostgreSQL)"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

load_dotenv()

# 체크아웃 시 연결 생존을 능동 검증하는 함수 (mock 패치와 분리하기 위해 미리 캡처)
_CHECK_CONNECTION = AsyncConnectionPool.check_connection

# 모듈 레벨 싱글톤
_checkpointer: AsyncPostgresSaver | None = None


@asynccontextmanager
async def setup_checkpointer():
    """
    비동기 Checkpointer 생성 및 정리를 위한 async context manager

    애플리케이션 시작 시 호출하여 checkpointer를 초기화하고,
    종료 시 자동으로 정리합니다.
    """
    global _checkpointer

    db_url = os.getenv("CHECKPOINT_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError(
            "CHECKPOINT_DATABASE_URL 또는 DATABASE_URL 환경변수가 설정되지 않았습니다."
        )

    async with AsyncConnectionPool(
        conninfo=db_url,
        max_size=20,
        max_idle=240,
        check=_CHECK_CONNECTION,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        _checkpointer = checkpointer
        try:
            yield checkpointer
        finally:
            _checkpointer = None


def get_checkpointer() -> AsyncPostgresSaver:
    """
    비동기 PostgreSQL 기반 Checkpointer 반환 (싱글톤)

    Returns:
        AsyncPostgresSaver: Checkpointer 인스턴스

    주의: setup_checkpointer()가 애플리케이션 시작 시 호출되어야 합니다.
    """

    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer가 초기화되지 않았습니다. "
            "애플리케이션 시작 시 setup_checkpointer()를 호출하세요."
        )

    return _checkpointer


def reset_checkpointer() -> None:
    """
    Checkpointer 싱글톤 초기화 (테스트용)
    """
    global _checkpointer
    _checkpointer = None
