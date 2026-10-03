"""카카오톡 턴 서비스

접수(202) → 백그라운드 실행 → 최종 콜백 1회 시도 → 한도 정리·완료 통지 순서로 돈다.
모든 상태는 `ai_kakao_turn` 에 남기므로 재시작 뒤에도 `recovery_loop` 가 이어받는다.

시간 계약 (명세 1절): 메인 서버가 수신 시각 +50초를 `expires_at` 으로 준다. 이 서비스는
45초(= `expires_at` - 5초)에 새 작업을 멈추고, 50초 전에 콜백을 시도한다. 기한이 지난 뒤에는
실패 답변도 만들기 시작하지 않는다 (`NOT_ATTEMPTED`).
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from app.schemas.experience_map import ErrorEvent, MessageCompleteEvent, NodeStatusEvent
from features.experience_map.errors import (
    ExperienceMapError,
    InvalidRequestError,
    SessionBusyError,
    SessionNotFoundError,
)
from features.experience_map.kakao import callback as kakao_callback
from features.experience_map.kakao.config import (
    RECOVERY_INTERVAL_SECONDS,
    RUN_BUDGET_SECONDS,
    SESSION_WAIT_POLL_SECONDS,
    WORKER_LEASE_SECONDS,
    get_web_experience_url,
)
from features.experience_map.kakao.main_notify import KakaoMainNotifier
from features.experience_map.kakao.schemas import KakaoTurnRequest, KakaoTurnStatusResponse
from features.experience_map.kakao.store import (
    AcceptOutcome,
    KakaoTurnStore,
    TurnRow,
    get_store,
)
from features.experience_map.main_client import ExperienceMapMainClient
from features.experience_map.service import ExperienceMapService, get_service

logger = logging.getLogger(__name__)

_RUNNING: set[asyncio.Task[None]] = set()
"""실행 중인 worker task. event loop 가 약한 참조만 들고 있어 따로 잡아 둔다."""

CallbackSender = Callable[[str, dict[str, Any]], Awaitable[str]]


class KakaoNotConfiguredError(ExperienceMapError):
    """카톡 답변에 필요한 설정이 없음. 메인 서버가 확정 거절로 보고 보상하도록 4xx 로 응답한다."""

    status_code = 400
    code = "kakao_not_configured"
    message = "카카오톡 대화 설정이 완료되지 않았습니다."


def compute_request_hash(session_id: str, request: KakaoTurnRequest) -> str:
    """멱등성 판정용 해시. 세션과 모든 본문 필드를 포함한다. 비교 오류에 본문을 싣지 않는다."""
    payload = json.dumps(
        {
            "session_id": session_id,
            "user_id": request.user_id,
            "block_id": request.block_id,
            "utterance": request.utterance,
            "callback_url": request.callback_url,
            "expires_at": request.expires_at.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


class KakaoTurnService:
    """카톡 턴 접수·실행·정리"""

    def __init__(
        self,
        *,
        store: KakaoTurnStore | None = None,
        experience_service: ExperienceMapService | None = None,
        notifier: KakaoMainNotifier | None = None,
        main_client: ExperienceMapMainClient | None = None,
        send_callback: CallbackSender | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._store = store
        self._experience = experience_service
        self._notifier = notifier or KakaoMainNotifier()
        self._main_client = main_client
        self._send = send_callback or kakao_callback.send_callback
        self._now = now

    @property
    def store(self) -> KakaoTurnStore:
        return self._store or get_store()

    @property
    def experience(self) -> ExperienceMapService:
        return self._experience or get_service()

    @property
    def main_client(self) -> ExperienceMapMainClient:
        return self._main_client or self.experience.main_client

    # ===== 접수·조회 =====

    async def accept(self, session_id: str, request: KakaoTurnRequest) -> TurnRow:
        """검증 뒤 접수를 영속화하고 실행을 시작한다. 같은 본문 재접수는 추가 작업이 없다.

        Raises:
            InvalidRequestError: 콜백 URL·세션 소속 검증 실패 (작업 생성 이전 거부)
            SessionNotFoundError: 세션이 없거나 사용자가 다름
            IdempotencyKeyReusedError: 같은 request_id 에 다른 본문·사용자·세션
        """
        from features.experience_map.errors import IdempotencyKeyReusedError

        try:
            kakao_callback.validate_callback_url(request.callback_url)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        try:
            # 미설정이면 확정 답변을 못 만든다. 5xx 는 메인 서버가 '접수 불명'으로 보고 잠금·한도를
            # 붙잡아 두므로, 작업을 만들기 전에 명시적으로 거절해 바로 보상받게 한다.
            get_web_experience_url()
        except ValueError as exc:
            logger.error("카톡 턴 접수 거절 - %s", exc)
            raise KakaoNotConfiguredError() from exc

        session = await self.experience.repository.get_session(request.user_id, session_id)
        if session is None:
            raise SessionNotFoundError()
        if session.block_id != request.block_id:
            raise InvalidRequestError("block_id 가 세션의 활동과 일치하지 않습니다.")

        outcome, row = await self.store.accept(
            request_id=request.request_id,
            user_id=request.user_id,
            session_id=session_id,
            block_id=request.block_id,
            request_hash=compute_request_hash(session_id, request),
            utterance=request.utterance,
            callback_url=request.callback_url,
            expires_at=request.expires_at,
        )
        if outcome is AcceptOutcome.MISMATCH:
            raise IdempotencyKeyReusedError()
        if outcome is AcceptOutcome.CREATED:
            self.start(row.request_id)
        return row

    async def get_status(self, request_id: str, user_id: str) -> KakaoTurnStatusResponse | None:
        """사용자와 request_id 가 일치할 때만 상태를 돌려준다. 없으면 `None`(404)."""
        row = await self.store.get(request_id, user_id)
        if row is None:
            return None
        return KakaoTurnStatusResponse(
            request_id=row.request_id,
            state=row.state,  # type: ignore[arg-type]
            delivery_status=row.delivery_status,  # type: ignore[arg-type]
        )

    def start(self, request_id: str) -> None:
        """worker 를 띄운다. 실행권은 DB 가 가리므로 여러 번 불러도 한 곳만 돈다."""
        task = asyncio.create_task(self.run(request_id))
        _RUNNING.add(task)
        task.add_done_callback(_RUNNING.discard)

    # ===== 실행 =====

    async def run(self, request_id: str) -> None:
        """턴 하나를 이어서 처리한다. 예외는 삼키고 복구 루프에 맡긴다."""
        row = await self.store.claim_worker(request_id)
        if row is None or row.worker_token is None:
            return
        token = row.worker_token
        lease = asyncio.create_task(self._keep_lease(request_id, token))
        try:
            await self._process(row, token)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("카톡 턴 처리 중 예외 (request_id=%s)", request_id)
        finally:
            lease.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await lease
            with suppress(Exception):
                await self.store.release_worker(request_id, token)

    async def _keep_lease(self, request_id: str, token: str) -> None:
        interval = max(1, WORKER_LEASE_SECONDS // 3)
        while True:
            await asyncio.sleep(interval)
            try:
                if not await self.store.renew_worker(request_id, token):
                    return
            except Exception:
                logger.exception("카톡 worker 실행권 연장 실패 (request_id=%s)", request_id)

    async def _process(self, row: TurnRow, token: str) -> None:
        if row.outcome == "COMMIT_UNKNOWN" and row.complete_notified:
            await self._resolve_commit_unknown(row, token)
            row = await self.store.get(row.request_id) or row
        elif row.outcome is None:
            outcome, payload = await self._execute_turn(row)
            await self.store.record_outcome(
                row.request_id, token, outcome=outcome, reply_payload=payload
            )
            row = await self.store.get(row.request_id) or row

        if row.delivery_claimed_at is None or row.callback_url is not None:
            await self._deliver(row)
            row = await self.store.get(row.request_id) or row
        await self._finalize(row)

    def _deadline(self, row: TurnRow) -> datetime:
        """새 작업을 멈추기 시작하는 시각 (`expires_at` - 5초 = 수신 후 45초)"""
        return row.expires_at - timedelta(seconds=max(0, 50 - RUN_BUDGET_SECONDS))

    async def _execute_turn(self, row: TurnRow) -> tuple[str, dict[str, Any] | None]:
        """대화 턴을 실행하고 `(outcome, 콜백 본문)` 을 만든다.

        outcome 은 내부 값이다: SUCCEEDED / FAILED / EXPIRED / COMMIT_UNKNOWN.
        """
        web_url = get_web_experience_url()
        failure = kakao_callback.build_failure_payload(web_url)

        remaining = (self._deadline(row) - self._now()).total_seconds()
        if remaining <= 0:
            # 기한 뒤에는 새 실행을 시작하지 않는다. 다만 죽은 worker 가 이미 커밋했을 수
            # 있으므로(재시작 복구) 커밋 여부는 확인한 뒤 확정한다.
            return await self._after_unsuccessful(row, "EXPIRED", web_url)

        prepared = None
        texts: list[str] = []
        error_code: str | None = None
        commit_in_flight = False
        try:
            async with asyncio.timeout(remaining):
                prepared = await self._claim_session(row)
                async for event in self.experience.execute(prepared):
                    if isinstance(event, NodeStatusEvent) and event.node == "commit":
                        commit_in_flight = event.status == "running"
                    elif isinstance(event, MessageCompleteEvent):
                        texts.append(event.message.ai_response)
                    elif isinstance(event, ErrorEvent):
                        error_code = event.error.code
        except TimeoutError:
            logger.warning("카톡 턴 실행 기한 초과 (request_id=%s)", row.request_id)
            await self._release_request(row, prepared)
            if commit_in_flight:
                # 커밋 호출은 shield 되어 취소 뒤에도 계속 돈다. 지금 조회하면 아직 반영 전이라
                # committed=false 가 나올 수 있어 환불하면 안 된다. 복구 루프가 나중에 확정한다.
                return "COMMIT_UNKNOWN", kakao_callback.build_unknown_payload(web_url)
            return await self._after_unsuccessful(row, "EXPIRED", web_url)
        except ExperienceMapError as exc:
            logger.warning("카톡 턴 접수 실패 (request_id=%s, code=%s)", row.request_id, exc.code)
            return "FAILED", failure
        except Exception:
            logger.exception("카톡 턴 실행 예외 (request_id=%s)", row.request_id)
            return await self._after_unsuccessful(row, "FAILED", web_url)

        if error_code is not None:
            logger.warning("카톡 턴 실패 (request_id=%s, code=%s)", row.request_id, error_code)
            return await self._after_unsuccessful(row, "FAILED", web_url)

        summary = kakao_callback.build_summary(*texts)
        return "SUCCEEDED", kakao_callback.build_success_payload(summary, web_url)

    async def _claim_session(self, row: TurnRow):
        """같은 세션의 웹·카톡 턴이 끝나길 기다렸다가 요청을 잡는다 (직렬화)."""
        while True:
            try:
                return await self.experience.prepare_chat(
                    row.user_id,
                    row.session_id,
                    row.request_id,
                    user_message=row.utterance,
                    context_experience_id=None,
                    view=None,
                    stored_files=[],
                    channel="KAKAO",
                )
            except SessionBusyError:
                await asyncio.sleep(SESSION_WAIT_POLL_SECONDS)

    async def _release_request(self, row: TurnRow, prepared) -> None:
        """기한으로 끊은 요청이 세션을 계속 잡고 있지 않게 실패로 마감한다."""
        if prepared is None or prepared.owner_token is None or prepared.is_replay:
            return
        with suppress(Exception):
            await self.experience.repository.mark_request_failed(
                row.user_id,
                row.request_id,
                error={"code": "kakao_deadline", "message": "실행 기한을 넘겨 중단했습니다."},
                retryable=False,
                owner_token=prepared.owner_token,
            )

    async def _after_unsuccessful(
        self, row: TurnRow, outcome: str, web_url: str
    ) -> tuple[str, dict[str, Any] | None]:
        """실패·기한 초과 뒤 **커밋이 실제로 없었는지** 확인해 결과를 확정한다.

        커밋됐다면 환불하면 안 된다. 확인조차 못 하면 성공·실패를 추정하지 않는다.
        """
        committed = await self._confirm_commit(row.request_id)
        if committed is True:
            summary = kakao_callback.build_summary()
            return "SUCCEEDED", kakao_callback.build_success_payload(summary, web_url)
        if committed is None:
            return "COMMIT_UNKNOWN", kakao_callback.build_unknown_payload(web_url)
        return outcome, kakao_callback.build_failure_payload(web_url)

    async def _confirm_commit(self, request_id: str) -> bool | None:
        """`True` 커밋됨 / `False` 커밋 없음 확정 / `None` 확인 불가"""
        try:
            recovery = await self.main_client.get_commit(request_id)
        except Exception:
            logger.warning("커밋 결과 확인 실패 (request_id=%s)", request_id)
            return None
        return recovery.committed

    async def _resolve_commit_unknown(self, row: TurnRow, token: str) -> None:
        """COMMIT_UNKNOWN 의 결과를 조회해 확정한다. 계속 불명이면 그대로 둔다."""
        committed = await self._confirm_commit(row.request_id)
        if committed is None:
            return
        await self.store.resolve_commit_unknown(
            row.request_id, token, "SUCCEEDED" if committed else "FAILED"
        )

    # ===== 전달 =====

    async def _deliver(self, row: TurnRow) -> None:
        """최종 콜백을 **한 번만** 시도한다. 시도 전·후 상태를 항상 남긴다."""
        request_id = row.request_id
        if row.delivery_claimed_at is not None and row.callback_url is not None:
            # 이전 worker 가 전송 소유권을 잡고 죽었다. 도달 여부를 알 수 없고 URL 은 1회용이다.
            await self.store.finish_delivery(request_id, "UNKNOWN")
            return

        remaining = (row.expires_at - self._now()).total_seconds()
        if remaining <= 0 or not row.reply_payload:
            await self.store.finish_delivery(request_id, "NOT_ATTEMPTED")
            return

        url = await self.store.claim_delivery(request_id)
        if url is None:
            return  # 이미 다른 쪽이 전송 소유권을 가졌다.

        status = "UNKNOWN"
        try:
            status = await self._send_with_budget(url, row.reply_payload, remaining)
        except Exception as exc:
            logger.warning("카톡 콜백 예외 (%s)", type(exc).__name__)  # URL 은 남기지 않는다.
        finally:
            # 예외가 나도 URL 을 비우고 결과를 남겨 정리 단계가 이어지게 한다.
            await self.store.finish_delivery(request_id, status)

    async def _send_with_budget(self, url: str, payload: dict[str, Any], remaining: float) -> str:
        return await asyncio.wait_for(self._send(url, payload), timeout=max(0.5, remaining))

    # ===== 정리 =====

    async def _finalize(self, row: TurnRow) -> None:
        """공개 상태를 확정하고, 한도 복구·완료 통지를 시도한다 (영속 재시도 대상)."""
        outcome = row.outcome
        if outcome is None:
            return
        request_id = row.request_id

        if row.delivery_claimed_at is not None and row.callback_url is None:
            await self.store.publish_state(request_id, outcome)
        row = await self.store.get(request_id) or row

        if outcome in {"FAILED", "EXPIRED"} and not row.usage_reported:
            if await self._notifier.report_failed_usage(user_id=row.user_id, request_id=request_id):
                await self.store.mark_usage_reported(request_id)

        if row.complete_notified:
            return
        main_outcome = "FAILED" if outcome == "EXPIRED" else outcome
        if await self._notifier.notify_turn_complete(
            user_id=row.user_id,
            request_id=request_id,
            outcome=main_outcome,
            delivery_status=row.delivery_status,
        ):
            await self.store.mark_complete_notified(request_id)

    # ===== 복구 =====

    async def recover_once(self) -> int:
        """이어받을 턴을 한 번 훑어 처리한다. 처리한 개수를 돌려준다."""
        ids = await self.store.list_recoverable()
        for request_id in ids:
            await self.run(request_id)
        return len(ids)

    async def recovery_loop(self, interval: float = RECOVERY_INTERVAL_SECONDS) -> None:
        """죽은 worker 의 턴과 남은 정리를 주기적으로 이어받는다. 취소될 때까지 돈다."""
        purge_every = max(1, int(3600 // max(interval, 1)))
        tick = 0
        while True:
            try:
                await self.recover_once()
                tick += 1
                if tick % purge_every == 0:
                    await self.store.purge_old()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("카톡 턴 복구 루프 예외")
            await asyncio.sleep(interval)


_service: KakaoTurnService | None = None


def get_kakao_service() -> KakaoTurnService:
    global _service
    if _service is None:
        _service = KakaoTurnService()
    return _service


def set_kakao_service(service: KakaoTurnService | None) -> None:
    """서비스 주입 (테스트용)"""
    global _service
    _service = service
