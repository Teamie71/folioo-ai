"""메인 서버 없이 경험 맵 흐름을 재현하는 in-memory 데모."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.schemas.experience_map import ExperienceMapEvent
from features.experience_map import graph as graph_module
from features.experience_map.coordinator import coordinate
from features.experience_map.graph_runner import CheckpointGraphRunner
from features.experience_map.schemas import AppliedItem, CommitResult
from features.experience_map.state import ExperienceMapState

DEMO_MESSAGE = "결제 오류 원인을 분석하고 재시도 로직을 추가해 장애를 줄였다."
_run_lock = asyncio.Lock()


async def run_demo() -> tuple[list[dict], list[dict[str, str]]]:
    """결정적 데모 turn을 실행해 SSE event와 가상 맵 변경을 반환한다."""
    demo_map: list[dict[str, str]] = [{"block_id": "305", "content": "기존 문제 해결 내용"}]
    state: ExperienceMapState = {
        "user_id": "9000001",
        "session_id": "demo-session-1",
        "request_id": "demo-request-1",
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
        "alias_metadata": {
            "exp_1": {
                "block_id": "101",
                "parent_alias": None,
                "level": 2,
                "kind": "experience",
                "is_text_editable": False,
            },
            "b_1": {
                "block_id": "305",
                "parent_alias": "exp_1",
                "level": 3,
                "kind": "category",
                "is_text_editable": False,
            },
        },
        "activity_contexts": {},
        "block_id_to_content": {"305": "기존 문제 해결 내용"},
        "block_id_to_experience_alias": {"305": "exp_1"},
    }

    async with _run_lock:
        with (
            patch.object(graph_module, "route", _route),
            patch.object(graph_module, "filter_content", _filter),
            patch.object(graph_module, "select_target_activity", _select_target),
            patch.object(graph_module, "structure_blocks", _structure),
            patch.object(graph_module, "refine_text", _refine),
        ):
            runner = CheckpointGraphRunner(
                graph_module.build_graph(checkpointer=InMemorySaver()),
                state_events=lambda final_state: _events(final_state, demo_map),
            )
            events = [event.model_dump(mode="json") async for event in runner.run(state)]
    return events, demo_map


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
            {"item_id": "demo_1", "action": "add", "parent_ref": "b_1", "text": DEMO_MESSAGE}
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
            request_id="demo-request-1",
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


__all__ = ["DEMO_MESSAGE", "run_demo"]
