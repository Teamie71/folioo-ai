"""Interviewer 노드 - 질문 생성 및 대화 진행"""
from ..state import InterviewState

def run(state: InterviewState) -> InterviewState:
    """
    고정 질문 또는 생성 질문 수행
    - stages.yaml에서 고정 질문 로드
    - LLM 기반 추가 질문 생성
    
    TODO: 실제 질문 생성 로직은 후속 이슈에서 구현
    - YAML 설정 로드
    - LLM 기반 동적 질문 생성
    - 대화 컨텍스트 관리
    """
    
    # 질문 후 Supervisor 노드로 전환 (임시 값)
    return {
        **state,
        "next_node": "supervisor",
    }