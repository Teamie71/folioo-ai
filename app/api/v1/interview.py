"""인터뷰 API 라우터"""

import asyncio
import json
import logging
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.schemas.interview import (
    ChatResponse,
    CollectedFieldSchema,
    CreateSessionRequest,
    CreateSessionResponse,
    ErrorResponse,
    ExtendSessionResponse,
    FileMetadataSchema,
    FileTurnRecordSchema,
    InsightLogSchema,
    InsightTurnRecordSchema,
    MessageSchema,
    SessionStateResponse,
    SessionStatusResponse,
    StageProgressSchema,
)
from common.sse import SSEErrorCode, SSEEventType
from features.interview import get_interview_service
from features.interview.agents.state import FilePayload

router = APIRouter(prefix="/interview", tags=["interview"])
logger = logging.getLogger(__name__)

# ping 전송 간격 (초)
_PING_INTERVAL_SECONDS = 10
_STREAM_END = object()
_MAX_FILES_PER_TURN = 3
_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
_FILE_UPLOAD_CHUNK_SIZE_BYTES = 1024 * 1024
_ALLOWED_CONTENT_TYPES = {"application/pdf", "image/png", "image/jpeg"}
_ALLOWED_FILE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


def _map_service_error_to_http_error(message: str) -> HTTPException:
    """서비스 ValueError를 HTTP 에러로 변환"""
    if "세션을 찾을 수 없습니다" in message:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)

    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _normalize_content_type(content_type: str | None) -> str:
    """업로드 파일 content-type을 정규화한다."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def _create_temp_upload_file(suffix: str) -> Path:
    """업로드 파일을 저장할 임시 파일 경로를 생성한다."""
    file_descriptor, temp_path = tempfile.mkstemp(prefix="interview-upload-", suffix=suffix)
    os.close(file_descriptor)
    return Path(temp_path)


def _cleanup_temp_files(files: list[FilePayload] | None) -> None:
    """임시 업로드 파일을 정리한다."""
    for file in files or []:
        temp_path = file.get("temp_path")
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("임시 업로드 파일 정리에 실패했습니다: %s", temp_path, exc_info=True)


async def _read_and_validate_files(files: list[UploadFile] | None) -> list[FilePayload]:
    """multipart 업로드 파일을 읽고 인터뷰 파일 참조 payload로 변환한다."""
    upload_files = list(files or [])

    payloads: list[FilePayload] = []

    try:
        if len(upload_files) > _MAX_FILES_PER_TURN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"파일은 한 번에 최대 {_MAX_FILES_PER_TURN}개까지 업로드할 수 있습니다.",
            )

        for file in upload_files:
            normalized_content_type = _normalize_content_type(file.content_type)
            file_extension = Path(file.filename or "").suffix.lower()

            if normalized_content_type not in _ALLOWED_CONTENT_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="PDF, PNG, JPG(JPEG) 파일만 업로드할 수 있습니다.",
                )

            if file_extension not in _ALLOWED_FILE_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="PDF, PNG, JPG(JPEG) 파일만 업로드할 수 있습니다.",
                )

            temp_path = _create_temp_upload_file(file_extension)
            bytes_written = 0

            try:
                with temp_path.open("wb") as temp_file:
                    while chunk := await file.read(_FILE_UPLOAD_CHUNK_SIZE_BYTES):
                        bytes_written += len(chunk)
                        if bytes_written > _MAX_FILE_SIZE_BYTES:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"{file.filename or '파일'} 크기는 10MB를 초과할 수 없습니다.",
                            )
                        temp_file.write(chunk)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

            payloads.append(
                {
                    "filename": file.filename or "",
                    "content_type": normalized_content_type,
                    "temp_path": str(temp_path),
                    "file_size": bytes_written,
                }
            )

        return payloads
    except Exception:
        _cleanup_temp_files(payloads)
        raise
    finally:
        for file in upload_files:
            with suppress(Exception):
                await file.close()


async def _interleave_ping_events(stream):
    """SSE 스트림에 ping 이벤트를 인터리빙"""
    stream_iter = aiter(stream)
    stream_iter_aclose = getattr(stream_iter, "aclose", None)
    stream_iter_closed = False
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
                    is_dict = isinstance(event_data, dict)
                    invalid_event_meta = {
                        "is_dict": is_dict,
                        "type": event_data.get("type") if is_dict else None,
                        "id": event_data.get("id") if is_dict else None,
                        "keys": list(event_data.keys()) if is_dict else None,
                        "payload_redacted": True,
                    }
                    logger.error("잘못된 SSE 이벤트 페이로드: %s", invalid_event_meta)
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
                    if callable(stream_iter_aclose):
                        try:
                            await stream_iter_aclose()
                        except Exception:
                            logger.warning(
                                "잘못된 SSE 이벤트 종료 처리 중 upstream 스트림 정리 실패",
                                exc_info=True,
                            )
                        finally:
                            stream_iter_closed = True
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
        if not stream_iter_closed and callable(stream_iter_aclose):
            with suppress(Exception):
                await stream_iter_aclose()


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="인터뷰 세션 생성",
    description="새로운 인터뷰 세션을 생성하고 첫 AI 질문을 반환합니다.",
    responses={400: {"model": ErrorResponse, "description": "잘못된 요청"}},
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
        400: {"model": ErrorResponse, "description": "잘못된 요청"},
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
            "X-Session-Id": session_id,
            "X-Accel-Buffering": "no",  # Nginx 프록시 버퍼링 비활성화
        },
    )


@router.post(
    "/sessions/{session_id}/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="메시지 전송",
    description="사용자 메시지를 전송하고 AI 응답을 받습니다.",
    responses={
        400: {"model": ErrorResponse, "description": "multipart 파일 검증 실패"},
        404: {"model": ErrorResponse, "description": "세션을 찾을 수 없음"},
    },
)
async def chat(
    session_id: str,
    message: str = Form(..., min_length=1),
    mentioned_insight: str | None = Form(None),
    files: list[UploadFile] | None = File(None),
) -> ChatResponse:
    """
    사용자 메시지 처리 및 AI 응답 생성

    - 세션이 존재하지 않으면 404 에러
    - AI 응답과 함께 현재 진행 상황 반환
    """
    service = get_interview_service()
    validated_files = await _read_and_validate_files(files)

    try:
        result = await service.process_message(
            session_id=session_id,
            message=message,
            files=validated_files,
            mentioned_insight=mentioned_insight,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    finally:
        _cleanup_temp_files(validated_files)

    return ChatResponse(
        ai_response=result["ai_response"],
        current_stage=result["current_stage"],
        stage_progress=StageProgressSchema(**result["stage_progress"]),
        overall_completion=result["overall_completion"],
        all_complete=result["all_complete"],
        is_extended_mode=result["is_extended_mode"],
        extension_turns_used=result["extension_turns_used"],
        extension_turns_max=result["extension_turns_max"],
    )


@router.post(
    "/sessions/{session_id}/extend",
    response_model=ExtendSessionResponse,
    status_code=status.HTTP_200_OK,
    summary="연장 모드 시작",
    description="완료된 인터뷰 세션을 연장 모드로 전환하고 첫 질문을 생성합니다.",
    responses={
        400: {"model": ErrorResponse, "description": "연장 불가 상태"},
        404: {"model": ErrorResponse, "description": "세션을 찾을 수 없음"},
    },
)
async def extend_session(session_id: str) -> ExtendSessionResponse:
    """완료된 세션의 연장 모드 시작"""
    service = get_interview_service()

    try:
        result = await service.extend_session(session_id)
    except ValueError as e:
        raise _map_service_error_to_http_error(str(e))

    return ExtendSessionResponse(
        ai_response=result["ai_response"],
        extension_count=result["extension_count"],
        extension_turns_max=result["extension_turns_max"],
    )


@router.post(
    "/sessions/{session_id}/extend/stream",
    status_code=status.HTTP_200_OK,
    summary="연장 모드 시작 (SSE 스트리밍)",
    description="완료된 인터뷰 세션을 연장 모드로 전환하고 첫 질문을 SSE로 스트리밍합니다.",
    responses={
        200: {
            "description": "SSE 스트림",
            "content": {"text/event-stream": {}},
        },
        404: {"model": ErrorResponse, "description": "세션을 찾을 수 없음"},
    },
)
async def extend_session_stream(session_id: str):
    """완료된 세션의 연장 모드 시작 SSE"""
    service = get_interview_service()

    async def event_generator():
        stream = service.extend_session_stream(session_id=session_id)
        async for event in _interleave_ping_events(stream):
            yield event

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 프록시 버퍼링 비활성화
        },
    )


@router.get(
    "/sessions/{session_id}/status",
    response_model=SessionStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="세션 경량 상태 조회",
    description="현재 세션의 경량 상태를 조회합니다.",
    responses={404: {"model": ErrorResponse, "description": "세션을 찾을 수 없음"}},
)
async def get_session_status(session_id: str) -> SessionStatusResponse:
    """세션의 경량 상태를 조회한다."""

    service = get_interview_service()
    session_status = await service.get_session_status(session_id)

    if session_status is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"세션을 찾을 수 없습니다: {session_id}",
        )

    return SessionStatusResponse(**session_status)


@router.get(
    "/sessions/{session_id}/state",
    response_model=SessionStateResponse,
    status_code=status.HTTP_200_OK,
    summary="세션 상태 조회",
    description="현재 세션의 전체 상태를 조회합니다.",
    responses={404: {"model": ErrorResponse, "description": "세션을 찾을 수 없음"}},
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
        status=state["status"],
        turn_number=state["turn_number"],
        current_stage=state["current_stage"],
        stage_progress=StageProgressSchema(**state["stage_progress"]),
        overall_completion=state["overall_completion_percentage"],
        all_complete=state["all_stages_complete"],
        message_count=len(state["messages"]),
        is_extended_mode=state["is_extended_mode"],
        collected_data=collected_data,
        insight_turn_history=[
            InsightTurnRecordSchema(
                turn_number=record["turn_number"],
                user_message=record["user_message"],
                mentioned_insight=record.get("mentioned_insight"),
                insights=[InsightLogSchema(**insight) for insight in record["insights"]],
            )
            for record in state["insight_turn_history"]
        ],
        file_turn_history=[
            FileTurnRecordSchema(
                turn_number=record["turn_number"],
                files=[FileMetadataSchema(**file_meta) for file_meta in record["files"]],
            )
            for record in state["file_turn_history"]
        ],
        messages=messages,
    )


@router.post(
    "/sessions/{session_id}/chat/stream",
    status_code=status.HTTP_200_OK,
    summary="메시지 전송 (SSE 스트리밍)",
    description="사용자 메시지를 전송하고 AI 응답을 SSE 스트리밍으로 받습니다.",
    responses={
        200: {
            "description": "SSE 스트림",
            "content": {"text/event-stream": {}},
        },
        400: {"model": ErrorResponse, "description": "multipart 파일 검증 실패"},
        404: {"model": ErrorResponse, "description": "세션을 찾을 수 없음"},
    },
)
async def chat_stream(
    session_id: str,
    message: str = Form(..., min_length=1),
    mentioned_insight: str | None = Form(None),
    files: list[UploadFile] | None = File(None),
):
    """
    사용자 메시지 처리 및 AI 응답 SSE 스트리밍

    이벤트 타입:
    - content_block_delta: LLM 토큰 스트리밍
    - message_complete: 최종 결과 (전체 응답 + 진행 상황)
    - error: 에러 발생
    - ping: 연결 유지 (10초 간격)
    """

    service = get_interview_service()
    validated_files = await _read_and_validate_files(files)

    async def event_generator():
        """SSE 이벤트를 생성하는 비동기 제너레이터"""
        stream = service.process_message_stream(
            session_id=session_id,
            message=message,
            files=validated_files,
            mentioned_insight=mentioned_insight,
        )
        try:
            async for event in _interleave_ping_events(stream):
                yield event
        finally:
            _cleanup_temp_files(validated_files)

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 프록시 버퍼링 비활성화
        },
    )
