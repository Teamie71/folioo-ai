"""경험정리 서비스

API 계층과 그래프 사이를 잇는다. 요청 claim, 상태 저장, SSE 이벤트 순서를 담당한다.

**스트림을 열기 전에 실패할 수 있는 일을 모두 끝냅니다.** 파일 검증과 요청 claim은
`prepare_*` 에서 하고, `stream_*` 은 이미 확정된 요청만 실행합니다. SSE 를 연 뒤에
거절하면 프론트는 200 응답 안의 오류 이벤트를 따로 처리해야 합니다 (API 명세 2-3).
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from app.schemas.experience_map import (
    CommitResultEvent,
    CompletedMessage,
    ErrorEvent,
    ExperienceMapEvent,
    MessageCompleteEvent,
    ProcessingCompleteEvent,
    ProcessingStartedEvent,
    RequestErrorInfo,
    RequestStateResponse,
    SessionStateResponse,
    SuggestionInfo,
    SuggestionReadyEvent,
)
from features.experience_map.config import LEASE_RENEW_INTERVAL_SECONDS
from features.experience_map.errors import (
    ExperienceMapError,
    IdempotencyKeyReusedError,
    LeaseLostError,
    RequestNotFoundError,
    RetryExpiredError,
    RetryNotAllowedError,
    SessionBusyError,
    SessionNotFoundError,
)
from features.experience_map.graph_runner import GraphRunner, get_graph_runner
from features.experience_map.repository import (
    ClaimOutcome,
    ExperienceMapRepository,
    LeaseRenewer,
    RequestRow,
    get_repository,
)
from features.experience_map.state import ExperienceMapState, start_turn
from features.experience_map.upload_store import StoredFile

logger = logging.getLogger(__name__)

_STREAM_END = object()


def compute_request_hash(
    user_message: str | None,
    context_experience_id: str | None,
    view: str | None,
    file_hashes: list[str],
) -> str:
    """멱등성 판정용 요청 해시 (API 명세 2-5).

    같은 `request_id` 로 다른 입력이 오면 다른 해시가 나와야 한다. 파일은 내용
    SHA-256 을 쓰므로 이름만 바꾼 같은 파일은 같은 요청이다.
    """
    payload = json.dumps(
        {
            "message": (user_message or "").strip(),
            "context_experience_id": context_experience_id,
            "view": view,
            "files": sorted(file_hashes),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class PreparedRequest:
    """스트림을 열어도 되는 상태로 확정된 요청"""

    user_id: str
    session_id: str
    request_id: str
    request_hash: str
    user_message: str | None = None
    context_experience_id: str | None = None
    view: str | None = None
    stored_files: list[StoredFile] = field(default_factory=list)
    replay_of: RequestRow | None = None
    """저장된 결과를 재전송해야 하는 경우의 원본 요청"""

    is_retry: bool = False
    owner_token: str | None = None
    """이 요청의 실행권 표식. 상태를 바꿀 때마다 함께 보낸다."""

    @property
    def is_replay(self) -> bool:
        return self.replay_of is not None


class ExperienceMapService:
    """세션·요청 오케스트레이션"""

    def __init__(
        self,
        repository: ExperienceMapRepository | None = None,
        runner: GraphRunner | None = None,
        *,
        lease_renew_interval: int = LEASE_RENEW_INTERVAL_SECONDS,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._lease_renew_interval = lease_renew_interval
        """실행권 확인 주기. 실행권 상실을 알아채는 데 걸리는 시간의 상한이다."""

    @property
    def repository(self) -> ExperienceMapRepository:
        return self._repository or get_repository()

    @property
    def runner(self) -> GraphRunner:
        return self._runner or get_graph_runner()

    # ===== 세션 =====

    async def create_session(self, user_id: str) -> tuple[str, str]:
        """세션을 만들거나 기존 것을 돌려준다.

        Returns:
            tuple[str, str]: `(session_id, status)`
        """
        session = await self.repository.get_or_create_session(user_id)
        latest = await self.repository.get_latest_request(user_id, session.session_id)
        status = latest.status if latest and latest.status in {"running", "failed"} else "ready"
        return session.session_id, status

    async def get_session_state(self, user_id: str, session_id: str) -> SessionStateResponse:
        """화면 새로고침·재접속 시 조회한다.

        만료된 lease 는 조회 전에 정리한다. 정리하지 않으면 죽은 요청이 계속
        `running` 으로 보여 재시도 버튼이 나오지 않는다.
        """
        session = await self.repository.get_session(user_id, session_id)
        if session is None:
            raise SessionNotFoundError()

        await self.repository.expire_stale_running_requests(session_id=session_id)

        latest = await self.repository.get_latest_request(user_id, session_id)
        if latest is None:
            return SessionStateResponse(session_id=session_id, status="ready")

        return SessionStateResponse(
            session_id=session_id,
            status=latest.status if latest.status != "completed" else "ready",
            active_request_id=latest.request_id,
            retryable=latest.retryable,
            failed_node=latest.failed_node,
        )

    async def get_request_state(self, user_id: str, request_id: str) -> RequestStateResponse:
        """SSE 단절 뒤 저장 결과를 복구한다."""
        await self.repository.expire_stale_running_requests()

        row = await self.repository.get_request(user_id, request_id)
        if row is None:
            raise RequestNotFoundError()
        return _to_request_state(row)

    # ===== 요청 준비 (스트림 열기 전) =====

    async def prepare_chat(
        self,
        user_id: str,
        session_id: str,
        request_id: str,
        *,
        user_message: str | None,
        context_experience_id: str | None,
        view: str | None,
        stored_files: list[StoredFile],
    ) -> PreparedRequest:
        """요청을 claim 한다. 여기서 실패하면 SSE 를 열지 않는다.

        Raises:
            SessionNotFoundError: 세션이 없거나 소유자가 아님
            SessionBusyError: 실행 중인 요청이 있음
            IdempotencyKeyReusedError: 같은 `request_id` 에 다른 입력
            RetryNotAllowedError: 실패한 요청은 retry API 로 이어간다
        """
        if await self.repository.get_session(user_id, session_id) is None:
            raise SessionNotFoundError()

        request_hash = compute_request_hash(
            user_message, context_experience_id, view, [f.sha256 for f in stored_files]
        )
        claim = await self.repository.claim_request(
            user_id,
            session_id,
            request_id,
            request_hash,
            input_meta={
                "has_message": bool((user_message or "").strip()),
                "file_count": len(stored_files),
                "view": view,
            },
        )

        prepared = PreparedRequest(
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            request_hash=request_hash,
            user_message=user_message,
            context_experience_id=context_experience_id,
            view=view,
            stored_files=stored_files,
        )

        match claim.outcome:
            case ClaimOutcome.CLAIMED:
                prepared.owner_token = claim.request.owner_token
                return prepared
            case ClaimOutcome.REPLAY:
                prepared.replay_of = claim.request
                return prepared
            case ClaimOutcome.SESSION_BUSY:
                raise SessionBusyError()
            case ClaimOutcome.HASH_MISMATCH:
                raise IdempotencyKeyReusedError()
            case ClaimOutcome.RETRY_REQUIRED:
                raise RetryNotAllowedError("실패한 요청입니다. 재시도 API 를 사용해 주세요.")

        raise AssertionError(f"처리하지 않은 claim 결과: {claim.outcome}")

    async def prepare_retry(
        self, user_id: str, session_id: str, request_id: str
    ) -> PreparedRequest:
        """재시도를 허용할지 판정하고 요청을 `running` 으로 되돌린다.

        상태 전이는 `repository.retry_request()` 가 **하나의 UPDATE 로 원자적으로**
        처리한다. 여기서 조건을 미리 확인하고 따로 전이시키면 그 사이에 다른
        worker 가 끼어들 수 있다.

        Raises:
            RetryNotAllowedError: 마지막 요청이 아니거나 재시도 대상이 아님
            RetryExpiredError: 재시도 TTL 초과
            SessionBusyError: 세션에 실행 중인 요청이 있음
        """
        if await self.repository.get_session(user_id, session_id) is None:
            raise SessionNotFoundError()

        await self.repository.expire_stale_running_requests(session_id=session_id)

        # 마지막 요청만 재시도할 수 있다 (9절 19번). 정책 판정이라 전이와 분리한다 —
        # 그 사이에 새 요청이 시작되면 `_disable_previous_retries` 가 retryable 을
        # 내려 아래 UPDATE 가 0행이 되므로 경쟁에 안전하다.
        latest = await self.repository.get_latest_request(user_id, session_id)
        if latest is None:
            raise RequestNotFoundError()
        if latest.request_id != request_id:
            if await self.repository.get_request(user_id, request_id) is None:
                raise RequestNotFoundError()
            raise RetryNotAllowedError("세션의 마지막 요청만 재시도할 수 있습니다.")

        claim = await self.repository.retry_request(user_id, request_id)

        match claim.outcome:
            case ClaimOutcome.CLAIMED:
                return PreparedRequest(
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    request_hash=claim.request.request_hash,
                    is_retry=True,
                    owner_token=claim.request.owner_token,
                )
            case ClaimOutcome.REPLAY:
                # 이미 끝난 요청은 저장 결과를 그대로 돌려준다.
                return PreparedRequest(
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    request_hash=claim.request.request_hash,
                    replay_of=claim.request,
                    is_retry=True,
                )
            case ClaimOutcome.SESSION_BUSY:
                raise SessionBusyError()
            case ClaimOutcome.RETRY_EXPIRED:
                raise RetryExpiredError()
            case ClaimOutcome.RETRY_NOT_FOUND:
                raise RequestNotFoundError()

        raise RetryNotAllowedError()

    # ===== 스트림 =====

    async def stream(self, prepared: PreparedRequest) -> AsyncIterator[ExperienceMapEvent]:
        """요청을 실행하며 SSE 이벤트를 낸다.

        `processing_started` 로 열고 `processing_complete` 로 닫는다. 그 사이는
        그래프 실행기가 채운다.
        """
        yield ProcessingStartedEvent(request_id=prepared.request_id)

        if prepared.is_replay:
            async for event in self._replay(prepared.replay_of):
                yield event
            return

        async for event in self._execute(prepared):
            yield event

    async def _replay(self, row: RequestRow) -> AsyncIterator[ExperienceMapEvent]:
        """완료된 요청의 저장 결과를 같은 순서로 재전송한다 (API 명세 2-5)."""
        if row.result:
            yield CommitResultEvent(result=row.result)
            yield MessageCompleteEvent(
                message=CompletedMessage(
                    request_id=row.request_id,
                    session_id=row.session_id,
                    response_kind="result",
                    ai_response=(row.result.get("ai_response") or "정리를 완료했어요."),
                    committed=True,
                    map_version=row.result.get("map_version"),
                    can_revert=bool(row.result.get("can_revert")),
                )
            )

        if row.suggestion:
            yield SuggestionReadyEvent(gap=row.suggestion.get("gap"))
            yield MessageCompleteEvent(
                message=CompletedMessage(
                    request_id=row.request_id,
                    session_id=row.session_id,
                    response_kind="suggestion",
                    ai_response=row.suggestion.get("message", ""),
                    committed=False,
                )
            )

        yield ProcessingCompleteEvent(request_id=row.request_id, status="completed")

    async def _execute(self, prepared: PreparedRequest) -> AsyncIterator[ExperienceMapEvent]:
        """그래프를 돌리고 결과를 저장한다."""
        state = _build_state(prepared)
        result_payload: dict[str, Any] | None = None
        suggestion_payload: dict[str, Any] | None = None

        try:
            async with LeaseRenewer(
                self.repository,
                prepared.user_id,
                prepared.request_id,
                owner_token=prepared.owner_token,
                interval_seconds=self._lease_renew_interval,
            ) as renewer:
                run = self.runner.resume(state) if prepared.is_retry else self.runner.run(state)
                async for event in _interrupt_when_lease_lost(run, renewer):
                    if isinstance(event, CommitResultEvent):
                        result_payload = event.result.model_dump()
                    elif isinstance(event, SuggestionReadyEvent):
                        suggestion_payload = {"gap": event.gap.model_dump() if event.gap else None}
                    elif isinstance(event, MessageCompleteEvent):
                        if event.message.response_kind == "result" and result_payload:
                            result_payload["ai_response"] = event.message.ai_response
                        elif event.message.response_kind == "suggestion":
                            suggestion_payload = {
                                **(suggestion_payload or {}),
                                "message": event.message.ai_response,
                            }

                    yield event

        except LeaseLostError as exc:
            # 우리가 더 이상 주인이 아니다. **DB 를 건드리지 않는다.**
            logger.warning("실행권을 잃어 중단합니다 (request_id=%s)", prepared.request_id)
            yield ErrorEvent(error=exc.to_sse_error().model_dump())
            return
        except ExperienceMapError as exc:
            logger.warning("경험정리 요청 실패 (request_id=%s)", prepared.request_id)
            yield await self._fail(prepared, exc)
            return
        except Exception:
            logger.exception("경험정리 요청 처리 중 예외 (request_id=%s)", prepared.request_id)
            from features.experience_map.errors import LlmError

            yield await self._fail(prepared, LlmError())
            return

        completed = await self.repository.mark_request_completed(
            prepared.user_id,
            prepared.request_id,
            result=result_payload,
            suggestion=suggestion_payload,
            committed_version=(result_payload or {}).get("map_version"),
            owner_token=prepared.owner_token,
        )
        if completed is None:
            # 저장에 실패했다. 다른 worker 가 이미 이 요청의 주인이므로 완료를
            # 주장하지 않는다.
            yield ErrorEvent(error=LeaseLostError().to_sse_error().model_dump())
            return

        yield ProcessingCompleteEvent(request_id=prepared.request_id, status="completed")

    async def _fail(self, prepared: PreparedRequest, exc: ExperienceMapError) -> ErrorEvent:
        """실패를 저장하고 error 이벤트를 만든다.

        저장이 0행이어도 오류 이벤트는 보낸다. 사용자는 자기 스트림이 실패했다는
        사실을 알아야 하고, 상태는 이미 다른 경로가 정리했다.
        """
        payload = exc.to_sse_error()
        await self.repository.mark_request_failed(
            prepared.user_id,
            prepared.request_id,
            error=payload.model_dump(),
            failed_node=payload.failed_node,
            retryable=payload.retryable,
            owner_token=prepared.owner_token,
        )
        return ErrorEvent(error=payload.model_dump())


async def _interrupt_when_lease_lost(
    events: AsyncIterator[ExperienceMapEvent], renewer: LeaseRenewer
) -> AsyncIterator[ExperienceMapEvent]:
    """lease 를 잃는 즉시 실행을 끊는다.

    이벤트가 도착할 때만 확인하면 조용한 구간에서 lease 를 잃어도 그 구간이
    끝날 때까지 모른다. 파일처리 120초·LLM 60초처럼 긴 침묵이 정상인 구조라
    실제로 벌어진다.

    Raises:
        LeaseLostError: 실행 중 실행권을 잃음
    """
    iterator = aiter(events)
    lost_wait = asyncio.create_task(renewer.lost.wait())

    try:
        while True:
            if renewer.lost.is_set():
                raise LeaseLostError()

            next_event = asyncio.create_task(anext(iterator, _STREAM_END))
            done, _ = await asyncio.wait(
                {next_event, lost_wait}, return_when=asyncio.FIRST_COMPLETED
            )

            if next_event not in done:
                next_event.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await next_event
                raise LeaseLostError()

            event = next_event.result()
            if event is _STREAM_END:
                return
            yield event
    finally:
        lost_wait.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await lost_wait
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            with suppress(Exception):
                await aclose()


def _build_state(prepared: PreparedRequest) -> ExperienceMapState:
    """그래프에 넘길 초기 state"""
    state = start_turn(
        {"user_id": prepared.user_id, "session_id": prepared.session_id},
        request_id=prepared.request_id,
        request_hash=prepared.request_hash,
        user_message=prepared.user_message,
        context_experience_id=prepared.context_experience_id,
        view=prepared.view,
    )
    state["file_references"] = [f.as_reference() for f in prepared.stored_files]
    return state


def _to_request_state(row: RequestRow) -> RequestStateResponse:
    suggestion = None
    if row.suggestion:
        suggestion = SuggestionInfo(
            gap=row.suggestion.get("gap"), message=row.suggestion.get("message", "")
        )

    error = None
    if row.error:
        error = RequestErrorInfo(
            code=row.error.get("code", "internal_error"),
            failed_node=row.error.get("failed_node") or row.failed_node,
            retryable=row.retryable,
            message=row.error.get("message", ""),
        )

    return RequestStateResponse(
        request_id=row.request_id,
        status=row.status,
        result=row.result,
        suggestion=suggestion,
        error=error,
    )


_service: ExperienceMapService | None = None


def get_service() -> ExperienceMapService:
    """서비스 싱글톤 반환"""
    global _service
    if _service is None:
        _service = ExperienceMapService()
    return _service


def set_service(service: ExperienceMapService | None) -> None:
    """서비스 주입 (테스트용)"""
    global _service
    _service = service
