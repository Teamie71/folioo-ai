"""인터뷰 에이전트 비즈니스 로직"""

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import suppress

from langchain_core.messages import AIMessage, HumanMessage

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
    InterviewSessionStatus,
    InterviewState,
    ensure_interview_state_defaults,
    get_initial_interview_state,
)
from features.interview.config.loader import get_global_config

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

    @staticmethod
    def _get_latest_ai_response(messages: list) -> str | None:
        """메시지 목록에서 가장 최근 AI 응답 텍스트를 반환"""
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                content = message.content
                if isinstance(content, str):
                    return content
                if content is None:
                    return None
                return str(content)
        return None

    @staticmethod
    def _build_extension_meta(state: dict | None) -> dict:
        """응답 페이로드용 연장 메타데이터 구성"""
        state = state or {}
        return {
            "is_extended_mode": bool(state.get("is_extended_mode", False)),
            "extension_turns_used": state.get("extension_turns_used"),
            "extension_turns_max": state.get("extension_turns_max"),
        }

    @staticmethod
    def _get_thread_config(session_id: str) -> dict[str, dict[str, str]]:
        """세션별 LangGraph thread config를 반환"""
        return {"configurable": {"thread_id": session_id}}

    async def _set_session_status(
        self,
        session_id: str,
        status: InterviewSessionStatus,
        fallback_state: dict | None = None,
    ) -> None:
        """세션 상태(status)를 저장한다."""
        state_update: dict = {"status": status}

        if fallback_state is not None:
            current_state = await self.get_session_state(session_id)
            if current_state is None:
                state_update = {**fallback_state, "status": status}

        await self._graph.aupdate_state(self._get_thread_config(session_id), state_update)

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
        # "generating" 상태를 체크포인터에 즉시 저장 (process_message_stream, extend_session_stream과 일관성)
        # 세션 최초 생성이므로 fallback_state로 초기 상태 전달 → 체크포인터에 세션이 없으면 전체 초기 상태를 포함해 저장
        await self._set_session_status(session_id, "generating", fallback_state=initial_state)
        config = self._get_thread_config(session_id)
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
                await self._set_session_status(
                    session_id,
                    "completed",
                    fallback_state=initial_state,
                )
                final_state = {**final_state, "status": "completed"}
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
                                "status": final_state["status"],
                                "current_stage": final_state["current_stage"],
                                "stage_progress": final_state["stage_progress"],
                                **self._build_extension_meta(final_state),
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            else:
                with suppress(Exception):
                    await self._set_session_status(
                        session_id,
                        "failed",
                        fallback_state=initial_state,
                    )
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
            with suppress(Exception):
                await self._set_session_status(
                    session_id,
                    "failed",
                    fallback_state=initial_state,
                )
            logger.exception(f"SSE 스트리밍 중 예외 발생: {e}")
            yield {
                "event": SSEEventType.ERROR,
                "data": json.dumps(
                    {
                        "type": SSEEventType.ERROR,
                        "error": {
                            "code": SSEErrorCode.LLM_ERROR,
                            "message": "처리 중 오류가 발생했습니다.",
                        },
                    },
                    ensure_ascii=False,
                ),
            }

    async def extend_session(self, session_id: str) -> dict:
        """완료된 세션을 연장 모드로 전환하고 첫 연장 질문을 생성"""
        current_state = await self.get_session_state(session_id)
        if current_state is None:
            raise ValueError(f"세션을 찾을 수 없습니다: {session_id}")

        global_config = get_global_config()
        if not current_state["all_stages_complete"]:
            raise ValueError("연장은 모든 단계 완료 후에만 시작할 수 있습니다.")

        if current_state["extension_count"] >= global_config.max_extensions:
            raise ValueError("최대 연장 횟수에 도달하여 더 이상 연장할 수 없습니다.")

        input_state = {
            "is_extended_mode": True,
            "all_stages_complete": False,
            "extension_count": current_state["extension_count"] + 1,
            "extension_turns_used": 0,
            "extension_turns_max": global_config.extension_turns_per_session,
            "mentioned_insight": None,
        }

        result = await self._graph.ainvoke(
            input_state,
            config={"configurable": {"thread_id": session_id}},
        )

        ai_response = self._get_latest_ai_response(result.get("messages", []))
        if ai_response is None:
            raise ValueError("연장 질문을 생성하지 못했습니다.")

        return {
            "ai_response": ai_response,
            "extension_count": result.get("extension_count", input_state["extension_count"]),
            "extension_turns_max": result.get(
                "extension_turns_max",
                input_state["extension_turns_max"],
            ),
        }

    async def extend_session_stream(self, session_id: str) -> AsyncGenerator[dict, None]:
        """완료된 세션을 연장 모드로 전환하고 첫 연장 질문을 SSE로 스트리밍"""
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

        global_config = get_global_config()
        if not current_state["all_stages_complete"]:
            yield {
                "event": SSEEventType.ERROR,
                "data": json.dumps(
                    {
                        "type": SSEEventType.ERROR,
                        "error": {
                            "code": SSEErrorCode.LLM_ERROR,
                            "message": "연장은 모든 단계 완료 후에만 시작할 수 있습니다.",
                        },
                    },
                    ensure_ascii=False,
                ),
            }
            return

        if current_state["extension_count"] >= global_config.max_extensions:
            yield {
                "event": SSEEventType.ERROR,
                "data": json.dumps(
                    {
                        "type": SSEEventType.ERROR,
                        "error": {
                            "code": SSEErrorCode.LLM_ERROR,
                            "message": "최대 연장 횟수에 도달하여 더 이상 연장할 수 없습니다.",
                        },
                    },
                    ensure_ascii=False,
                ),
            }
            return

        input_state = {
            "is_extended_mode": True,
            "all_stages_complete": False,
            "extension_count": current_state["extension_count"] + 1,
            "extension_turns_used": 0,
            "extension_turns_max": global_config.extension_turns_per_session,
            "mentioned_insight": None,
        }
        config = self._get_thread_config(session_id)
        accumulated_text = ""

        try:
            await self._set_session_status(session_id, "generating")
            async for event in self._graph.astream_events(input_state, config=config, version="v2"):
                event_type = event.get("event")
                metadata = event.get("metadata", {})
                node_name = metadata.get("langgraph_node")

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

            final_state = await self.get_session_state(session_id)
            if final_state:
                await self._set_session_status(session_id, "completed")
                final_state = {**final_state, "status": "completed"}
                ai_response = accumulated_text or self._get_latest_ai_response(
                    final_state.get("messages", [])
                )

                yield {
                    "event": SSEEventType.MESSAGE_COMPLETE,
                    "data": json.dumps(
                        {
                            "type": SSEEventType.MESSAGE_COMPLETE,
                            "message": {
                                "ai_response": ai_response,
                                "status": final_state["status"],
                                "current_stage": final_state["current_stage"],
                                "stage_progress": final_state["stage_progress"],
                                "overall_completion": final_state["overall_completion_percentage"],
                                "all_complete": final_state["all_stages_complete"],
                                **self._build_extension_meta(final_state),
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            else:
                with suppress(Exception):
                    await self._set_session_status(session_id, "failed")
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
            with suppress(Exception):
                await self._set_session_status(session_id, "failed")
            logger.exception(f"SSE 스트리밍 중 예외 발생: {e}")
            yield {
                "event": SSEEventType.ERROR,
                "data": json.dumps(
                    {
                        "type": SSEEventType.ERROR,
                        "error": {
                            "code": SSEErrorCode.LLM_ERROR,
                            "message": "처리 중 오류가 발생했습니다.",
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
        mentioned_insight: str | None = None,
    ) -> dict:
        """
        사용자 메시지 처리 및 AI 응답 생성

        Args:
            session_id: 세션 ID
            message: 사용자 메시지
            file_ids: 현재 턴에서 업로드된 파일 ID 목록 (선택)
            mentioned_insight: @ 멘션으로 참조한 인사이트 로그 ID (선택)

        Returns:
            dict: 처리 결과
                - ai_response: AI 응답 메시지 (없으면 None)
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
            "current_turn_files": file_ids or [],
            "mentioned_insight": mentioned_insight,
        }

        # 그래프 비동기 실행 (Checkpointer가 이전 상태 자동 로드)
        result = await self._graph.ainvoke(
            input_state,
            config=self._get_thread_config(session_id),
        )

        ai_response = None
        if not result["all_stages_complete"]:
            ai_response = self._get_latest_ai_response(result.get("messages", []))

        return {
            "ai_response": ai_response,
            "current_stage": result["current_stage"],
            "stage_progress": result["stage_progress"],
            "overall_completion": result["overall_completion_percentage"],
            "all_complete": result["all_stages_complete"],
            **self._build_extension_meta(result),
        }

    async def get_session_state(self, session_id: str) -> InterviewState | None:
        """
        현재 세션 상태 조회

        Args:
            session_id: 세션 ID

        Returns:
            InterviewState | None: 세션 상태 (없으면 None)
        """

        state_snapshot = await self._graph.aget_state(config=self._get_thread_config(session_id))

        if state_snapshot is None or not state_snapshot.values:
            return None

        return ensure_interview_state_defaults(state_snapshot.values)

    async def get_session_status(self, session_id: str) -> dict | None:
        """현재 세션의 경량 status 정보를 조회한다."""
        state = await self.get_session_state(session_id)
        if state is None:
            return None

        return {
            "session_id": state["session_id"],
            "status": state["status"],
            "current_stage": state["current_stage"],
            "all_complete": state["all_stages_complete"],
        }

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
                    "activity_name": insight.get("activity_name", ""),
                    "category": insight.get("category"),
                    "content": insight.get("content", ""),
                    "similarity_score": similarity,
                    "source": insight.get("source")
                    or ("search" if similarity is not None else "mention"),
                }
            )

        return {"type": SSEEventType.RETRIEVER_RESULT, "insights": insights}

    async def process_message_stream(
        self,
        session_id: str,
        message: str,
        file_ids: list[str] | None = None,
        mentioned_insight: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        사용자 메시지 처리 및 AI 응답 SSE 스트리밍

        LangGraph astream_events() API를 사용하여 토큰 단위 스트리밍을 제공합니다.
        question_generator 노드의 LLM 호출에서 발생하는 토큰을 실시간으로 전달합니다.

        Args:
            session_id: 세션 ID
            message: 사용자 메시지
            file_ids: 현재 턴에서 업로드된 파일 ID 목록 (선택)
            mentioned_insight: @ 멘션으로 참조한 인사이트 로그 ID (선택)

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
            "current_turn_files": file_ids or [],
            "mentioned_insight": mentioned_insight,
        }

        config = self._get_thread_config(session_id)
        accumulated_text = ""

        # 3. LangGraph astream_events로 스트리밍
        try:
            await self._set_session_status(session_id, "generating")
            async for event in self._graph.astream_events(input_state, config=config, version="v2"):
                event_type = event.get("event")
                metadata = event.get("metadata", {})
                node_name = metadata.get("langgraph_node")

                if event_type == LangGraphEventType.ON_CHAIN_START and node_name == "retriever":
                    yield {
                        "event": SSEEventType.RETRIEVER_STATUS,
                        "data": json.dumps(
                            {
                                "type": SSEEventType.RETRIEVER_STATUS,
                                "message": "대화 내용을 바탕으로 유사도가 높은 인사이트 로그를 읽었어요.",
                            },
                            ensure_ascii=False,
                        ),
                    }

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
                await self._set_session_status(session_id, "completed")
                final_state = {**final_state, "status": "completed"}
                ai_response: str | None = None
                if not final_state["all_stages_complete"]:
                    ai_response = accumulated_text or self._get_latest_ai_response(
                        final_state.get("messages", [])
                    )

                yield {
                    "event": SSEEventType.MESSAGE_COMPLETE,
                    "data": json.dumps(
                        {
                            "type": SSEEventType.MESSAGE_COMPLETE,
                            "message": {
                                "ai_response": ai_response,
                                "status": final_state["status"],
                                "current_stage": final_state["current_stage"],
                                "stage_progress": final_state["stage_progress"],
                                "overall_completion": final_state["overall_completion_percentage"],
                                "all_complete": final_state["all_stages_complete"],
                                **self._build_extension_meta(final_state),
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            else:
                with suppress(Exception):
                    await self._set_session_status(session_id, "failed")
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
            with suppress(Exception):
                await self._set_session_status(session_id, "failed")
            logger.exception(f"SSE 스트리밍 중 예외 발생: {e}")
            yield {
                "event": SSEEventType.ERROR,
                "data": json.dumps(
                    {
                        "type": SSEEventType.ERROR,
                        "error": {
                            "code": SSEErrorCode.LLM_ERROR,
                            "message": "처리 중 오류가 발생했습니다.",
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
