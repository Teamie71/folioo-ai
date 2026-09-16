"""경험정리 처리 그래프 (에이전트 문서 4절, 5-7)."""

from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

from features.experience_map.config import NODE_MAX_ATTEMPTS
from features.experience_map.nodes.content_filter import filter_content
from features.experience_map.nodes.content_filter import next_node as filter_next
from features.experience_map.nodes.fallback import fallback
from features.experience_map.nodes.file_processor import cleanup_extracted_files, process_files
from features.experience_map.nodes.file_processor import next_node as file_next
from features.experience_map.nodes.refine import refine_text
from features.experience_map.nodes.router import next_node as router_next
from features.experience_map.nodes.router import route
from features.experience_map.nodes.structure import structure_blocks
from features.experience_map.nodes.target_activity import select_target_activity
from features.experience_map.nodes.validate import next_node as validate_next
from features.experience_map.nodes.validate import validate_operations
from features.experience_map.state import ExperienceMapState

RETRY_POLICY = RetryPolicy(max_attempts=NODE_MAX_ATTEMPTS)


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Router부터 validate까지의 실제 LangGraph를 compile한다.

    commit·gap 분석은 service coordinator(3.18~3.20)의 책임이므로 validate 성공은
    ``END``로 끝내고 호출자가 `commit_items`를 소비한다.
    """
    graph = StateGraph(ExperienceMapState)
    graph.add_node("router", route, retry_policy=RETRY_POLICY)
    graph.add_node("file_processor", process_files, retry_policy=RETRY_POLICY)
    graph.add_node("file_cleanup", cleanup_extracted_files)
    graph.add_node("content_filter", filter_content, retry_policy=RETRY_POLICY)
    graph.add_node("target_activity", select_target_activity, retry_policy=RETRY_POLICY)
    # structure는 누락 원문만 좁혀서 배치별로 내부에서 한 번 보정하지만, 그 보정은
    # 배치 루프 안에서만 동작한다 — 모든 배치가 끝난 뒤의 카탈로그 슬롯 채우기·
    # 앵커 재사용·최종 _validate_output 은 이 노드 안에서 재시도되지 않는다.
    # 공통 RetryPolicy를 완전히 빼면 그 마지막 단계의 일시적 실패(카탈로그 조회
    # 순단, 드물게 나쁜 배치 조합으로 인한 검증 실패 등)가 재시도 없이 바로
    # 사용자 하드 실패로 이어진다 — 다른 모든 노드는 여전히 1회 자동 재시도를
    # 받는데 structure만 못 받는 비대칭이 생긴다. 최악의 경우 배치당 LLM 호출이
    # 최대 네 번(배치 내부 2회 × 그래프 재시도 2회)까지 늘 수 있지만, 그건 배치
    # 내부 재시도와 마지막 단계 실패가 동시에 겹치는 드문 경우에만 일어나므로
    # 감수한다.
    #
    # 예외: 최종 검증이 모델의 자기모순(existing_categories 자기 신고와 실제
    # 출력이 어긋남)으로 실패하면, structure 노드는 이 그래프 레벨 재시도를
    # 기다리지 않고 노드 실행 안에서 온도를 올려 배치 루프 전체를 한 번 더
    # 돈다(`nodes/structure.py`의 `_SelfContradictionError` 처리) — 같은
    # temperature=0 프롬프트를 그대로 반복하면 이 실패는 결정론적으로 재발해
    # 그래프 레벨 재시도가 무의미하기 때문이다. 이 경로가 걸리면 배치당 LLM
    # 호출이 최대 여덟 번(배치 루프 전체 2회 × 배치 내부 2회 × 그래프 재시도
    # 2회)까지 늘 수 있다.
    graph.add_node("structure", structure_blocks, retry_policy=RETRY_POLICY)
    graph.add_node("refine", refine_text, retry_policy=RETRY_POLICY)
    graph.add_node("validate", _validate_async)
    graph.add_node("fallback", fallback)

    graph.set_entry_point("router")
    graph.add_conditional_edges(
        "router",
        _router_next_async,
        {
            "file_processor": "file_processor",
            "content_filter": "content_filter",
            "fallback": "fallback",
        },
    )
    graph.add_edge("file_processor", "file_cleanup")
    graph.add_conditional_edges(
        "file_cleanup",
        _file_next_async,
        {"content_filter": "content_filter", "fallback": "fallback"},
    )
    graph.add_conditional_edges(
        "content_filter",
        _after_filter_async,
        {"target_activity": "target_activity", "fallback": "fallback"},
    )
    graph.add_conditional_edges(
        "target_activity",
        _after_target_async,
        {"structure": "structure", "refine": "refine", "fallback": "fallback"},
    )
    graph.add_edge("structure", "refine")
    graph.add_edge("refine", "validate")
    graph.add_conditional_edges(
        "validate",
        _validate_next_async,
        {"structure": "structure", "refine": "refine", "coordinator": END, "fallback": "fallback"},
    )
    graph.add_edge("fallback", END)
    return graph.compile(checkpointer=checkpointer)


def build_commit_recovery_graph(entry_node: str):
    """map version 충돌 뒤 structure 또는 validate부터 재처리하는 그래프를 만든다.

    최초 요청 그래프는 validate 성공 시 종료되므로, 커밋 시점에 발견한 version 충돌은
    별도 짧은 그래프로 재처리한다. 같은 노드와 RetryPolicy를 사용해 최초 실행과 보정
    규칙이 달라지지 않게 한다.
    """
    if entry_node not in {"structure", "validate"}:
        raise ValueError(f"지원하지 않는 커밋 복구 진입점입니다: {entry_node}")

    graph = StateGraph(ExperienceMapState)
    graph.add_node("structure", structure_blocks, retry_policy=RETRY_POLICY)
    graph.add_node("refine", refine_text, retry_policy=RETRY_POLICY)
    graph.add_node("validate", _validate_async)
    graph.add_node("fallback", fallback)

    graph.set_entry_point(entry_node)
    graph.add_edge("structure", "refine")
    graph.add_edge("refine", "validate")
    graph.add_conditional_edges(
        "validate",
        _validate_next_async,
        {"structure": "structure", "refine": "refine", "coordinator": END, "fallback": "fallback"},
    )
    graph.add_edge("fallback", END)
    return graph.compile()


async def recover_commit_state(
    state: ExperienceMapState, entry_node: Literal["validate", "structure"]
) -> ExperienceMapState:
    """최신 맵 state를 지정 노드부터 재실행해 새 commit items를 확정한다."""
    result = await build_commit_recovery_graph(entry_node).ainvoke(dict(state))
    if result.get("fallback_reason") or not result.get("commit_items"):
        from features.experience_map.errors import ValidationFailedError

        raise ValidationFailedError(failed_node="validate")
    return result


async def _validate_async(state: ExperienceMapState) -> ExperienceMapState:
    """작은 동기 검증 함수를 LangGraph executor 없이 실행한다."""
    return validate_operations(state)


async def _router_next_async(state: ExperienceMapState) -> str:
    return router_next(state)


async def _file_next_async(state: ExperienceMapState) -> str:
    return file_next(state)


async def _after_filter_async(state: ExperienceMapState) -> str:
    return _after_filter(state)


async def _after_target_async(state: ExperienceMapState) -> str:
    return _after_target(state)


async def _validate_next_async(state: ExperienceMapState) -> str:
    return validate_next(state)


def _after_target(state: ExperienceMapState) -> str:
    """대상 선택 후 filter 분류가 결정한 구조화·정제 경로로 간다."""
    if state.get("fallback_reason") or not state.get("target_experience_alias"):
        return "fallback"
    return filter_next(state)


def _after_filter(state: ExperienceMapState) -> str:
    """반영할 내용이 없으면 대상 활동 선택 LLM을 호출하지 않는다."""
    return "fallback" if filter_next(state) == "fallback" else "target_activity"


graph = build_graph()
