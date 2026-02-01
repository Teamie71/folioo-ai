"""QuestionGenerator 노드 테스트"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from features.interview.agents.nodes import question_generator
from features.interview.agents.state import CollectedField, get_initial_interview_state
from features.interview.config.loader import load_stage_config


def _mock_llm_return(content: str):
    return RunnableLambda(lambda _: AIMessage(content=content))


def _mock_llm_raise():
    def _raise(_):
        raise RuntimeError("LLM 호출 실패")

    return RunnableLambda(_raise)


@pytest.fixture
def first_turn_state():
    """첫 턴 테스트용 state fixture"""
    return get_initial_interview_state(
        user_id="test_user", session_id="test_session", experience_name="AI 에이전트 개발 프로젝트"
    )


def test_first_turn_question_generation(first_turn_state, monkeypatch):
    """
    첫 턴 질문 생성 테스트
    - 메시지가 비어있을 때 첫 질문을 생성하는지 확인
    - AIMessage가 추가되는지 확인
    - stage_progress의 fixed_q_used가 1로 증가하는지 확인
    - next_node가 'end'인지 확인
    """
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda _temperature=0.7: _mock_llm_return("첫 질문입니다."),
    )

    # 실행
    result = question_generator.run(first_turn_state)

    # 검증 1: AIMessage가 추가되었는지 확인
    assert "messages" in result
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)

    # 검증 2: 질문 내용이 생성되었는지 확인
    question_content = result["messages"][0].content
    assert question_content == "첫 질문입니다."

    # 검증 3: stage_progress가 업데이트되었는지 확인
    assert "stage_progress" in result
    assert result["stage_progress"]["fixed_q_used"] == 1

    # 검증 4: next_node가 'end'인지 확인
    assert result["next_node"] == "end"


def test_first_turn_uses_fixed_question_content(first_turn_state, monkeypatch):
    """
    첫 번째 고정 질문 내용이 사용되는지 확인
    - stage 1의 첫 번째 고정 질문이 플레이스홀더 치환되어 사용되는지 확인
    """
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda _temperature=0.7: _mock_llm_raise(),
    )

    # 실행
    result = question_generator.run(first_turn_state)

    # 검증: 고정 질문 내용이 질문에 반영되었는지 확인
    question_content = result["messages"][0].content

    # 플레이스홀더가 치환되었는지 확인 (원본에는 [경험명]이 있지만 결과에는 실제 경험명이 들어가야 함)
    assert "[경험명]" not in question_content
    assert "AI 에이전트 개발 프로젝트" in question_content


def test_followup_fixed_question_generation(first_turn_state, monkeypatch):
    """
    후속 고정 질문 생성 테스트
    - 첫 턴 이후 고정 질문을 순차적으로 생성하는지 확인
    - 플레이스홀더가 치환되는지 확인
    """
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda _temperature=0.7: _mock_llm_raise(),
    )

    non_first_turn_state = {
        **first_turn_state,
        "messages": [
            AIMessage(content="첫 질문입니다."),
            HumanMessage(content="사용자 답변입니다."),
        ],
        "stage_progress": {
            **first_turn_state["stage_progress"],
            "fixed_q_used": 1,
        },
    }

    result = question_generator.run(non_first_turn_state)

    expected_fixed_question = load_stage_config(1).fixed_questions[1]

    assert result["stage_progress"]["fixed_q_used"] == 2
    assert result["next_node"] == "end"
    assert result["messages"][0].content == expected_fixed_question


def test_generated_question_skipped_when_fields_complete(first_turn_state):
    """
    생성 질문 건너뛰기 테스트
    - 모든 required_fields가 충분히 수집되면 생성 질문을 생략하고 analyst로 전환
    """
    collected_field: CollectedField = {
        "field_name": "project_background",
        "description": "이 활동을 시작하게 된 이유",
        "value": "배경 설명",
        "completeness": 0.9,
    }
    complete_collected = {
        "project_background": collected_field,
        "problem_definition": {**collected_field, "field_name": "problem_definition"},
        "message_or_concept": {**collected_field, "field_name": "message_or_concept"},
        "project_duration": {**collected_field, "field_name": "project_duration"},
        "team_composition": {**collected_field, "field_name": "team_composition"},
        "target_audience": {**collected_field, "field_name": "target_audience"},
    }

    state = {
        **first_turn_state,
        "messages": [
            AIMessage(content="질문1"),
            HumanMessage(content="답변1"),
        ],
        "stage_progress": {
            **first_turn_state["stage_progress"],
            "fixed_q_used": first_turn_state["stage_progress"]["fixed_q_total"],
            "generated_q_used": 0,
        },
        "collected_data": {**first_turn_state["collected_data"], "stage_1": complete_collected},
    }

    result = question_generator.run(state)

    assert result["next_node"] == "analyst"
    assert result["stage_progress"]["is_complete"] is True
    assert result["messages"] == state["messages"]


def test_generated_question_fallback_on_llm_error(first_turn_state, monkeypatch):
    """
    생성 질문 LLM 실패 시 fallback 질문 생성 테스트
    - 미수집 필드의 설명을 기반으로 질문이 생성되는지 확인
    """
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda _temperature=0.7: _mock_llm_raise(),
    )

    state = {
        **first_turn_state,
        "messages": [
            AIMessage(content="질문1"),
            HumanMessage(content="답변1"),
        ],
        "stage_progress": {
            **first_turn_state["stage_progress"],
            "fixed_q_used": first_turn_state["stage_progress"]["fixed_q_total"],
            "generated_q_used": 0,
        },
    }

    result = question_generator.run(state)

    assert result["stage_progress"]["generated_q_used"] == 1
    assert "이 활동을 시작하게 된 이유" in result["messages"][0].content
