"""checkpoint 기반 경험 맵 graph 재개 테스트."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.schemas.experience_map import MessageCompleteEvent, NodeStatusEvent
from features.experience_map import graph as graph_module
from features.experience_map.graph_runner import CheckpointGraphRunner, _state_events
from features.experience_map.nodes.fallback import FALLBACK_MESSAGES


class GraphStub:
    """astream 입력을 기록하고 최종 state 하나만 내는 compiled graph 대역."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []

    async def astream(self, value, config, **kwargs):
        self.calls.append((value, config, kwargs))
        yield "values", {"request_id": "request"}


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
    assert graph.calls[0][0:2] == (
        None,
        {"configurable": {"thread_id": "session-1", "checkpoint_ns": "experience_map"}},
    )
    assert graph.calls[0][2] == {
        "stream_mode": ["tasks", "values"],
        "durability": "sync",
    }


@pytest.mark.asyncio
async def test_run_passes_new_state_to_graph():
    graph = GraphStub()
    runner = CheckpointGraphRunner(graph, state_events=events)
    state = {"session_id": "session-1", "request_id": "request"}

    await anext(runner.run(state))

    assert graph.calls[0][0] == state


@pytest.mark.asyncio
async def test_out_of_scope_input_runs_graph_and_emits_fallback_sse(monkeypatch):
    """기능 밖 입력은 실제 graph 배선을 거쳐 router node_status + fallback SSE로 끝난다."""

    async def out_of_scope(state):
        return {**state, "intent": "out_of_scope", "fallback_reason": "out_of_scope"}

    monkeypatch.setattr(graph_module, "route", out_of_scope)
    runner = CheckpointGraphRunner(
        graph_module.build_graph(checkpointer=InMemorySaver()), state_events=_state_events
    )

    events = [
        event
        async for event in runner.run(
            {"session_id": "session-1", "request_id": "request-1", "user_id": "1"}
        )
    ]

    node_events = [event for event in events if isinstance(event, NodeStatusEvent)]
    message_events = [event for event in events if isinstance(event, MessageCompleteEvent)]
    assert {event.node for event in node_events} == {"router", "fallback"}
    assert len(message_events) == 1
    assert message_events[0].message.response_kind == "fallback"
    assert message_events[0].message.committed is False
    assert message_events[0].message.ai_response == FALLBACK_MESSAGES["out_of_scope"]


@pytest.mark.asyncio
async def test_unreadable_file_runs_graph_and_emits_file_fallback_sse(monkeypatch):
    """파일 처리 실패는 target/structure 단계를 건너뛰고 파일 전용 fallback을 낸다."""

    async def file_input(state):
        return {**state, "intent": "file_input"}

    async def unreadable_file(state):
        return {**state, "fallback_reason": "file_unreadable"}

    monkeypatch.setattr(graph_module, "route", file_input)
    monkeypatch.setattr(graph_module, "process_files", unreadable_file)
    runner = CheckpointGraphRunner(
        graph_module.build_graph(checkpointer=InMemorySaver()), state_events=_state_events
    )

    events = [
        event
        async for event in runner.run(
            {
                "session_id": "session-1",
                "request_id": "request-1",
                "user_id": "1",
                "file_references": [{"file_id": "file-1"}],
            }
        )
    ]

    node_events = [event for event in events if isinstance(event, NodeStatusEvent)]
    message_events = [event for event in events if isinstance(event, MessageCompleteEvent)]
    assert {event.node for event in node_events} == {
        "router",
        "file_processor",
        "file_cleanup",
        "fallback",
    }
    assert len(message_events) == 1
    assert message_events[0].message.response_kind == "fallback"
    assert message_events[0].message.ai_response.startswith("파일에서 내용을 읽지 못했어요.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gap_type", "includes_new_item", "expected_path"),
    [
        ("extend_block", False, ["target_activity", "refine"]),
        ("new_child_block", False, ["target_activity", "structure", "refine"]),
        ("extend_block", True, ["target_activity", "structure", "refine"]),
    ],
)
async def test_gap_answer_uses_expected_graph_path_before_commit(
    monkeypatch, gap_type, includes_new_item, expected_path
):
    """gap 유형에 따라 결합 정제 또는 하위 블록 생성 경로를 실제 graph로 고른다."""
    calls: list[str] = []
    completed_states = []

    async def chat_input(state):
        return {**state, "intent": "chat_input"}

    async def gap_answer(state):
        return {
            **state,
            "gap_answer_items": [
                {"item_id": "gap_1", "text": "재시도 로직을 추가했다.", "source": "message"}
            ],
            "new_items": (
                [{"item_id": "new_1", "text": "모니터링 대시보드도 만들었다.", "source": "message"}]
                if includes_new_item
                else []
            ),
        }

    async def select_target(state):
        calls.append("target_activity")
        return {
            **state,
            "target_experience_alias": "exp_1",
            "activity_tree_text": "[exp_1] 결제 개선\n  [b_1] 문제 해결",
        }

    async def structure(state):
        calls.append("structure")
        return {
            **state,
            "structured_items": [
                {
                    "item_id": "new_1" if state["new_items"] else "gap_child",
                    "action": "add",
                    "parent_ref": "b_1",
                    "text": (
                        "모니터링 대시보드도 만들었다."
                        if state["new_items"]
                        else "재시도 로직을 추가했다."
                    ),
                }
            ],
        }

    async def refine(state):
        calls.append("refine")
        if state["active_gap"]["gap_type"] == "extend_block":
            return {
                **state,
                "gap_update_item": {
                    "item_id": "gap_update:305",
                    "action": "update",
                    "target_ref": "b_1",
                    "text": "기존 내용\n재시도 로직을 추가했다.",
                },
                "refined_items": (
                    [
                        {"item_id": "new_1", "refined_text": "모니터링 대시보드 구축"},
                        {
                            "item_id": "gap_update:305",
                            "refined_text": "기존 내용과 재시도 로직",
                        },
                    ]
                    if state["new_items"]
                    else [
                        {
                            "item_id": "gap_update:305",
                            "refined_text": "기존 내용과 재시도 로직",
                        }
                    ]
                ),
            }
        return {
            **state,
            "refined_items": [{"item_id": "gap_child", "refined_text": "재시도 로직 추가"}],
        }

    async def capture_state(state):
        completed_states.append(state)
        if False:
            yield NodeStatusEvent(node="validate", status="completed")

    monkeypatch.setattr(graph_module, "route", chat_input)
    monkeypatch.setattr(graph_module, "filter_content", gap_answer)
    monkeypatch.setattr(graph_module, "select_target_activity", select_target)
    monkeypatch.setattr(graph_module, "structure_blocks", structure)
    monkeypatch.setattr(graph_module, "refine_text", refine)
    runner = CheckpointGraphRunner(
        graph_module.build_graph(checkpointer=InMemorySaver()), state_events=capture_state
    )

    events = [
        event
        async for event in runner.run(
            {
                "session_id": "session-1",
                "request_id": "request-1",
                "user_id": "1",
                "active_gap": {"gap_type": gap_type, "anchor_block_id": "305"},
                "alias_to_block_id": {"exp_1": "101", "b_1": "305"},
                "alias_metadata": {
                    "exp_1": {
                        "block_id": "101",
                        "parent_alias": None,
                        "level": 2,
                        "kind": "ACTIVITY",
                        "is_text_editable": False,
                    },
                    "b_1": {
                        "block_id": "305",
                        "parent_alias": "exp_1",
                        "level": 4,
                        "kind": "ITEM",
                        "is_text_editable": True,
                    },
                },
            }
        )
    ]

    # capture_state는 아무것도 안 내므로, 남는 건 실제 graph가 낸 node_status뿐이다.
    assert all(isinstance(event, NodeStatusEvent) for event in events)
    assert calls == expected_path
    assert len(completed_states) == 1
    assert completed_states[0]["commit_items"]
