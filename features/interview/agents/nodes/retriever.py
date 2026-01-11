"""Retriever 노드 - 인사이트 로그 벡터 검색"""

from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    인사이트 로그 벡터 검색 (ChromaDB)
    - @ 멘션된 인사이트 검색
    - 유사도 기반 관련 인사이트 검색

    TODO: 실제 검색 로직은 후속 이슈에서 구현
    - ChromaDB 연동
    - 임베딩 및 유사도 검색
    """

    # 검색 후 Supervisor 노드로 전환 (임시 값)
    return {
        **state,
        "retrieved_insights": [],
        "next_node": "supervisor",
    }
