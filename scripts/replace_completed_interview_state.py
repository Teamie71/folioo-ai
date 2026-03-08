"""
기존 인터뷰 세션을 완료 상태 템플릿으로 교체하는 스크립트

이미 SQLite 체크포인터에 저장된 session_id를 입력받아,
해당 세션의 상태를 "대화가 모두 끝난 인터뷰" 상태로 덮어씁니다.

사용법:
    uv run python scripts/replace_completed_interview_state.py --session-id <session_id>
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (직접 실행 시 모듈 탐색용)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from features.interview.agents.graph import build_graph
from features.interview.agents.state import InterviewState
from scripts.seed_completed_interview import build_completed_state

load_dotenv()


def parse_args() -> argparse.Namespace:
    """CLI 인자 파싱"""
    parser = argparse.ArgumentParser(
        description="기존 인터뷰 세션을 완료 상태 템플릿으로 덮어씁니다."
    )
    parser.add_argument(
        "--session-id",
        required=True,
        help="SQLite 체크포인터에 이미 저장된 대상 세션 ID",
    )
    return parser.parse_args()


def build_replacement_state(existing_state: InterviewState) -> InterviewState:
    """기존 세션 식별 정보를 유지한 완료 상태 템플릿 생성"""
    session_id = existing_state["session_id"]
    replacement_state = build_completed_state(session_id)

    replacement_state["user_id"] = existing_state["user_id"]
    replacement_state["session_id"] = session_id
    replacement_state["experience_name"] = existing_state["experience_name"]

    if "extension_turns_max" in existing_state:
        replacement_state["extension_turns_max"] = existing_state["extension_turns_max"]

    return replacement_state


async def replace_session_state(session_id: str) -> dict:
    """대상 세션을 완료 상태 템플릿으로 교체하고 결과를 반환"""
    db_path = os.getenv("CHECKPOINT_DB_PATH", ".data/checkpoints.sqlite")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": session_id}}

        snapshot = await graph.aget_state(config)
        if snapshot is None or not snapshot.values:
            raise ValueError(f"세션 상태를 찾을 수 없습니다: {session_id}")

        replacement_state = build_replacement_state(snapshot.values)
        await graph.aupdate_state(config, replacement_state)

        updated_snapshot = await graph.aget_state(config)
        if updated_snapshot is None or not updated_snapshot.values:
            raise ValueError(f"세션 상태 갱신에 실패했습니다: {session_id}")

        updated_state = updated_snapshot.values
        return {
            "db_path": db_path,
            "session_id": session_id,
            "user_id": updated_state["user_id"],
            "experience_name": updated_state["experience_name"],
            "current_stage": updated_state["current_stage"],
            "all_stages_complete": updated_state["all_stages_complete"],
            "overall_completion_percentage": updated_state["overall_completion_percentage"],
        }


async def main() -> None:
    """스크립트 실행 진입점"""
    args = parse_args()
    result = await replace_session_state(args.session_id)

    print()
    print("=" * 70)
    print("  인터뷰 세션 상태 교체 완료")
    print("=" * 70)
    print(f"  DB: {result['db_path']}")
    print(f"  session_id: {result['session_id']}")
    print(f"  user_id: {result['user_id']}")
    print(f"  experience_name: {result['experience_name']}")
    print(f"  current_stage: {result['current_stage']}")
    print(f"  all_stages_complete: {result['all_stages_complete']}")
    print(f"  overall_completion_percentage: {result['overall_completion_percentage']}%")
    print()


if __name__ == "__main__":
    asyncio.run(main())
