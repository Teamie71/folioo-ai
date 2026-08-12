"""경험정리 그래프 실행기

API 계층과 LangGraph 사이의 경계다. 서비스는 이 Protocol 만 알고, 실제 그래프는
3.17(validate·graph 배선)에서 붙인다.

지금은 `MockGraphRunner` 가 기본이다. 노드가 하나도 없어도 API 계약과 SSE 이벤트
순서를 로컬에서 확인할 수 있어야 하기 때문이다 (태스크 3.10 DoD).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Protocol

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


class MockGraphRunner:
    """노드 없이 이벤트 순서만 재현하는 실행기.

    **3.17 에서 실제 그래프로 교체한다.** 그때까지 이 실행기가 API 계약 테스트와
    로컬 수동 확인을 담당한다.

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


_runner: GraphRunner | None = None


def get_graph_runner() -> GraphRunner:
    """그래프 실행기 반환. 주입된 것이 없으면 mock 을 쓴다."""
    global _runner
    if _runner is None:
        logger.warning("경험정리 그래프가 아직 없어 MockGraphRunner 를 사용합니다 (3.17 에서 교체)")
        _runner = MockGraphRunner()
    return _runner


def set_graph_runner(runner: GraphRunner | None) -> None:
    """그래프 실행기 주입 (3.17·테스트)"""
    global _runner
    _runner = runner
