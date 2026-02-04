"""InterviewService 유닛 테스트"""

import importlib
import sys
import types

import pytest
from langchain_core.messages import AIMessage, HumanMessage


def _install_dummy_langchain_openai():
    """테스트용 langchain_openai 더미 모듈 설치"""
    dummy_module = types.ModuleType("langchain_openai")

    class DummyChatOpenAI:  # pragma: no cover - 간단 더미
        def __init__(self, *args, **kwargs):
            pass

    dummy_module.ChatOpenAI = DummyChatOpenAI
    sys.modules.setdefault("langchain_openai", dummy_module)


_install_dummy_langchain_openai()

interview_service = importlib.import_module("features.interview.service")


class DummyStateSnapshot:
    """그래프 상태 스냅샷 더미"""

    def __init__(self, values):
        self.values = values


class DummyGraph:
    """그래프 더미 객체"""

    def __init__(self):
        self.invocations = []
        self.invoke_result = None
        self.state_snapshot = None
        self.last_get_state_config = None

    def invoke(self, state, config=None):
        self.invocations.append({"state": state, "config": config})
        return self.invoke_result

    def get_state(self, config=None):
        self.last_get_state_config = config
        return self.state_snapshot


def _build_service(monkeypatch, dummy_graph):
    monkeypatch.setattr(interview_service, "build_graph", lambda checkpointer=None: dummy_graph)
    monkeypatch.setattr(interview_service, "get_checkpointer", lambda: object())
    return interview_service.InterviewService()


def test_create_session_validation_error(monkeypatch):
    """필수 파라미터 누락 시 예외 발생 테스트"""
    dummy_graph = DummyGraph()
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(ValueError, match="필수"):
        service.create_session(user_id="", session_id="sid", experience_name="exp")


def test_create_session_returns_expected_payload(monkeypatch):
    """세션 생성 결과 포맷 테스트"""
    dummy_graph = DummyGraph()
    dummy_graph.invoke_result = {
        "messages": [AIMessage(content="첫 질문")],
        "current_stage": 1,
        "stage_progress": {"fixed_q_used": 1},
    }

    service = _build_service(monkeypatch, dummy_graph)

    result = service.create_session(
        user_id="user_1",
        session_id="session_1",
        experience_name="프로젝트 A",
    )

    assert result["session_id"] == "session_1"
    assert result["first_question"] == "첫 질문"
    assert result["current_stage"] == 1
    assert result["stage_progress"]["fixed_q_used"] == 1

    assert len(dummy_graph.invocations) == 1
    invocation = dummy_graph.invocations[0]
    assert invocation["config"] == {"configurable": {"thread_id": "session_1"}}
    assert invocation["state"]["user_id"] == "user_1"
    assert invocation["state"]["session_id"] == "session_1"
    assert invocation["state"]["experience_name"] == "프로젝트 A"


def test_process_message_session_not_found(monkeypatch):
    """세션이 없을 때 예외 발생 테스트"""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = None
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(ValueError, match="세션을 찾을 수 없습니다"):
        service.process_message(session_id="missing", message="안녕하세요")


def test_process_message_with_files(monkeypatch):
    """파일 ID 포함 메시지 처리 테스트"""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(values={"session_id": "session_1"})
    dummy_graph.invoke_result = {
        "messages": [AIMessage(content="응답")],
        "current_stage": 2,
        "stage_progress": {"fixed_q_used": 2},
        "overall_completion_percentage": 40.0,
        "all_stages_complete": False,
    }

    service = _build_service(monkeypatch, dummy_graph)

    result = service.process_message(
        session_id="session_1",
        message="답변입니다.",
        file_ids=["file_1", "file_2"],
    )

    assert result["ai_response"] == "응답"
    assert result["current_stage"] == 2
    assert result["stage_progress"]["fixed_q_used"] == 2
    assert result["overall_completion"] == 40.0
    assert result["all_complete"] is False

    invocation = dummy_graph.invocations[0]
    assert invocation["config"] == {"configurable": {"thread_id": "session_1"}}
    assert isinstance(invocation["state"]["messages"][0], HumanMessage)
    assert invocation["state"]["messages"][0].content == "답변입니다."
    assert invocation["state"]["current_turn_files"] == ["file_1", "file_2"]


def test_get_session_state_returns_none_on_empty_snapshot(monkeypatch):
    """스냅샷이 없거나 비어있을 때 None 반환 테스트"""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(values={})
    service = _build_service(monkeypatch, dummy_graph)

    assert service.get_session_state("session_1") is None


def test_get_session_state_returns_values(monkeypatch):
    """스냅샷 값 반환 테스트"""
    dummy_graph = DummyGraph()
    expected_state = {"session_id": "session_1", "current_stage": 1}
    dummy_graph.state_snapshot = DummyStateSnapshot(values=expected_state)
    service = _build_service(monkeypatch, dummy_graph)

    assert service.get_session_state("session_1") == expected_state


def test_singleton_get_and_reset(monkeypatch):
    """싱글톤 생성 및 초기화 테스트"""
    dummy_graph = DummyGraph()
    monkeypatch.setattr(interview_service, "build_graph", lambda checkpointer=None: dummy_graph)
    monkeypatch.setattr(interview_service, "get_checkpointer", lambda: object())

    interview_service.reset_interview_service()
    first = interview_service.get_interview_service()
    second = interview_service.get_interview_service()

    assert first is second

    interview_service.reset_interview_service()
    third = interview_service.get_interview_service()
    assert first is not third
