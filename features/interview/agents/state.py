"""인터뷰 에이전트의 공유 state 정의"""

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages


class InterviewState(TypedDict):
    """공유 상태 - 모든 노드가 읽고 쓸 수 있음"""

    # 세션 정보
    user_id: str
    session_id: str

    # 대화 기록 (LangGraph 메시지 리듀서 사용)
    messages: Annotated[list, add_messages]

    # 단계 관리
    current_stage: Literal[1, 2, 3, 4]
    fixed_q_count: int  # 완료된 고정 질문 수
    generated_q_count: int  # 완료된 생성 질문 수

    # 수집된 포트폴리오 정보
    collected_data: dict  # {"stage_1": {...}, "stage_2": {...}, ...}

    # 인사이트 로그
    mentioned_insights: list[str]  # @ 멘션된 insight ID들
    retrieved_insights: list[dict]  # 검색된 인사이트 목록

    # 파일 업로드
    uploaded_files: list[dict]  # [{"name": ..., "type": ..., "path": ...}]
    file_context: list[str]  # 파일에서 추출된 텍스트들

    # 라우팅
    next_node: Literal["supervisor", "file_processor", "interviewer", "analyst", "retriever", "end"]

    # 완료 상태
    stage_complete: bool  # 단계 완료 여부
    all_complete: bool  # 전체 완료 여부
    completion_percentage: float  # 완료율
