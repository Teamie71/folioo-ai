"""그래프 구조 테스트"""

import pytest
from langchain_core.messages import AIMessage

from features.interview.agents import InterviewState, build_graph
from features.interview.agents.state import get_initial_interview_state


@pytest.fixture
def initial_state() -> InterviewState:
    """테스트용 InterviewState 초기화 fixture"""
    return get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )


def test_build_graph():
    """그래프가 정상적으로 빌드되는지 확인"""
    graph = build_graph()
    assert graph is not None


def test_graph_execution_mock(initial_state, monkeypatch):
    """Mock 상태로 그래프 실행 테스트 - 무한 루프 확인"""
    from features.interview.agents.nodes import question_generator

    monkeypatch.setattr(
        question_generator,
        "run",
        lambda state: {
            **state,
            "messages": [AIMessage(content="첫 질문")],
            "next_node": "end",
        },
    )

    graph = build_graph()
    result = graph.invoke(initial_state)
    assert result["next_node"] == "end"
    assert result["messages"][-1].content == "첫 질문"


def test_supervisor_routing(initial_state):
    """Router가 첫 턴에 question_generator로 라우팅하는지 확인"""
    from features.interview.agents.nodes import router

    result = router.run(initial_state)
    assert result["next_node"] == "question_generator"


def test_interviewer_routing(initial_state):
    """Router가 후속 턴에 retriever로 라우팅하는지 확인"""
    from features.interview.agents.nodes import router

    state = {**initial_state, "messages": [AIMessage(content="이전 질문")]}
    result = router.run(state)
    assert result["next_node"] == "retriever"


def test_graph_with_end_condition(initial_state, monkeypatch):
    """종료 조건이 있을 때 그래프가 정상 종료되는지 확인"""
    from features.interview.agents.nodes import question_generator

    monkeypatch.setattr(
        question_generator,
        "run",
        lambda state: {
            **state,
            "all_stages_complete": True,
            "overall_completion_percentage": 100.0,
            "next_node": "end",
        },
    )
    graph = build_graph()
    result = graph.invoke(initial_state)
    assert result is not None
    assert result["all_stages_complete"] is True
    assert result["overall_completion_percentage"] == 100.0
