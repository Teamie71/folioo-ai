"""인터뷰 에이전트 비즈니스 로직"""

from langchain_core.messages import HumanMessage

from common.checkpointer.factory import get_checkpointer
from features.interview.agents.graph import build_graph
from features.interview.agents.state import (
    InterviewState,
    get_initial_interview_state,
)

# 모듈 레벨 싱글톤
_service: "InterviewService | None" = None


class InterviewService:
    """
    인터뷰 세션 관리 및 그래프 실행 오케스트레이터

    API 레이어와 LangGraph 사이에서 비즈니스 로직을 처리합니다.
    - 세션 생성/조회
    - 메시지 처리 및 AI 응답 생성
    - 상태 관리
    """

    def __init__(self):
        """Checkpointer가 연결된 그래프 초기화"""
        self._graph = build_graph(checkpointer=get_checkpointer())

    def create_session(
        self,
        user_id: str,
        session_id: str,
        experience_name: str,
    ) -> dict:
        """
        새 인터뷰 세션 생성 및 첫 AI 질문 생성

        Args:
            user_id: 사용자 ID
            session_id: 세션 ID (UUID, 호출자가 생성)
            experience_name: 정리할 경험/프로젝트 이름

        Returns:
            dict: 세션 생성 결과
                - session_id: 세션 ID
                - first_question: AI의 첫 질문
                - current_stage: 현재 단계 (1)
                - stage_progress: 단계 진행 상황

        Raises:
            ValueError: 필수 파라미터가 비어있는 경우
        """
        if not user_id or not session_id or not experience_name:
            raise ValueError("user_id, session_id, experience_name은 필수입니다.")

        # 초기 상태 생성
        initial_state = get_initial_interview_state(
            user_id=user_id,
            session_id=session_id,
            experience_name=experience_name,
        )

        # 그래프 실행 (첫 질문 생성)
        result = self._graph.invoke(
            initial_state,
            config={"configurable": {"thread_id": session_id}},
        )

        return {
            "session_id": session_id,
            "first_question": result["messages"][-1].content,
            "current_stage": result["current_stage"],
            "stage_progress": result["stage_progress"],
        }

    def process_message(
        self,
        session_id: str,
        message: str,
        file_ids: list[str] | None = None,
    ) -> dict:
        """
        사용자 메시지 처리 및 AI 응답 생성

        Args:
            session_id: 세션 ID
            message: 사용자 메시지
            file_ids: 현재 턴에서 업로드된 파일 ID 목록 (선택)

        Returns:
            dict: 처리 결과
                - ai_response: AI 응답 메시지
                - current_stage: 현재 단계
                - stage_progress: 단계 진행 상황
                - overall_completion: 전체 완료율 (0.0 ~ 100.0)
                - all_complete: 모든 단계 완료 여부

        Raises:
            ValueError: 세션이 존재하지 않는 경우
        """

        # 세션 존재 확인
        current_state = self.get_session_state(session_id)
        if current_state is None:
            raise ValueError(f"세션을 찾을 수 없습니다: {session_id}")

        # 입력 상태 구성
        input_state: dict = {
            "messages": [HumanMessage(content=message)],
        }

        # 파일 ID가 있으면 추가
        if file_ids:
            input_state["current_turn_files"] = file_ids

        # 그래프 실행 (Checkpointer가 이전 상태 자동 로드)
        result = self._graph.invoke(
            input_state,
            config={"configurable": {"thread_id": session_id}},
        )

        return {
            "ai_response": result["messages"][-1].content,
            "current_stage": result["current_stage"],
            "stage_progress": result["stage_progress"],
            "overall_completion": result["overall_completion_percentage"],
            "all_complete": result["all_stages_complete"],
        }

    def get_session_state(self, session_id: str) -> InterviewState | None:
        """
        현재 세션 상태 조회

        Args:
            session_id: 세션 ID

        Returns:
            InterviewState | None: 세션 상태 (없으면 None)
        """

        state_snapshot = self._graph.get_state(config={"configurable": {"thread_id": session_id}})

        if state_snapshot is None or not state_snapshot.values:
            return None

        return state_snapshot.values


def get_interview_service() -> InterviewService:
    """
    InterviewService 싱글톤 반환

    Returns:
        InterviewService: 인터뷰 서비스 인스턴스
    """

    global _service

    if _service is None:
        _service = InterviewService()

    return _service


def reset_interview_service() -> None:
    """
    InterviewService 싱글톤 초기화 (테스트용)

    테스트 간 격리를 위해 사용
    """

    global _service
    _service = None
