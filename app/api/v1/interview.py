"""인터뷰 API 라우터"""

import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.schemas.interview import (
    ChatRequest,
    ChatResponse,
    CollectedFieldSchema,
    CreateSessionRequest,
    CreateSessionResponse,
    MessageSchema,
    SessionStateResponse,
    StageProgressSchema,
)
from common.sse import SSEErrorCode, SSEEventType
from features.interview import get_interview_service

router = APIRouter(prefix="/interview", tags=["interview"])
logger = logging.getLogger(__name__)

# ping 전송 간격 (초)
_PING_INTERVAL_SECONDS = 10
_STREAM_END = object()


async def _interleave_ping_events(stream):
    """SSE 스트림에 ping 이벤트를 인터리빙"""
    stream_iter = aiter(stream)
    next_event_task = asyncio.create_task(anext(stream_iter, _STREAM_END))

    try:
        while True:
            done, _ = await asyncio.wait({next_event_task}, timeout=_PING_INTERVAL_SECONDS)

            if done:
                try:
                    event_data = next_event_task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("업스트림 SSE 스트림 처리 중 예외 발생")
                    yield ServerSentEvent(
                        event=SSEEventType.ERROR,
                        data=json.dumps(
                            {
                                "type": SSEEventType.ERROR,
                                "error": {
                                    "code": SSEErrorCode.STREAM_EVENT_ERROR,
                                    "message": "SSE 스트림 처리 중 오류가 발생했습니다.",
                                },
                            },
                            ensure_ascii=False,
                        ),
                    )
                    break

                if event_data is _STREAM_END:
                    break
                if (
                    not isinstance(event_data, dict)
                    or "event" not in event_data
                    or "data" not in event_data
                ):
                    logger.error("잘못된 SSE 이벤트 포맷: %r", event_data)
                    yield ServerSentEvent(
                        event=SSEEventType.ERROR,
                        data=json.dumps(
                            {
                                "type": SSEEventType.ERROR,
                                "error": {
                                    "code": SSEErrorCode.INVALID_STREAM_EVENT,
                                    "message": "SSE 이벤트 포맷이 올바르지 않습니다.",
                                },
                            },
                            ensure_ascii=False,
                        ),
                    )
                    break

                yield ServerSentEvent(
                    event=event_data["event"],
                    data=event_data["data"],
                )
                next_event_task = asyncio.create_task(anext(stream_iter, _STREAM_END))
            else:
                # 타임아웃 -> ping 이벤트 전송 (next_event_task는 유지)
                yield ServerSentEvent(
                    event=SSEEventType.PING,
                    data=json.dumps(
                        {
                            "type": SSEEventType.PING,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                        ensure_ascii=False,
                    ),
                )
    finally:
        if not next_event_task.done():
            next_event_task.cancel()
            with suppress(asyncio.CancelledError):
                await next_event_task


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="인터뷰 세션 생성",
    description="새로운 인터뷰 세션을 생성하고 첫 AI 질문을 반환합니다.",
)
async def create_session(request: CreateSessionRequest) -> CreateSessionResponse:
    """
    새 인터뷰 세션 생성

    - 세션 ID는 서버에서 자동 생성 (UUID)
    - 첫 AI 질문이 함께 반환됨
    """

    service = get_interview_service()
    session_id = str(uuid4())

    try:
        result = await service.create_session(
            user_id=request.user_id,
            session_id=session_id,
            experience_name=request.experience_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return CreateSessionResponse(
        session_id=result["session_id"],
        first_question=result["first_question"],
        current_stage=result["current_stage"],
        stage_progress=StageProgressSchema(**result["stage_progress"]),
    )


@router.post(
    "/sessions/stream",
    status_code=status.HTTP_201_CREATED,
    summary="인터뷰 세션 생성 (SSE 스트리밍)",
    description="새로운 인터뷰 세션을 생성하고 첫 AI 질문을 SSE 스트리밍으로 반환합니다.",
    responses={
        201: {
            "description": "SSE 스트림",
            "content": {"text/event-stream": {}},
        },
    },
)
async def create_session_stream(request: CreateSessionRequest):
    """새 인터뷰 세션 생성 및 첫 질문 SSE 스트리밍"""
    service = get_interview_service()
    session_id = str(uuid4())

    stream = service.create_session_stream(
        user_id=request.user_id,
        session_id=session_id,
        experience_name=request.experience_name,
    )

    return EventSourceResponse(
        _interleave_ping_events(stream),
        status_code=status.HTTP_201_CREATED,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 프록시 버퍼링 비활성화
        },
    )


@router.post(
    "/sessions/{session_id}/chat",
    response_model=ChatResponse,
    summary="메시지 전송",
    description="사용자 메시지를 전송하고 AI 응답을 받습니다.",
)
async def chat(session_id: str, request: ChatRequest) -> ChatResponse:
    """
    사용자 메시지 처리 및 AI 응답 생성

    - 세션이 존재하지 않으면 404 에러
    - AI 응답과 함께 현재 진행 상황 반환
    """
    service = get_interview_service()

    try:
        result = await service.process_message(
            session_id=session_id,
            message=request.message,
            file_ids=request.file_ids,
            mentioned_insight_ids=request.mentioned_insight_ids,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    return ChatResponse(
        ai_response=result["ai_response"],
        current_stage=result["current_stage"],
        stage_progress=StageProgressSchema(**result["stage_progress"]),
        overall_completion=result["overall_completion"],
        all_complete=result["all_complete"],
    )


@router.get(
    "/sessions/{session_id}/state",
    summary="세션 상태 조회",
    description="현재 세션의 전체 상태를 조회합니다.",
)
async def get_session_state(session_id: str) -> SessionStateResponse:
    """
    세션 상태 조회

    - 세션이 존재하지 않으면 404 에러
    - 수집된 데이터, 대화 기록 등 전체 상태 반환
    """

    service = get_interview_service()
    state = await service.get_session_state(session_id)

    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"세션을 찾을 수 없습니다: {session_id}",
        )

    # 메시지 변환
    messages = [
        MessageSchema(
            type=msg.type,
            content=msg.content,
            id=getattr(msg, "id", None),
        )
        for msg in state["messages"]
    ]

    # collected_data 변환
    collected_data = {}
    for stage_key, fields in state["collected_data"].items():
        collected_data[stage_key] = {
            field_name: CollectedFieldSchema(**field_data)
            for field_name, field_data in fields.items()
        }

    return SessionStateResponse(
        session_id=state["session_id"],
        user_id=state["user_id"],
        experience_name=state["experience_name"],
        current_stage=state["current_stage"],
        stage_progress=StageProgressSchema(**state["stage_progress"]),
        overall_completion=state["overall_completion_percentage"],
        all_complete=state["all_stages_complete"],
        message_count=len(state["messages"]),
        is_extended_mode=state["is_extended_mode"],
        collected_data=collected_data,
        messages=messages,
    )


@router.post(
    "/sessions/{session_id}/chat/stream",
    summary="메시지 전송 (SSE 스트리밍)",
    description="사용자 메시지를 전송하고 AI 응답을 SSE 스트리밍으로 받습니다.",
    responses={
        200: {
            "description": "SSE 스트림",
            "content": {"text/event-stream": {}},
        },
    },
)
async def chat_stream(session_id: str, request: ChatRequest):
    """
    사용자 메시지 처리 및 AI 응답 SSE 스트리밍

    이벤트 타입:
    - content_block_delta: LLM 토큰 스트리밍
    - message_complete: 최종 결과 (전체 응답 + 진행 상황)
    - error: 에러 발생
    - ping: 연결 유지 (10초 간격)
    """

    service = get_interview_service()

    async def event_generator():
        """SSE 이벤트를 생성하는 비동기 제너레이터"""
        stream = service.process_message_stream(
            session_id=session_id,
            message=request.message,
            file_ids=request.file_ids,
            mentioned_insight_ids=request.mentioned_insight_ids,
        )
        async for event in _interleave_ping_events(stream):
            yield event

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 프록시 버퍼링 비활성화
        },
    )
