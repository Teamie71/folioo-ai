"""메인 서버 없이 경험 맵 처리 흐름을 확인하는 로컬 데모.

실제 LangGraph 배선, validate, coordinator와 SSE 이벤트 모델을 실행한다. 외부 LLM,
템플릿 API, 커밋 API만 결정적인 in-memory 대역으로 바꾸므로 API 키나 메인 서버 없이
입력 → 처리 이벤트 → 가상 맵 변경을 확인할 수 있다.

실행:
    uv run python scripts/experience_map/demo.py
"""

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

# `uv run python scripts/...`로 실행할 때도 저장소 패키지를 찾게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schemas.experience_map import ExperienceMapEvent
from features.experience_map import graph as graph_module
from features.experience_map.coordinator import coordinate
from features.experience_map.graph_runner import CheckpointGraphRunner
from features.experience_map.schemas import AppliedItem, CommitResult
from features.experience_map.state import ExperienceMapState

DEMO_USER_ID = "9000001"
DEMO_SESSION_ID = "demo-session-1"
DEMO_REQUEST_ID = "demo-request-1"
DEMO_MESSAGE = "결제 오류 원인을 분석하고 재시도 로직을 추가해 장애를 줄였다."


async def _route(state: ExperienceMapState) -> ExperienceMapState:
    return {**state, "intent": "chat_input", "current_node": "router"}


async def _filter(state: ExperienceMapState) -> ExperienceMapState:
    return {
        **state,
        "current_node": "content_filter",
        "gap_answer_items": [],
        "new_items": [{"item_id": "demo_1", "text": DEMO_MESSAGE, "source": "message"}],
        "excluded_reasons": [],
    }


async def _select_target(state: ExperienceMapState) -> ExperienceMapState:
    return {
        **state,
        "current_node": "target_activity",
        "target_experience_alias": "exp_1",
        "activity_tree_text": "[exp_1] 교내 커머스 리뉴얼\n  [b_1] 문제 해결",
    }


async def _structure(state: ExperienceMapState) -> ExperienceMapState:
    return {
        **state,
        "current_node": "structure",
        "structured_items": [
            {
                "item_id": "demo_1",
                "action": "add",
                "parent_ref": "b_1",
                "text": DEMO_MESSAGE,
            }
        ],
    }


async def _refine(state: ExperienceMapState) -> ExperienceMapState:
    return {
        **state,
        "current_node": "refine",
        "refined_items": [
            {
                "item_id": "demo_1",
                "refined_text": "결제 오류 원인 분석 → 재시도 로직 추가로 장애 감소",
            }
        ],
    }


async def _events(
    state: ExperienceMapState, demo_map: list[dict[str, str]]
) -> AsyncIterator[ExperienceMapEvent]:
    async def commit(commit_state: ExperienceMapState) -> ExperienceMapState:
        item = commit_state["commit_items"][0]
        demo_map.append({"parent": item["parent_ref"], "content": item["text"]})
        result = CommitResult(
            request_id=DEMO_REQUEST_ID,
            previous_version=1,
            map_version=2,
            revert_to_version=1,
            can_revert=True,
            applied=[
                AppliedItem(
                    item_id="demo_1", block_id="1001", path="교내 커머스 리뉴얼 > 문제 해결"
                )
            ],
            dropped=[],
        )
        return {**commit_state, "commit_result": result.model_dump()}

    async def gap(gap_state: ExperienceMapState) -> ExperienceMapState:
        return {
            **gap_state,
            "suggestion": {"gap": None, "message": "더 정리하고 싶으신 내용이 있나요?"},
        }

    async for event in coordinate(state, commit_runner=commit, gap_runner=gap):
        yield event


async def main() -> None:
    """데모 입력을 실행하고 SSE 형식 이벤트와 가상 맵 변경을 출력한다."""
    demo_map: list[dict[str, str]] = [{"block_id": "305", "content": "기존 문제 해결 내용"}]
    initial_state: ExperienceMapState = {
        "user_id": DEMO_USER_ID,
        "session_id": DEMO_SESSION_ID,
        "request_id": DEMO_REQUEST_ID,
        "request_hash": "d" * 64,
        "user_message": DEMO_MESSAGE,
        "outline": [
            {
                "level": 1,
                "title": "프로젝트",
                "children": [{"alias": "exp_1", "level": 2, "title": "교내 커머스 리뉴얼"}],
            }
        ],
        "alias_to_block_id": {"exp_1": "101", "b_1": "305"},
        "activity_contexts": {},
        "block_id_to_content": {"305": "기존 문제 해결 내용"},
        "block_id_to_experience_alias": {"305": "exp_1"},
    }

    with (
        patch.object(graph_module, "route", _route),
        patch.object(graph_module, "filter_content", _filter),
        patch.object(graph_module, "select_target_activity", _select_target),
        patch.object(graph_module, "structure_blocks", _structure),
        patch.object(graph_module, "refine_text", _refine),
    ):
        runner = CheckpointGraphRunner(
            graph_module.build_graph(checkpointer=InMemorySaver()),
            state_events=lambda state: _events(state, demo_map),
        )
        print("=== 경험 맵 로컬 데모 SSE ===")
        async for event in runner.run(initial_state):
            print(f"data: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}")

    print("\n=== 가상 경험 맵 ===")
    print(json.dumps(demo_map, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
