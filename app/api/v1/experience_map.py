"""경험정리 API

인증이 경로마다 다릅니다 (API 명세 2-1).

| 경로 | 호출자 | 인증 |
| --- | --- | --- |
| `POST /sessions` | 메인 서버 | `X-API-Key` (`ApiKeyAuthMiddleware`) |
| 나머지 | 프론트 | `Bearer {ticket}` (`ExperienceMapTicketMiddleware`) |

티켓 검증은 미들웨어가 **요청 body 를 읽기 전에** 끝냅니다. 여기서는 검증된
`user_id` 를 scope 에서 꺼내 쓰기만 합니다.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from app.schemas.experience_map import (
    ChatStreamRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    ErrorEvent,
    ExperienceMapEvent,
    PingEvent,
    RequestStateResponse,
    RetryStreamRequest,
    SessionStateResponse,
)
from features.experience_map.config import SSE_HEARTBEAT_INTERVAL_SECONDS
from features.experience_map.errors import (
    ExperienceMapError,
    InvalidRequestError,
    SessionForbiddenError,
    StreamError,
    TicketInvalidError,
)
from features.experience_map.service import ExperienceMapService, PreparedRequest, get_service
from features.experience_map.upload_store import StoredFile, get_upload_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/experience-map", tags=["experience-map"])

_STREAM_END = object()


# ===== 공통 =====


def _ticket_user_id(request: Request) -> str:
    """티켓 미들웨어가 검증한 `user_id`

    미들웨어를 거치지 않았다면 라우터 등록이 잘못된 것이다. 인증 없이 통과시키지
    않는다.
    """
    user_id = request.scope.get("state", {}).get("experience_map_user_id")
    if not user_id:
        logger.error("티켓 검증 없이 경험정리 API 에 도달했습니다: %s", request.url.path)
        raise TicketInvalidError()
    return user_id


def _require_session_owner(request: Request, session_id: str) -> None:
    """경로 `session_id` 가 티켓의 `sid` 와 같은지 재확인한다.

    미들웨어가 이미 검사하지만, 라우터가 바뀌어도 깨지지 않도록 여기서도 본다.
    """
    ticket_sid = request.scope.get("state", {}).get("experience_map_session_id")
    if ticket_sid and ticket_sid != session_id:
        raise SessionForbiddenError()


def _error_response(exc: ExperienceMapError) -> JSONResponse:
    """스트림 시작 전 오류는 JSON 이다 (API 명세 2-3)."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_response().model_dump())


def _sse(event: ExperienceMapEvent) -> ServerSentEvent:
    payload = event.model_dump()
    return ServerSentEvent(event=payload["type"], data=json.dumps(payload, ensure_ascii=False))


def _stream_error_event() -> ServerSentEvent:
    """SSE 어댑터 자체의 예기치 못한 실패를 프론트에 알린다."""
    return _sse(ErrorEvent(error=StreamError().to_sse_error().model_dump()))


async def _with_heartbeat(
    events: AsyncIterator[ExperienceMapEvent],
) -> AsyncIterator[ServerSentEvent]:
    """이벤트가 없는 동안 10초마다 `ping` 을 보낸다.

    파일처리 120초, LLM 60초처럼 조용한 구간이 길어 프록시가 연결을 끊을 수 있다.
    """
    iterator = aiter(events)
    pending = asyncio.create_task(anext(iterator, _STREAM_END))

    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=SSE_HEARTBEAT_INTERVAL_SECONDS)

            if not done:
                yield _sse(PingEvent())
                continue

            try:
                event = pending.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("경험정리 SSE 스트림 처리 중 예외")
                yield _stream_error_event()
                break

            if event is _STREAM_END:
                break

            try:
                serialized = _sse(event)
            except Exception:
                logger.exception("경험정리 SSE 이벤트 직렬화 중 예외")
                yield _stream_error_event()
                break
            yield serialized
            pending = asyncio.create_task(anext(iterator, _STREAM_END))
    finally:
        pending.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await pending
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            with suppress(Exception):
                await aclose()


def _stream_response(
    service: ExperienceMapService, prepared: PreparedRequest
) -> EventSourceResponse:
    return EventSourceResponse(
        _with_heartbeat(service.stream(prepared)),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _collect_uploads(
    user_id: str, request_id: str, files: list[UploadFile]
) -> list[StoredFile]:
    """업로드를 검증하고 저장한다. **스트림을 열기 전에** 호출한다."""
    if not files:
        return []

    store = get_upload_store()
    try:
        return await store.store_files(
            user_id,
            request_id,
            [(f.filename or "", f.content_type or "", f) for f in files],
        )
    finally:
        for file in files:
            with suppress(Exception):
                await file.close()


# ===== 엔드포인트 =====


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    summary="세션 생성",
    description="메인 서버가 티켓 발급 과정에서 호출합니다. X-API-Key 인증입니다.",
)
async def create_session(payload: CreateSessionRequest):
    service = get_service()
    try:
        session_id, session_status = await service.create_session(payload.user_id)
    except ExperienceMapError as exc:
        return _error_response(exc)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=CreateSessionResponse(session_id=session_id, status=session_status).model_dump(),
    )


@router.get(
    "/sessions/{session_id}/state",
    response_model=SessionStateResponse,
    summary="세션 상태 조회",
    description="화면 새로고침·재접속 시 호출합니다.",
)
async def get_session_state(request: Request, session_id: str):
    try:
        user_id = _ticket_user_id(request)
        _require_session_owner(request, session_id)
        state = await get_service().get_session_state(user_id, session_id)
    except ExperienceMapError as exc:
        return _error_response(exc)
    return state


@router.get(
    "/sessions/{session_id}/requests/{request_id}",
    response_model=RequestStateResponse,
    summary="요청 결과 조회",
    description="SSE 단절 뒤 저장된 결과를 복구합니다.",
)
async def get_request_state(request: Request, session_id: str, request_id: str):
    try:
        user_id = _ticket_user_id(request)
        _require_session_owner(request, session_id)
        return await get_service().get_request_state(user_id, request_id)
    except ExperienceMapError as exc:
        return _error_response(exc)


@router.post(
    "/sessions/{session_id}/chat/stream",
    summary="채팅 스트림",
    description="SSE 로 처리 과정과 결과를 보냅니다. multipart/form-data 입니다.",
)
async def chat_stream(
    request: Request,
    session_id: str,
    request_body: str = Form(..., alias="request"),
    files: list[UploadFile] = File(default=[]),
):
    stored: list[StoredFile] = []
    try:
        user_id = _ticket_user_id(request)
        _require_session_owner(request, session_id)

        try:
            payload = ChatStreamRequest.model_validate_json(request_body)
        except ValidationError as exc:
            raise InvalidRequestError(_first_error(exc)) from exc

        try:
            payload.require_message_or_files(len(files))
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc

        # 업로드 검증은 스트림을 열기 전에 끝낸다.
        stored = await _collect_uploads(user_id, payload.request_id, files)

        service = get_service()
        prepared = await service.prepare_chat(
            user_id,
            session_id,
            payload.request_id,
            user_message=payload.user_message,
            context_experience_id=payload.context_experience_id,
            view=payload.view,
            stored_files=stored,
        )
    except ExperienceMapError as exc:
        # 이번 요청을 쓰지 않게 됐으므로 방금 올린 object 를 지운다.
        await _discard_uploads(stored, session_id)
        return _error_response(exc)

    if prepared.is_replay and stored:
        # 저장 결과 재전송이면 방금 올린 파일은 필요 없다.
        await _discard_uploads(stored, session_id)

    return _stream_response(service, prepared)


@router.post(
    "/sessions/{session_id}/retry/stream",
    summary="재시도 스트림",
    description="세션의 마지막 실패 요청을 실패 지점부터 재실행합니다.",
)
async def retry_stream(request: Request, session_id: str, payload: RetryStreamRequest):
    try:
        user_id = _ticket_user_id(request)
        _require_session_owner(request, session_id)
        service = get_service()
        prepared = await service.prepare_retry(user_id, session_id, payload.request_id)
    except ExperienceMapError as exc:
        return _error_response(exc)

    return _stream_response(service, prepared)


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "요청 형식이 올바르지 않습니다."
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', '')}".strip(": ")


async def _discard_uploads(stored: list[StoredFile], session_id: str) -> None:
    if not stored:
        return
    with suppress(Exception):
        store = get_upload_store()
        for item in stored:
            await store.delete_after_extraction(item)


__all__ = ["router"]
