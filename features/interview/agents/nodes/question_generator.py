"""QuestionGenerator 노드 - 인터뷰 질문 생성"""

from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    인터뷰 질문 생성 (초기 또는 분석 기반)
    - 첫 턴: 첫 질문 생성
    - 이후 턴: Analyst 분석 결과 기반 질문 생성

    TODO: 실제 질문 생성 로직은 후속 이슈에서 구현
    - LLM 기반 질문 생성
    - 스테이지별 초기 질문 템플릿
    - 동적 질문 최적화
    """

    is_first_turn = state.get("is_first_turn", False)
    if is_first_turn:
        # 첫 턴: 첫 질문 생성 (임시 값)
        generated_question = "안녕하세요! 인터뷰를 시작하겠습니다. 먼저 자기소개를 부탁드립니다."
    else:
        # 분석 결과 기반 질문 생성 (임시 값)
        generated_question = None

    # 질문 생성 후 종료 (임시 값)
    return {
        **state,
        "generated_question": generated_question,
        "next_node": "end",
    }
