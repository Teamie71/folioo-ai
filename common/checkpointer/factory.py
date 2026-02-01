"""Checkpointer 팩토리"""

import os
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver

load_dotenv()

# 모듈 레벨 싱글톤
_checkpointer: SqliteSaver | None = None


def get_checkpointer() -> SqliteSaver:
    """
    SQLite 기반 Checkpointer 반환 (싱글톤)
    Returns:
        SqliteSaver: LangGraph 상태 영속화를 위한 checkpointer
    Notes:
        - 개발 환경: .data/checkpoints.sqlite
        - 프로덕션: 환경변수 CHECKPOINT_DB_PATH 사용
        - .data/ 디렉토리는 .gitignore에 추가
    """
    global _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    db_path = os.getenv("CHECKPOINT_DB_PATH", ".data/checkpoints.sqlite")

    # 디렉토리 생성
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # SqliteSaver 생성 (자동으로 테이블 생성)
    _checkpointer = SqliteSaver.from_conn_string(db_path)

    return _checkpointer


def reset_checkpointer() -> None:
    """
    Checkpointer 싱글톤 초기화 (테스트용)
    테스트 간 격리를 위해 사용
    """
    global _checkpointer
    _checkpointer = None
