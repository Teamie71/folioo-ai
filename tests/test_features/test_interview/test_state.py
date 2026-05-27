"""InterviewState 기본값 테스트"""

from features.interview.agents.state import (
    ensure_interview_state_defaults,
    get_initial_interview_state,
)


def test_initial_state_has_additional_question_defaults():
    """새 세션 state는 추가 질문 내부 상태를 빈 값으로 초기화한다."""
    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )

    assert state["additional_question_target_statuses"] == {}
    assert state["additional_question_pre_evaluated"] is False
    assert state["current_additional_question_target_id"] is None
    assert state["pending_extended_end_guide"] is False


def test_ensure_interview_state_defaults_adds_additional_question_fields():
    """구버전 checkpoint state에도 추가 질문 내부 상태 기본값을 보강한다."""
    state = ensure_interview_state_defaults(
        {
            "messages": [],
            "retrieved_insights": None,
            "current_turn_files": None,
            "file_contexts": None,
        }
    )

    assert state["additional_question_target_statuses"] == {}
    assert state["additional_question_pre_evaluated"] is False
    assert state["current_additional_question_target_id"] is None
    assert state["pending_extended_end_guide"] is False
