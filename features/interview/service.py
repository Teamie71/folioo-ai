"""인터뷰 에이전트 비즈니스 로직"""

import json
import logging
from collections.abc import AsyncGenerator

from langchain_core.messages import HumanMessage

from common.checkpointer.factory import get_checkpointer
from common.sse import (
    STREAMING_TARGET_NODES,
    LangGraphEventType,
    SSEDeltaType,
    SSEErrorCode,
    SSEEventType,
)
from features.interview.agents.graph import build_graph
from features.interview.agents.state import (
    InterviewState,
    get_initial_interview_state,
)

logger = logging.getLogger(__name__)

# 모듈 레벨 싱글톤
_service: "InterviewService | None" = None


class InterviewService:
    """
    인터뷰 세션 관리 및 그래프 실행 오케스트레이터

    API 레이어와 LangGraph 사이에서 비즈니스 로직을 처리합니다.
    - 세션 생성/조회
    - 메시지 처리 및 AI 응답 생성
    - 상태 관리
    - SSE 스트리밍
    """

    def __init__(self):
        """Checkpointer가 연결된 그래프 초기화"""
        self._graph = build_graph(checkpointer=get_checkpointer())

    async def create_session(
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

        # 그래프 비동기 실행 (첫 질문 생성)
        result = await self._graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": session_id}},
        )

        return {
            "session_id": session_id,
            "first_question": result["messages"][-1].content,
            "current_stage": result["current_stage"],
            "stage_progress": result["stage_progress"],
        }

    async def create_session_stream(
        self,
        user_id: str,
        session_id: str,
        experience_name: str,
    ) -> AsyncGenerator[dict, None]:
        """
        새 인터뷰 세션 생성 및 첫 AI 질문 SSE 스트리밍

        Args:
            user_id: 사용자 ID
            session_id: 세션 ID (UUID, 호출자가 생성)
            experience_name: 정리할 경험/프로젝트 이름

        Yields:
            dict: SSE 이벤트 데이터
        """
        if not user_id or not session_id or not experience_name:
            raise ValueError("user_id, session_id, experience_name은 필수입니다.")

        # 1. 초기 입력 상태 생성
        initial_state = get_initial_interview_state(
            user_id=user_id,
            session_id=session_id,
            experience_name=experience_name,
        )
        config = {"configurable": {"thread_id": session_id}}
        accumulated_text = ""

        # 2. 스트리밍 진행
        try:
            async for event in self._graph.astream_events(
                initial_state, config=config, version="v2"
            ):
                event_type = event.get("event")
                metadata = event.get("metadata", {})
                node_name = metadata.get("langgraph_node")

                # 3. 스트리밍 대상 노드의 LLM 토큰 스트리밍 이벤트만 처리
                if (
                    event_type == LangGraphEventType.ON_CHAT_MODEL_STREAM
                    and node_name in STREAMING_TARGET_NODES
                ):
                    chunk = event["data"].get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        token_text = chunk.content
                        accumulated_text += token_text

                        yield {
                            "event": SSEEventType.CONTENT_BLOCK_DELTA,
                            "data": json.dumps(
                                {
                                    "type": SSEEventType.CONTENT_BLOCK_DELTA,
                                    "delta": {
                                        "type": SSEDeltaType.TEXT_DELTA,
                                        "text": token_text,
                                    },
                                },
                                ensure_ascii=False,
                            ),
                        }

            # 4. 스트리밍 완료 후 최종 상태에서 첫 질문 추출
            final_state = await self.get_session_state(session_id)
            if final_state:
                first_question = accumulated_text
                messages = final_state.get("messages", [])
                if not first_question and messages:
                    first_question = messages[-1].content

                yield {
                    "event": SSEEventType.MESSAGE_COMPLETE,
                    "data": json.dumps(
                        {
                            "type": SSEEventType.MESSAGE_COMPLETE,
                            "message": {
                                "session_id": session_id,
                                "first_question": first_question,
                                "current_stage": final_state["current_stage"],
                                "stage_progress": final_state["stage_progress"],
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            else:
                logger.error(f"세션 상태를 찾을 수 없습니다: {session_id}")
                yield {
                    "event": SSEEventType.ERROR,
                    "data": json.dumps(
                        {
                            "type": SSEEventType.ERROR,
                            "error": {
                                "code": SSEErrorCode.FINAL_STATE_MISSING,
                                "message": f"최종 상태를 조회할 수 없습니다: {session_id}",
                            },
                        },
                        ensure_ascii=False,
                    ),
                }

        except Exception as e:
            logger.exception(f"SSE 스트리밍 중 예외 발생: {e}")
            yield {
                "event": SSEEventType.ERROR,
                "data": json.dumps(
                    {
                        "type": SSEEventType.ERROR,
                        "error": {
                            "code": SSEErrorCode.LLM_ERROR,
                            "message": f"처리 중 오류가 발생했습니다: {str(e)}",
                        },
                    },
                    ensure_ascii=False,
                ),
            }

    async def process_message(
        self,
        session_id: str,
        message: str,
        file_ids: list[str] | None = None,
        mentioned_insight_ids: list[str] | None = None,
    ) -> dict:
        """
        사용자 메시지 처리 및 AI 응답 생성

        Args:
            session_id: 세션 ID
            message: 사용자 메시지
            file_ids: 현재 턴에서 업로드된 파일 ID 목록 (선택)
            mentioned_insight_ids: @ 멘션으로 참조한 인사이트 로그 ID 목록 (선택)

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
        current_state = await self.get_session_state(session_id)
        if current_state is None:
            raise ValueError(f"세션을 찾을 수 없습니다: {session_id}")

        # 입력 상태 구성
        input_state: dict = {
            "messages": [HumanMessage(content=message)],
        }

        # 파일 ID가 있으면 추가
        if file_ids:
            input_state["current_turn_files"] = file_ids

        # @ 멘션된 인사이트 ID (매 턴 초기화 필요)
        input_state["mentioned_insight_ids"] = mentioned_insight_ids or []

        # 그래프 비동기 실행 (Checkpointer가 이전 상태 자동 로드)
        result = await self._graph.ainvoke(
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

    async def get_session_state(self, session_id: str) -> InterviewState | None:
        """
        현재 세션 상태 조회

        Args:
            session_id: 세션 ID

        Returns:
            InterviewState | None: 세션 상태 (없으면 None)
        """

        state_snapshot = await self._graph.aget_state(
            config={"configurable": {"thread_id": session_id}}
        )

        if state_snapshot is None or not state_snapshot.values:
            return None

        return state_snapshot.values

    @staticmethod
    def _build_retriever_result_payload(output: dict | None) -> dict:
        """Retriever 결과를 SSE 전송 포맷으로 변환"""
        if not output or not isinstance(output, dict):
            return {"type": SSEEventType.RETRIEVER_RESULT, "insights": []}

        retrieved_insights = output.get("retrieved_insights", [])
        insights = []
        for insight in retrieved_insights:
            similarity = insight.get("similarity_score")
            insights.append(
                {
                    "id": insight.get("id"),
                    "title": insight.get("title"),
                    "category": insight.get("category"),
                    "similarity": similarity,
                    "source": "search" if similarity is not None else "mention",
                }
            )

        return {"type": SSEEventType.RETRIEVER_RESULT, "insights": insights}

    async def process_message_stream(
        self,
        session_id: str,
        message: str,
        file_ids: list[str] | None = None,
        mentioned_insight_ids: list[str] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        사용자 메시지 처리 및 AI 응답 SSE 스트리밍

        LangGraph astream_events() API를 사용하여 토큰 단위 스트리밍을 제공합니다.
        question_generator 노드의 LLM 호출에서 발생하는 토큰을 실시간으로 전달합니다.

        Args:
            session_id: 세션 ID
            message: 사용자 메시지
            file_ids: 현재 턴에서 업로드된 파일 ID 목록 (선택)
            mentioned_insight_ids: @ 멘션으로 참조한 인사이트 로그 ID 목록 (선택)

        Yields:
            dict: SSE 이벤트 데이터
                - {"event": "content_block_delta", "data": {...}}
                - {"event": "message_complete", "data": {...}}
                - {"event": "error", "data": {...}}
        """
        # 1. 세션 존재 확인
        current_state = await self.get_session_state(session_id)
        if current_state is None:
            yield {
                "event": SSEEventType.ERROR,
                "data": json.dumps(
                    {
                        "type": SSEEventType.ERROR,
                        "error": {
                            "code": SSEErrorCode.SESSION_NOT_FOUND,
                            "message": f"세션을 찾을 수 없습니다: {session_id}",
                        },
                    },
                    ensure_ascii=False,
                ),
            }
            return

        # 2. 입력 상태 구성
        input_state: dict = {
            "messages": [HumanMessage(content=message)],
            "mentioned_insight_ids": mentioned_insight_ids or [],
        }
        if file_ids:
            input_state["current_turn_files"] = file_ids

        config = {"configurable": {"thread_id": session_id}}
        accumulated_text = ""

        # 3. LangGraph astream_events로 스트리밍
        try:
            async for event in self._graph.astream_events(input_state, config=config, version="v2"):
                event_type = event.get("event")
                metadata = event.get("metadata", {})
                node_name = metadata.get("langgraph_node")

                if event_type == LangGraphEventType.ON_CHAIN_END and node_name == "retriever":
                    output = event.get("data", {}).get("output")
                    yield {
                        "event": SSEEventType.RETRIEVER_RESULT,
                        "data": json.dumps(
                            self._build_retriever_result_payload(output),
                            ensure_ascii=False,
                        ),
                    }

                # 스트리밍 대상 노드의 LLM 토큰 스트리밍 이벤트만 처리
                if (
                    event_type == LangGraphEventType.ON_CHAT_MODEL_STREAM
                    and node_name in STREAMING_TARGET_NODES
                ):
                    chunk = event["data"].get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        token_text = chunk.content
                        accumulated_text += token_text

                        yield {
                            "event": SSEEventType.CONTENT_BLOCK_DELTA,
                            "data": json.dumps(
                                {
                                    "type": SSEEventType.CONTENT_BLOCK_DELTA,
                                    "delta": {
                                        "type": SSEDeltaType.TEXT_DELTA,
                                        "text": token_text,
                                    },
                                },
                                ensure_ascii=False,
                            ),
                        }
            # 4. 스트리밍 완료 후 최종 상태 조회 -> message_complete 전송
            final_state = await self.get_session_state(session_id)
            if final_state:
                # accumulated_text가 없는 경우 (단계 완료 등) 최종 메시지에서 가져옴
                ai_response = accumulated_text
                if not ai_response and final_state["messages"]:
                    ai_response = final_state["messages"][-1].content

                yield {
                    "event": SSEEventType.MESSAGE_COMPLETE,
                    "data": json.dumps(
                        {
                            "type": SSEEventType.MESSAGE_COMPLETE,
                            "message": {
                                "ai_response": ai_response,
                                "current_stage": final_state["current_stage"],
                                "stage_progress": final_state["stage_progress"],
                                "overall_completion": final_state["overall_completion_percentage"],
                                "all_complete": final_state["all_stages_complete"],
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            else:
                logger.error(f"세션 상태를 찾을 수 없습니다: {session_id}")
                yield {
                    "event": SSEEventType.ERROR,
                    "data": json.dumps(
                        {
                            "type": SSEEventType.ERROR,
                            "error": {
                                "code": SSEErrorCode.FINAL_STATE_MISSING,
                                "message": f"최종 상태를 조회할 수 없습니다: {session_id}",
                            },
                        },
                        ensure_ascii=False,
                    ),
                }

        except Exception as e:
            logger.exception(f"SSE 스트리밍 중 예외 발생: {e}")
            yield {
                "event": SSEEventType.ERROR,
                "data": json.dumps(
                    {
                        "type": SSEEventType.ERROR,
                        "error": {
                            "code": SSEErrorCode.LLM_ERROR,
                            "message": f"처리 중 오류가 발생했습니다: {str(e)}",
                        },
                    },
                    ensure_ascii=False,
                ),
            }


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
