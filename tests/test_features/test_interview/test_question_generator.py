"""QuestionGenerator 노드 테스트"""

import pytest
from langchain_core.messages import AIMessage

from features.interview.agents.nodes import question_generator
from features.interview.agents.state import get_initial_interview_state


@pytest.fixture
def first_turn_state():
    """첫 턴 테스트용 state fixture"""
    return get_initial_interview_state(
        user_id="test_user", session_id="test_session", experience_name="AI 에이전트 개발 프로젝트"
    )


def test_first_turn_question_generation(first_turn_state):
    """
    첫 턴 질문 생성 테스트
    - 메시지가 비어있을 때 첫 질문을 생성하는지 확인
    - AIMessage가 추가되는지 확인
    - stage_progress의 fixed_q_used가 1로 증가하는지 확인
    - next_node가 'end'인지 확인
    """
    # 실행
    result = question_generator.run(first_turn_state)

    # 검증 1: AIMessage가 추가되었는지 확인
    assert "messages" in result
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)

    # 검증 2: 질문 내용이 생성되었는지 확인
    question_content = result["messages"][0].content
    assert isinstance(question_content, str)
    assert len(question_content) > 0

    # 검증 3: 경험명이 질문에 포함되어 있는지 확인
    assert "AI 에이전트 개발 프로젝트" in question_content

    # 검증 4: stage_progress가 업데이트되었는지 확인
    assert "stage_progress" in result
    assert result["stage_progress"]["fixed_q_used"] == 1

    # 검증 5: next_node가 'end'인지 확인
    assert result["next_node"] == "end"


def test_first_turn_uses_fixed_question_content(first_turn_state):
    """
    첫 번째 고정 질문 내용이 사용되는지 확인
    - stage 1의 첫 번째 고정 질문이 플레이스홀더 치환되어 사용되는지 확인
    """
    # 실행
    result = question_generator.run(first_turn_state)

    # 검증: 고정 질문 내용이 질문에 반영되었는지 확인
    question_content = result["messages"][0].content

    # 플레이스홀더가 치환되었는지 확인 (원본에는 [경험명]이 있지만 결과에는 실제 경험명이 들어가야 함)
    assert "[경험명]" not in question_content
    assert "AI 에이전트 개발 프로젝트" in question_content


def test_non_first_turn_raises_not_implemented(first_turn_state):
    """
    첫 턴이 아닐 때 NotImplementedError가 발생하는지 확인
    - messages에 내용이 있으면 후속 질문 생성으로 분기되어야 함
    - 현재는 구현되지 않았으므로 NotImplementedError 발생 예상
    """
    from langchain_core.messages import HumanMessage

    # messages에 내용 추가 (첫 턴이 아닌 상황 시뮬레이션)
    non_first_turn_state = {
        **first_turn_state,
        "messages": [
            AIMessage(content="첫 질문입니다."),
            HumanMessage(content="사용자 답변입니다."),
        ],
    }

    # 실행 및 검증
    with pytest.raises(NotImplementedError, match="후속 질문 생성은 아직 구현되지 않았습니다"):
        question_generator.run(non_first_turn_state)
