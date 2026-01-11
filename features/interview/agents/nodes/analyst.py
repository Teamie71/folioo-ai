"""Analyst 노드 - 정보 추출 및 단계 전환"""

from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    대화에서 정보 추출 및 단계 전환 판단
    - 대화 내용에서 required_fields 추출
    - 단계 완료 여부 판단
    - 다음 단계로 전환 또는 종료

    TODO: 실제 분석 로직은 후속 이슈에서 구현
    - LLM 기반 정보 추출
    - 완료율 계산
    - 단계 전환 로직
    """

    # 분석 후 Supervisor 노드로 전환 (임시 값)
    return {
        **state,
        "stage_complete": False,
        "all_complete": False,
        "completion_percentage": 0.0,
        "next_node": "supervisor",
    }
