"""Supervisor 노드 - 라우팅 결정 및 흐름 제어"""
from ..state import InterviewState

def run(state: InterviewState) -> InterviewState:
    """
    Supervisor의 라우팅 우선순위:
    1. 파일 업로드 -> FileProcessor
    2. @ 멘션 또는 검색 필요 -> Retriever
    3. 단계 종료 조건 -> Analyst
    4. 기본 -> Interviewer
    
    TODO: 실제 라우팅 로직은 후속 이슈에서 구현
    """
    
    # 기본적으로 interviewer로 라우팅 (임시 값)
    return {
        **state,
        "next_node": "interviewer",
    }