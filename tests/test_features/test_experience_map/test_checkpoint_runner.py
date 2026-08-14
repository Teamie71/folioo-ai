"""checkpoint 기반 경험 맵 graph 재개 테스트."""

import pytest

from app.schemas.experience_map import NodeStatusEvent
from features.experience_map.graph_runner import CheckpointGraphRunner


class GraphStub:
    """ainvoke 입력을 기록하는 compiled graph 대역."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []

    async def ainvoke(self, value, config):
        self.calls.append((value, config))
        return {"request_id": "request"}


async def events(state):
    """최종 state를 실행기 이벤트로 바꾸는 대역."""
    yield NodeStatusEvent(node="refine", status="completed")


@pytest.mark.asyncio
async def test_resume_uses_checkpoint_without_replaying_input_state():
    """재시도는 None input으로 실패 superstep부터 이어서 실행한다."""
    graph = GraphStub()
    runner = CheckpointGraphRunner(graph, state_events=events)

    received = [event async for event in runner.resume({"session_id": "session-1"})]

    assert received[0].node == "refine"
    assert graph.calls == [
        (None, {"configurable": {"thread_id": "session-1", "checkpoint_ns": "experience_map"}})
    ]


@pytest.mark.asyncio
async def test_run_passes_new_state_to_graph():
    graph = GraphStub()
    runner = CheckpointGraphRunner(graph, state_events=events)
    state = {"session_id": "session-1", "request_id": "request"}

    await anext(runner.run(state))

    assert graph.calls[0][0] == state
