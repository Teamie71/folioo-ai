"""Analyst 노드 - 정보 통합 분석"""

from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    수집된 정보 통합 분석
    - FileProcessor 결과 통합
    - Retriever 결과 통합
    - 대화 컨텍스트에서 정보 추출
    - 단계 완료 여부 판단

    TODO: 실제 분석 로직은 후속 이슈에서 구현
    - LLM 기반 정보 추출 및 통합
    - 완료율 계산
    - 단계 전환 로직
    """

    # 정보 통합 분석 후 QuestionGenerator 노드로 전환 (임시 값)
    return {
        **state,
        "stage_complete": False,
        "all_complete": False,
        "completion_percentage": 0.0,
        "next_node": "question_generator",
    }
