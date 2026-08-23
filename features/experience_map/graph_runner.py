"""경험정리 그래프 실행기

API 계층과 LangGraph 사이의 경계다. 서비스는 이 Protocol 만 알고, 실제 그래프는
3.17(validate·graph 배선)에서 붙인다.

기본 실행기는 checkpoint가 붙은 실제 LangGraph다. `MockGraphRunner`는 API 계약과
서비스 단위 테스트에서만 명시적으로 주입한다.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from app.schemas.experience_map import (
    CommitResultEvent,
    CompletedMessage,
    ExperienceMapEvent,
    MessageCompleteEvent,
    NodeStatusEvent,
    SuggestionGap,
    SuggestionReadyEvent,
)
from features.experience_map.state import ExperienceMapState

logger = logging.getLogger(__name__)

GRAPH_NODE_NAMES = frozenset(
    {
        "router",
        "file_processor",
        "content_filter",
        "target_activity",
        "structure",
        "refine",
        "validate",
        "fallback",
    }
)
"""`graph.py` 가 `add_node` 로 등록한 노드 이름. `astream_events` 잡음(RunnableSequence
등 내부 컴포넌트)에서 실제 노드 경계만 걸러내는 데 쓴다."""


class GraphRunner(Protocol):
    """그래프 실행 인터페이스

    `processing_started` 와 `processing_complete` 는 서비스가 보낸다. 실행기는
    그 사이의 이벤트만 낸다.
    """

    def run(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        """그래프를 실행하며 SSE 이벤트를 순서대로 낸다."""
        ...

    def resume(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        """실패 지점부터 이어서 실행한다 (사용자 재시도)."""
        ...


StateEventFactory = Callable[[ExperienceMapState], AsyncIterator[ExperienceMapEvent]]


class CheckpointGraphRunner:
    """checkpointer가 붙은 LangGraph를 실행·재개하는 실행기.

    재시도는 새 입력 state를 넣지 않는다. LangGraph가 저장한 실패 superstep부터
    ``ainvoke(None, config)``로 재개하므로 이미 성공한 노드는 다시 호출되지 않는다.
    실제 맵 조회·초기 state 구성은 service의 요청 준비 단계가 소유한다.
    """

    def __init__(
        self,
        graph: Any,
        *,
        state_events: StateEventFactory,
    ) -> None:
        self._graph = graph
        self._state_events = state_events

    async def run(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        """새 요청 state로 graph를 실행한다."""
        async for event in self._stream(state, _thread_config(state)):
            yield event

    async def resume(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        """checkpoint의 실패 지점부터 재개한다."""
        async for event in self._stream(None, _thread_config(state)):
            yield event

    async def _stream(
        self, input_state: ExperienceMapState | None, config: dict
    ) -> AsyncIterator[ExperienceMapEvent]:
        """노드 시작·종료마다 `node_status`를 내며 실행하고, 끝나면 최종 state로
        후속 이벤트를 만든다.

        `ainvoke` 한 번으로 끝내면 실제 요청에서 진행 상태를 하나도 못 보여준다.
        `astream_events`로 노드 경계(on_chain_start/end)만 걸러 듣고, 맨 끝
        `LangGraph` 체인의 출력을 최종 state로 쓴다 — `ainvoke`가 돌려주는 것과 같다.
        """
        result: ExperienceMapState | None = None
        async for event in self._graph.astream_events(input_state, config, version="v2"):
            name = event.get("name")
            if name not in GRAPH_NODE_NAMES:
                if event["event"] == "on_chain_end" and name == "LangGraph":
                    result = event["data"].get("output")
                continue
            if event["event"] == "on_chain_start":
                yield NodeStatusEvent(node=name, status="running")
            elif event["event"] == "on_chain_end":
                yield NodeStatusEvent(node=name, status="completed")

        if result is None:
            raise RuntimeError("그래프 실행이 최종 state를 내지 않았습니다.")
        async for event in self._state_events(result):
            yield event


def _thread_config(state: ExperienceMapState) -> dict[str, dict[str, str]]:
    """경험 맵 checkpoint namespace를 포함한 LangGraph 실행 config를 만든다."""
    session_id = state.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("checkpoint 재개에 필요한 session_id가 없습니다.")
    return {"configurable": {"thread_id": session_id, "checkpoint_ns": "experience_map"}}


class MockGraphRunner:
    """노드 없이 API 이벤트 순서만 재현하는 테스트용 실행기.

    정상 커밋 이벤트 순서 (API 명세 6절):

    ```text
    node_status* → commit_result → message_complete(result)
                 → suggestion_ready → message_complete(suggestion)
    ```
    """

    NODES = ("router", "content_filter", "structure", "refine", "validate", "commit")

    def __init__(self, *, node_delay_seconds: float = 0.0) -> None:
        self._node_delay = node_delay_seconds

    async def run(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        request_id = state["request_id"]
        session_id = state["session_id"]

        for node in self.NODES:
            yield NodeStatusEvent(node=node, status="running")
            if self._node_delay:
                await asyncio.sleep(self._node_delay)
            yield NodeStatusEvent(node=node, status="completed")

        yield CommitResultEvent(
            result={
                "request_id": request_id,
                "previous_version": 42,
                "map_version": 43,
                "revert_to_version": 42,
                "can_revert": True,
                "applied": [
                    {
                        "item_id": "it_1",
                        "block_id": "3701",
                        "path": "교내 커머스 리뉴얼 > 문제해결",
                    }
                ],
                "dropped": [],
            }
        )
        yield MessageCompleteEvent(
            message=CompletedMessage(
                request_id=request_id,
                session_id=session_id,
                response_kind="result",
                ai_response="교내 커머스 리뉴얼 > 문제해결에 1개를 정리했어요.",
                committed=True,
                map_version=43,
                can_revert=True,
            )
        )
        yield SuggestionReadyEvent(
            gap=SuggestionGap(
                gap_id=request_id,
                gap_type="extend_block",
                anchor_block_id="3701",
                path="교내 커머스 리뉴얼 > 문제해결",
                message="그 해결 방법을 고른 기준이 무엇이었나요?",
            )
        )
        yield MessageCompleteEvent(
            message=CompletedMessage(
                request_id=request_id,
                session_id=session_id,
                response_kind="suggestion",
                ai_response="그 해결 방법을 고른 기준이 무엇이었나요?",
                committed=False,
            )
        )

    async def resume(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        """mock 은 실패 지점을 모르므로 처음부터 다시 낸다."""
        async for event in self.run(state):
            yield event


class PartialGraphRunner:
    """일부 노드만 직접 실행하는 호환·실험용 실행기.

    운영 경로는 `CheckpointGraphRunner`이며, 이 클래스는 특정 초기 노드만
    독립 확인하거나 부분 실행 호환성이 필요할 때 명시적으로 주입한다.
    """

    REAL_NODES = ("router", "file_processor", "content_filter")

    def __init__(self, fallthrough: GraphRunner | None = None) -> None:
        self._fallthrough = fallthrough or MockGraphRunner()

    async def run(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        from features.experience_map.nodes.content_filter import filter_content
        from features.experience_map.nodes.content_filter import next_node as filter_next
        from features.experience_map.nodes.file_processor import next_node as file_next
        from features.experience_map.nodes.file_processor import process_files
        from features.experience_map.nodes.router import next_node as router_next
        from features.experience_map.nodes.router import route

        yield NodeStatusEvent(node="router", status="running")
        current = await route(state)
        yield NodeStatusEvent(node="router", status="completed")

        if router_next(current) == "fallback":
            async for event in self._emit_fallback(current):
                yield event
            return

        if router_next(current) == "file_processor":
            yield NodeStatusEvent(node="file_processor", status="running")
            current = await process_files(current)
            yield NodeStatusEvent(node="file_processor", status="completed")

            if file_next(current) == "fallback":
                async for event in self._emit_fallback(current):
                    yield event
                return

        yield NodeStatusEvent(node="content_filter", status="running")
        current = await filter_content(current)
        yield NodeStatusEvent(node="content_filter", status="completed")

        if filter_next(current) == "fallback":
            async for event in self._emit_fallback(current):
                yield event
            return

        async for event in self._fallthrough.run(current):
            # 이미 낸 노드의 이벤트는 걸러낸다.
            if isinstance(event, NodeStatusEvent) and event.node in self.REAL_NODES:
                continue
            yield event

    async def _emit_fallback(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        from features.experience_map.nodes.fallback import fallback, fallback_message

        done = await fallback(state)
        yield MessageCompleteEvent(
            message=CompletedMessage(
                request_id=state["request_id"],
                session_id=state["session_id"],
                response_kind="fallback",
                ai_response=fallback_message(done.get("fallback_reason")),
                committed=False,
            )
        )

    async def resume(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        async for event in self.run(state):
            yield event


_runner: GraphRunner | None = None


def get_graph_runner() -> GraphRunner:
    """그래프 실행기 반환. 주입된 것이 없으면 실제 checkpoint graph를 쓴다."""
    global _runner
    if _runner is None:
        from common.checkpointer import get_checkpointer
        from features.experience_map.graph import build_graph

        _runner = CheckpointGraphRunner(
            build_graph(checkpointer=get_checkpointer()), state_events=_state_events
        )
    return _runner


def set_graph_runner(runner: GraphRunner | None) -> None:
    """그래프 실행기 주입 (3.17·테스트)"""
    global _runner
    _runner = runner


async def _state_events(state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
    """graph 최종 state를 fallback 또는 coordinator SSE 이벤트로 바꾼다."""
    if state.get("fallback_reason"):
        from features.experience_map.nodes.fallback import fallback_message

        yield MessageCompleteEvent(
            message=CompletedMessage(
                request_id=str(state["request_id"]),
                session_id=str(state["session_id"]),
                response_kind="fallback",
                ai_response=fallback_message(state.get("fallback_reason")),
                committed=False,
            )
        )
        return
    if state.get("commit_items"):
        from features.experience_map.coordinator import coordinate
        from features.experience_map.repository import get_repository

        async def save_active_gap(user_id: str, gap: dict | None) -> None:
            await get_repository().save_active_gap(user_id, gap)

        async for event in coordinate(state, save_active_gap=save_active_gap):
            yield event
