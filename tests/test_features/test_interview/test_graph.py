"""그래프 구조 테스트"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from features.interview.agents import InterviewState, build_graph
from features.interview.agents.state import (
    ensure_interview_state_defaults,
    get_initial_interview_state,
)


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


def test_initial_state_starts_with_empty_file_turn_history():
    """초기 state는 빈 파일 턴 히스토리로 시작한다."""
    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )

    assert state["file_turn_history"] == []


def test_ensure_interview_state_defaults_adds_file_turn_history():
    """구세션 state에도 file_turn_history 기본값을 보강한다."""
    normalized = ensure_interview_state_defaults(
        {
            "messages": [],
            "current_turn_files": [],
            "file_contexts": [],
        }
    )

    assert normalized["file_turn_history"] == []


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
    assert result["turn_number"] == 0


def test_interviewer_routing(initial_state):
    """Router가 후속 턴에 retriever로 라우팅하는지 확인"""
    from features.interview.agents.nodes import router

    state = {
        **initial_state,
        "messages": [AIMessage(content="이전 질문"), HumanMessage(content="첫 답변")],
    }
    result = router.run(state)
    assert result["next_node"] == "retriever"
    assert result["turn_number"] == 1


def test_interviewer_routing_with_file_attachment(initial_state):
    """파일이 있으면 Router가 file_processor로 라우팅하는지 확인"""
    from features.interview.agents.nodes import router

    state = {
        **initial_state,
        "messages": [AIMessage(content="이전 질문"), HumanMessage(content="파일 포함 답변")],
        "current_turn_files": [
            {
                "filename": "portfolio.pdf",
                "content_type": "application/pdf",
                "temp_path": "/tmp/portfolio.pdf",
            }
        ],
    }
    result = router.run(state)
    assert result["next_node"] == "file_processor"
    assert result["turn_number"] == 1


def test_router_records_file_turn_history_for_new_user_turn(initial_state):
    """새 사용자 턴의 첨부 파일 메타데이터를 file_turn_history에 기록한다."""
    from features.interview.agents.nodes import router

    state = {
        **initial_state,
        "messages": [AIMessage(content="이전 질문"), HumanMessage(content="파일 포함 답변")],
        "current_turn_files": [
            {
                "filename": "portfolio.pdf",
                "content_type": "application/pdf",
                "temp_path": "/tmp/portfolio.pdf",
                "file_size": 123,
            }
        ],
    }

    result = router.run(state)

    assert result["turn_number"] == 1
    assert result["file_turn_history"] == [
        {
            "turn_number": 1,
            "files": [
                {
                    "filename": "portfolio.pdf",
                    "content_type": "application/pdf",
                    "file_size": 123,
                }
            ],
        }
    ]


def test_router_does_not_record_file_history_without_new_user_turn(initial_state):
    """새 사용자 메시지가 없으면 파일 히스토리를 기록하지 않는다."""
    from features.interview.agents.nodes import router

    state = {
        **initial_state,
        "turn_number": 1,
        "file_turn_history": [],
        "messages": [AIMessage(content="이전 질문"), HumanMessage(content="이전 답변")],
        "current_turn_files": [
            {
                "filename": "legacy.pdf",
                "content_type": "application/pdf",
                "temp_path": "/tmp/legacy.pdf",
            }
        ],
    }

    result = router.run(state)

    assert result["file_turn_history"] == []


def test_router_does_not_record_file_history_during_bootstrap(initial_state):
    """bootstrap 실행에서는 파일 히스토리를 기록하지 않는다."""
    from features.interview.agents.nodes import router

    result = router.run(
        {
            **initial_state,
            "current_turn_files": [
                {
                    "filename": "bootstrap.pdf",
                    "content_type": "application/pdf",
                    "temp_path": "/tmp/bootstrap.pdf",
                    "file_size": 12,
                }
            ],
        }
    )

    assert result["turn_number"] == 0
    assert result["file_turn_history"] == []


def test_router_uses_zero_for_legacy_file_payload_without_file_size(initial_state):
    """레거시 payload에 file_size가 없으면 0으로 보정한다."""
    from features.interview.agents.nodes import router

    state = {
        **initial_state,
        "messages": [AIMessage(content="이전 질문"), HumanMessage(content="레거시 파일 답변")],
        "current_turn_files": [
            {
                "filename": "legacy.pdf",
                "content_type": "application/pdf",
                "temp_path": "/tmp/legacy.pdf",
            }
        ],
    }

    result = router.run(state)

    assert result["file_turn_history"] == [
        {
            "turn_number": 1,
            "files": [
                {
                    "filename": "legacy.pdf",
                    "content_type": "application/pdf",
                    "file_size": 0,
                }
            ],
        }
    ]


def test_upsert_file_turn_history_replaces_same_turn_record():
    """같은 turn_number 기록은 append 대신 교체한다."""
    from features.interview.agents.nodes import router

    history = [
        {
            "turn_number": 1,
            "files": [
                {
                    "filename": "old.pdf",
                    "content_type": "application/pdf",
                    "file_size": 10,
                }
            ],
        },
        {
            "turn_number": 2,
            "files": [
                {
                    "filename": "old2.pdf",
                    "content_type": "application/pdf",
                    "file_size": 20,
                }
            ],
        },
    ]
    new_record = {
        "turn_number": 2,
        "files": [
            {
                "filename": "new.pdf",
                "content_type": "application/pdf",
                "file_size": 30,
            }
        ],
    }

    assert router._upsert_file_turn_history(history, new_record) == [history[0], new_record]


def test_router_increments_existing_turn_number(initial_state):
    """Router는 사용자 메시지 처리 시 기존 turn_number를 증가시킨다."""
    from features.interview.agents.nodes import router

    state = {
        **initial_state,
        "turn_number": 2,
        "messages": [
            AIMessage(content="이전 질문"),
            HumanMessage(content="이전 답변"),
            AIMessage(content="둘째 질문"),
            HumanMessage(content="둘째 답변"),
            AIMessage(content="새 질문"),
            HumanMessage(content="새 답변"),
        ],
    }

    result = router.run(state)

    assert result["turn_number"] == 3


@pytest.mark.asyncio
async def test_graph_with_end_condition(initial_state, monkeypatch):
    """Analyst가 end를 반환하면 QG를 거치지 않고 종료한다."""
    from features.interview.agents.nodes import analyst, question_generator

    def _analyst_run(state):
        return {
            **state,
            "all_stages_complete": True,
            "overall_completion_percentage": 100.0,
            "next_node": "end",
        }

    def _question_generator_run(_state):
        raise AssertionError("analyst가 end를 반환한 경우 question_generator가 호출되면 안 됩니다.")

    monkeypatch.setattr(analyst, "run", _analyst_run)
    monkeypatch.setattr(question_generator, "run", _question_generator_run)

    state = {
        **initial_state,
        "turn_number": 1,
        "messages": [AIMessage(content="이전 질문"), HumanMessage(content="답변")],
    }  # Router가 analyst로 분기
    graph = build_graph()
    result = await graph.ainvoke(state)
    assert result is not None
    assert result["all_stages_complete"] is True
    assert result["overall_completion_percentage"] == 100.0
