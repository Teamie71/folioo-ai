"""그래프 구조 테스트"""

import pytest
from features.interview.agents import build_graph, InterviewState


def test_build_graph():
    """그래프가 정상적으로 빌드되는지 확인"""
    graph = build_graph()
    assert graph is not None


def test_graph_execution_mock():
    """Mock 상태로 그래프 실행 테스트 - 무한 루프 확인"""
    from langgraph.errors import GraphRecursionError

    graph = build_graph()

    # Mock 초기 상태
    initial_state: InterviewState = {
        "user_id": "test_user",
        "session_id": "test_session",
        "messages": [],
        "current_stage": 1,
        "fixed_q_count": 0,
        "generated_q_count": 0,
        "collected_data": {},
        "mentioned_insights": [],
        "retrieved_insights": [],
        "uploaded_files": [],
        "file_context": [],
        "next_node": "supervisor",
        "stage_complete": False,
        "all_complete": False,
        "completion_percentage": 0.0,
    }

    # 그래프 실행 시 무한 루프로 인해 GraphRecursionError 발생 확인
    # 현재 노드 로직이 구현되지 않아 supervisor <-> interviewer 무한 반복
    with pytest.raises(GraphRecursionError) as exc_info:
        graph.invoke(initial_state, config={"recursion_limit": 3})

    # 에러 메시지에 recursion limit이 포함되어 있는지 확인
    assert "Recursion limit" in str(exc_info.value)


def test_supervisor_routing():
    """Supervisor가 기본적으로 interviewer로 라우팅하는지 확인"""
    from features.interview.agents.nodes import supervisor

    state: InterviewState = {
        "user_id": "test",
        "session_id": "test",
        "messages": [],
        "current_stage": 1,
        "fixed_q_count": 0,
        "generated_q_count": 0,
        "collected_data": {},
        "mentioned_insights": [],
        "retrieved_insights": [],
        "uploaded_files": [],
        "file_context": [],
        "next_node": "supervisor",
        "stage_complete": False,
        "all_complete": False,
        "completion_percentage": 0.0,
    }

    result = supervisor.run(state)
    assert result["next_node"] == "interviewer"


def test_interviewer_routing():
    """Interviewer가 작업 후 supervisor로 복귀하는지 확인"""
    from features.interview.agents.nodes import interviewer

    state: InterviewState = {
        "user_id": "test",
        "session_id": "test",
        "messages": [],
        "current_stage": 1,
        "fixed_q_count": 0,
        "generated_q_count": 0,
        "collected_data": {},
        "mentioned_insights": [],
        "retrieved_insights": [],
        "uploaded_files": [],
        "file_context": [],
        "next_node": "interviewer",
        "stage_complete": False,
        "all_complete": False,
        "completion_percentage": 0.0,
    }

    result = interviewer.run(state)
    assert result["next_node"] == "supervisor"


def test_graph_with_end_condition():
    """종료 조건이 있을 때 그래프가 정상 종료되는지 확인"""
    # 그래프가 컴파일되기 전에 노드 함수를 모킹해야 하므로,
    # 직접 노드 로직을 수정하는 방식으로 테스트
    from features.interview.agents import nodes

    # 원본 함수 백업
    original_supervisor = nodes.supervisor.run

    try:
        # supervisor가 즉시 종료하도록 수정
        def mock_supervisor(state: InterviewState) -> InterviewState:
            return {
                **state,
                "next_node": "end",
                "all_complete": True,
                "completion_percentage": 100.0,
            }

        nodes.supervisor.run = mock_supervisor

        # 그래프 재빌드 (수정된 노드 함수 사용)
        graph = build_graph()

        initial_state: InterviewState = {
            "user_id": "test_user",
            "session_id": "test_session",
            "messages": [],
            "current_stage": 1,
            "fixed_q_count": 0,
            "generated_q_count": 0,
            "collected_data": {},
            "mentioned_insights": [],
            "retrieved_insights": [],
            "uploaded_files": [],
            "file_context": [],
            "next_node": "supervisor",
            "stage_complete": False,
            "all_complete": False,
            "completion_percentage": 0.0,
        }

        # 그래프 실행 - 종료 조건이 있으므로 정상 종료되어야 함
        result = graph.invoke(initial_state)

        # 검증
        assert result is not None
        assert result["all_complete"] is True
        assert result["completion_percentage"] == 100.0

    finally:
        # 원본 함수 복원
        nodes.supervisor.run = original_supervisor
