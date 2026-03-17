"""첫 질문 스트리밍 관련 테스트"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.api.v1.interview import router
from common.sse import LangGraphEventType, SSEEventType
from features.interview.service import InterviewService


class DummyStateSnapshot:
    """그래프 상태 스냅샷 더미"""

    def __init__(self, values):
        self.values = values


class DummyChunk:
    """스트리밍 청크 더미"""

    def __init__(self, content: str):
        self.content = content


class DummyGraph:
    """스트리밍 테스트용 그래프 더미"""

    def __init__(self):
        self.stream_events = []
        self.stream_error = None
        self.state_snapshot = None
        self.astream_calls = []
        self.aget_state_calls = []
        self.update_state_calls = []

    async def astream_events(self, state, config=None, version=None):
        self.astream_calls.append({"state": state, "config": config, "version": version})
        if self.stream_error is not None:
            raise self.stream_error
        for event in self.stream_events:
            yield event

    async def aget_state(self, config=None):
        self.aget_state_calls.append(config)
        return self.state_snapshot

    async def aupdate_state(self, config, state):
        self.update_state_calls.append({"config": config, "state": state})
        if self.state_snapshot is None:
            self.state_snapshot = DummyStateSnapshot(values=dict(state))
        else:
            self.state_snapshot.values = {**self.state_snapshot.values, **state}


@pytest.mark.anyio
async def test_create_session_stream_yields_delta_and_complete(monkeypatch):
    """첫 질문 생성 시 토큰과 완료 이벤트를 순서대로 전송하는지 테스트"""
    dummy_graph = DummyGraph()
    dummy_graph.stream_events = [
        {
            "event": LangGraphEventType.ON_CHAT_MODEL_STREAM,
            "metadata": {"langgraph_node": "question_generator"},
            "data": {"chunk": DummyChunk("첫 질문")},
        }
    ]
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "messages": [AIMessage(content="첫 질문")],
            "current_stage": 1,
            "stage_progress": {
                "fixed_q_used": 0,
                "fixed_q_total": 1,
                "generated_q_used": 0,
                "generated_q_max": 0,
                "force_all_generated_q": False,
                "is_complete": False,
            },
        }
    )

    monkeypatch.setattr(
        "features.interview.service.build_graph", lambda checkpointer=None: dummy_graph
    )
    monkeypatch.setattr("features.interview.service.get_checkpointer", lambda: object())
    service = InterviewService()

    events = [
        event
        async for event in service.create_session_stream(
            user_id="user-1",
            session_id="session-1",
            experience_name="프로젝트",
        )
    ]

    assert events[0]["event"] == SSEEventType.CONTENT_BLOCK_DELTA
    delta_payload = json.loads(events[0]["data"])
    assert delta_payload["delta"]["text"] == "첫 질문"

    assert events[1]["event"] == SSEEventType.MESSAGE_COMPLETE
    complete_payload = json.loads(events[1]["data"])
    assert complete_payload["message"]["session_id"] == "session-1"
    assert complete_payload["message"]["first_question"] == "첫 질문"
    assert complete_payload["message"]["status"] == "completed"
    assert complete_payload["message"]["current_stage"] == 1
    assert complete_payload["message"]["is_extended_mode"] is False
    assert dummy_graph.astream_calls[0]["state"]["status"] == "generating"
    assert dummy_graph.update_state_calls[-1]["state"]["status"] == "completed"


def test_create_session_stream_route_exists():
    """첫 질문 스트리밍 라우트가 등록되어 있는지 테스트"""
    assert any(
        route.path == "/interview/sessions/stream" and "POST" in route.methods
        for route in router.routes
    )


def test_extend_session_stream_route_exists():
    """연장 모드 SSE 라우트가 등록되어 있는지 테스트"""
    assert any(
        route.path == "/interview/sessions/{session_id}/extend/stream" and "POST" in route.methods
        for route in router.routes
    )


@pytest.mark.anyio
async def test_process_message_stream_emits_retriever_events(monkeypatch):
    """Retriever 시작/결과 이벤트를 SSE로 전송한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "messages": [AIMessage(content="최종 응답")],
            "current_stage": 1,
            "stage_progress": {
                "fixed_q_used": 0,
                "fixed_q_total": 1,
                "generated_q_used": 0,
                "generated_q_max": 0,
                "force_all_generated_q": False,
                "is_complete": False,
            },
            "overall_completion_percentage": 25.0,
            "all_stages_complete": False,
        }
    )
    dummy_graph.stream_events = [
        {
            "event": LangGraphEventType.ON_CHAIN_START,
            "metadata": {"langgraph_node": "retriever"},
            "data": {},
        },
        {
            "event": LangGraphEventType.ON_CHAIN_END,
            "metadata": {"langgraph_node": "retriever"},
            "data": {
                "output": {
                    "retrieved_insights": [
                        {
                            "id": "insight-1",
                            "title": "문제 해결 경험",
                            "activity_name": "프로젝트 A",
                            "category": "문제해결",
                            "similarity_score": 0.91,
                            "source": "search",
                        },
                        {
                            "id": "insight-2",
                            "title": "멘션 인사이트",
                            "activity_name": "프로젝트 B",
                            "category": "기타",
                            "similarity_score": None,
                            "source": "mention",
                        },
                    ]
                }
            },
        },
        {
            "event": LangGraphEventType.ON_CHAT_MODEL_STREAM,
            "metadata": {"langgraph_node": "question_generator"},
            "data": {"chunk": DummyChunk("토큰")},
        },
    ]

    monkeypatch.setattr(
        "features.interview.service.build_graph", lambda checkpointer=None: dummy_graph
    )
    monkeypatch.setattr("features.interview.service.get_checkpointer", lambda: object())
    service = InterviewService()

    events = [
        event
        async for event in service.process_message_stream(
            session_id="session-1",
            message="사용자 답변",
        )
    ]

    assert len(events) > 0
    assert events[0]["event"] == SSEEventType.RETRIEVER_STATUS
    status_payload = json.loads(events[0]["data"])
    assert status_payload["type"] == SSEEventType.RETRIEVER_STATUS

    assert events[1]["event"] == SSEEventType.RETRIEVER_RESULT
    retriever_payload = json.loads(events[1]["data"])
    assert retriever_payload["insights"][0]["activity_name"] == "프로젝트 A"
    assert retriever_payload["insights"][0]["source"] == "search"
    assert retriever_payload["insights"][1]["source"] == "mention"

    assert events[2]["event"] == SSEEventType.CONTENT_BLOCK_DELTA
    complete_event = next(
        event for event in events if event["event"] == SSEEventType.MESSAGE_COMPLETE
    )
    complete_payload = json.loads(complete_event["data"])
    assert complete_payload["message"]["status"] == "completed"
    assert dummy_graph.update_state_calls[0]["state"]["status"] == "generating"
    assert dummy_graph.update_state_calls[-1]["state"]["status"] == "completed"


@pytest.mark.anyio
async def test_process_message_stream_resets_current_turn_files_when_no_files(monkeypatch):
    """스트리밍 턴에서도 current_turn_files를 빈 리스트로 초기화한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "messages": [AIMessage(content="최종 응답")],
            "current_stage": 1,
            "stage_progress": {
                "fixed_q_used": 0,
                "fixed_q_total": 1,
                "generated_q_used": 0,
                "generated_q_max": 0,
                "force_all_generated_q": False,
                "is_complete": False,
            },
            "overall_completion_percentage": 25.0,
            "all_stages_complete": False,
            "current_turn_files": ["old-file"],
        }
    )
    dummy_graph.stream_events = []

    monkeypatch.setattr(
        "features.interview.service.build_graph", lambda checkpointer=None: dummy_graph
    )
    monkeypatch.setattr("features.interview.service.get_checkpointer", lambda: object())
    service = InterviewService()

    _ = [
        event
        async for event in service.process_message_stream(
            session_id="session-1",
            message="사용자 답변",
        )
    ]

    assert dummy_graph.astream_calls[0]["state"]["current_turn_files"] == []


@pytest.mark.anyio
async def test_process_message_stream_returns_none_when_all_complete(monkeypatch):
    """모든 단계 완료 상태면 message_complete의 ai_response는 null이다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "messages": [HumanMessage(content="사용자 최종 답변")],
            "current_stage": 4,
            "stage_progress": {
                "fixed_q_used": 3,
                "fixed_q_total": 3,
                "generated_q_used": 2,
                "generated_q_max": 2,
                "force_all_generated_q": False,
                "is_complete": True,
            },
            "overall_completion_percentage": 100.0,
            "all_stages_complete": True,
        }
    )
    dummy_graph.stream_events = []

    monkeypatch.setattr(
        "features.interview.service.build_graph", lambda checkpointer=None: dummy_graph
    )
    monkeypatch.setattr("features.interview.service.get_checkpointer", lambda: object())
    service = InterviewService()

    events = [
        event
        async for event in service.process_message_stream(
            session_id="session-1",
            message="사용자 답변",
        )
    ]

    complete_event = next(
        event for event in events if event["event"] == SSEEventType.MESSAGE_COMPLETE
    )
    complete_payload = json.loads(complete_event["data"])
    assert complete_payload["message"]["ai_response"] is None
    assert complete_payload["message"]["status"] == "completed"
    assert complete_payload["message"]["is_extended_mode"] is False


@pytest.mark.anyio
async def test_extend_session_stream_yields_delta_and_complete(monkeypatch):
    """연장 시작 스트림에서 토큰과 완료 이벤트를 순서대로 전송한다."""
    dummy_graph = DummyGraph()
    dummy_graph.stream_events = [
        {
            "event": LangGraphEventType.ON_CHAT_MODEL_STREAM,
            "metadata": {"langgraph_node": "question_generator"},
            "data": {"chunk": DummyChunk("연장 질문")},
        }
    ]

    monkeypatch.setattr(
        "features.interview.service.build_graph", lambda checkpointer=None: dummy_graph
    )
    monkeypatch.setattr("features.interview.service.get_checkpointer", lambda: object())
    monkeypatch.setattr(
        "features.interview.service.get_global_config",
        lambda: type(
            "Config",
            (),
            {
                "max_extensions": 2,
                "extension_turns_per_session": 3,
            },
        )(),
    )
    service = InterviewService()

    initial_state = {
        "session_id": "session-1",
        "all_stages_complete": True,
        "extension_count": 0,
    }
    final_state = {
        "messages": [AIMessage(content="연장 질문")],
        "current_stage": 4,
        "stage_progress": {
            "fixed_q_used": 3,
            "fixed_q_total": 3,
            "generated_q_used": 2,
            "generated_q_max": 2,
            "force_all_generated_q": False,
            "is_complete": True,
        },
        "overall_completion_percentage": 100.0,
        "all_stages_complete": False,
        "is_extended_mode": True,
        "extension_turns_used": 1,
        "extension_turns_max": 3,
        "extension_count": 1,
    }
    states = [initial_state, final_state]

    async def _get_session_state(_session_id: str):
        if states:
            return states.pop(0)
        return final_state

    monkeypatch.setattr(service, "get_session_state", _get_session_state)

    events = [event async for event in service.extend_session_stream(session_id="session-1")]

    assert dummy_graph.astream_calls[0]["state"]["mentioned_insight"] is None

    assert events[0]["event"] == SSEEventType.CONTENT_BLOCK_DELTA
    delta_payload = json.loads(events[0]["data"])
    assert delta_payload["delta"]["text"] == "연장 질문"

    complete_event = next(
        event for event in events if event["event"] == SSEEventType.MESSAGE_COMPLETE
    )
    complete_payload = json.loads(complete_event["data"])
    assert complete_payload["message"]["ai_response"] == "연장 질문"
    assert complete_payload["message"]["status"] == "completed"
    assert complete_payload["message"]["is_extended_mode"] is True
    assert dummy_graph.update_state_calls[0]["state"]["status"] == "generating"
    assert dummy_graph.update_state_calls[-1]["state"]["status"] == "completed"


@pytest.mark.anyio
async def test_process_message_stream_sets_failed_status_on_exception(monkeypatch):
    """스트리밍 예외 발생 시 세션 status를 failed로 기록한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session-1",
            "current_stage": 1,
            "stage_progress": {
                "fixed_q_used": 0,
                "fixed_q_total": 1,
                "generated_q_used": 0,
                "generated_q_max": 0,
                "force_all_generated_q": False,
                "is_complete": False,
            },
            "overall_completion_percentage": 25.0,
            "all_stages_complete": False,
        }
    )
    dummy_graph.stream_error = RuntimeError("boom")

    monkeypatch.setattr(
        "features.interview.service.build_graph", lambda checkpointer=None: dummy_graph
    )
    monkeypatch.setattr("features.interview.service.get_checkpointer", lambda: object())
    service = InterviewService()

    events = [
        event
        async for event in service.process_message_stream(
            session_id="session-1",
            message="사용자 답변",
        )
    ]

    assert events[0]["event"] == SSEEventType.ERROR
    error_payload = json.loads(events[0]["data"])
    assert error_payload["error"]["code"] == "llm_error"
    assert dummy_graph.update_state_calls[0]["state"]["status"] == "generating"
    assert dummy_graph.update_state_calls[-1]["state"]["status"] == "failed"
