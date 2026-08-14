"""경험정리 처리 그래프 (에이전트 문서 4절, 5-7)."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

from features.experience_map.config import NODE_MAX_ATTEMPTS
from features.experience_map.nodes.content_filter import filter_content
from features.experience_map.nodes.content_filter import next_node as filter_next
from features.experience_map.nodes.fallback import fallback
from features.experience_map.nodes.file_processor import next_node as file_next
from features.experience_map.nodes.file_processor import process_files
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
    graph.add_node("content_filter", filter_content, retry_policy=RETRY_POLICY)
    graph.add_node("target_activity", select_target_activity, retry_policy=RETRY_POLICY)
    graph.add_node("structure", structure_blocks, retry_policy=RETRY_POLICY)
    graph.add_node("refine", refine_text, retry_policy=RETRY_POLICY)
    graph.add_node("validate", validate_operations)
    graph.add_node("fallback", fallback)

    graph.set_entry_point("router")
    graph.add_conditional_edges(
        "router",
        router_next,
        {
            "file_processor": "file_processor",
            "content_filter": "content_filter",
            "fallback": "fallback",
        },
    )
    graph.add_conditional_edges(
        "file_processor", file_next, {"content_filter": "content_filter", "fallback": "fallback"}
    )
    graph.add_conditional_edges(
        "content_filter",
        _after_filter,
        {"target_activity": "target_activity", "fallback": "fallback"},
    )
    graph.add_conditional_edges(
        "target_activity",
        _after_target,
        {"structure": "structure", "refine": "refine", "fallback": "fallback"},
    )
    graph.add_edge("structure", "refine")
    graph.add_edge("refine", "validate")
    graph.add_conditional_edges(
        "validate",
        validate_next,
        {"structure": "structure", "refine": "refine", "coordinator": END, "fallback": "fallback"},
    )
    graph.add_edge("fallback", END)
    return graph.compile(checkpointer=checkpointer)


def _after_target(state: ExperienceMapState) -> str:
    """대상 선택 후 filter 분류가 결정한 구조화·정제 경로로 간다."""
    if state.get("fallback_reason") or not state.get("target_experience_alias"):
        return "fallback"
    return filter_next(state)


def _after_filter(state: ExperienceMapState) -> str:
    """반영할 내용이 없으면 대상 활동 선택 LLM을 호출하지 않는다."""
    return "fallback" if filter_next(state) == "fallback" else "target_activity"


graph = build_graph()
