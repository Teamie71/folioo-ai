"""Checkpointer 팩토리"""

import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver

load_dotenv()

# 모듈 레벨 싱글톤
_checkpointer: SqliteSaver | None = None


@contextmanager
def setup_checkpointer():
    """
    Checkpointer 생성 및 정리를 위한 context manager

    애플리케이션 시작 시 호출하여 checkpointer를 초기화하고,
    종료 시 자동으로 정리합니다.
    """
    global _checkpointer

    db_path = os.getenv("CHECKPOINT_DB_PATH", ".data/checkpoints.sqlite")
    # 디렉토리 생성
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with SqliteSaver.from_conn_string(db_path) as checkpointer:
        _checkpointer = checkpointer
        yield checkpointer

    _checkpointer = None


def get_checkpointer() -> SqliteSaver:
    """
    SQLite 기반 Checkpointer 반환 (싱글톤)

    Returns:
        SqliteSaver: Checkpointer 인스턴스

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
