"""카톡 턴 접수·실행·정리 테스트 (실제 PostgreSQL + 외부 호출 대역)"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.schemas.experience_map import (
    CompletedMessage,
    ErrorEvent,
    ErrorEventPayload,
    MessageCompleteEvent,
)
from features.experience_map.errors import (
    IdempotencyKeyReusedError,
    InvalidRequestError,
    SessionBusyError,
    SessionNotFoundError,
)
from features.experience_map.kakao import service as kakao_service
from features.experience_map.kakao.schemas import KakaoTurnRequest
from features.experience_map.kakao.service import KakaoTurnService
from features.experience_map.kakao.store import KakaoTurnStore
from features.experience_map.main_client import CommitRecoveryResult
from features.experience_map.repository import RequestRow, SessionRow
from features.experience_map.service import ExperienceMapService, PreparedRequest

CALLBACK_URL = "https://bot-api.kakao.com/v1/callback/secret-token"
WEB_URL = "https://folioo.example/experience-map"
SESSION_ID = "550e8400-e29b-41d4-a716-446655440010"


@pytest.fixture(autouse=True)
def _kakao_env(monkeypatch):
    monkeypatch.setenv("KAKAO_WEB_EXPERIENCE_URL", WEB_URL)
    monkeypatch.setattr(kakao_service, "SESSION_WAIT_POLL_SECONDS", 0.01)


# ===== 대역 =====


class FakeRepository:
    def __init__(self, user_id: str, block_id: str = "12") -> None:
        self._session = SessionRow(
            user_id=user_id, session_id=SESSION_ID, block_id=block_id, active_gap=None
        )
        self.failed: list[dict] = []

    async def get_session(self, user_id, session_id):
        if user_id == self._session.user_id and session_id == SESSION_ID:
            return self._session
        return None

    async def mark_request_failed(self, user_id, request_id, **kwargs):
        self.failed.append({"request_id": request_id, **kwargs})


class FakeExperience:
    """`prepare_chat`·`execute` 만 흉내 낸다."""

    def __init__(self, user_id: str, *, events=None, busy_times: int = 0, hang: bool = False):
        self.repository = FakeRepository(user_id)
        self.events = events or []
        self.busy_times = busy_times
        self.hang = hang
        self.prepare_calls: list[dict] = []
        self.execute_calls = 0
        self.main_client = None

    async def prepare_chat(self, user_id, session_id, request_id, **kwargs):
        self.prepare_calls.append({"request_id": request_id, **kwargs})
        if self.busy_times > 0:
            self.busy_times -= 1
            raise SessionBusyError()
        return PreparedRequest(
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            request_hash="h" * 64,
            owner_token=str(uuid.uuid4()),
            channel=kwargs.get("channel", "WEB"),
        )

    async def execute(self, prepared):
        self.execute_calls += 1
        if self.hang:
            await asyncio.sleep(60)
        for event in self.events:
            yield event


class FakeMainClient:
    """`get_commit` 결과를 고정한다. 예외면 확인 불가."""

    def __init__(self, result: bool | Exception = False) -> None:
        self.result = result
        self.calls = 0

    async def get_commit(self, request_id):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return CommitRecoveryResult(committed=self.result)


class FakeNotifier:
    def __init__(self, journal: list[str]) -> None:
        self.journal = journal
        self.usage_ok = True
        self.complete_ok = True
        self.usage_calls = 0
        self.complete_calls: list[dict] = []

    async def report_failed_usage(self, *, user_id, request_id):
        self.usage_calls += 1
        self.journal.append("usage")
        return self.usage_ok

    async def notify_turn_complete(self, *, user_id, request_id, outcome, delivery_status):
        self.complete_calls.append({"outcome": outcome, "delivery_status": delivery_status})
        self.journal.append("complete")
        return self.complete_ok


class Harness:
    def __init__(self, clean_db, user_id: str, **experience_kwargs) -> None:
        self.user_id = user_id
        self.store = KakaoTurnStore(clean_db)
        self.journal: list[str] = []
        self.sent: list[dict] = []
        self.send_result: str | Exception = "SUCCESS"
        self.experience = FakeExperience(user_id, **experience_kwargs)
        self.main_client = FakeMainClient(False)
        self.notifier = FakeNotifier(self.journal)
        self.service = KakaoTurnService(
            store=self.store,
            experience_service=self.experience,
            notifier=self.notifier,
            main_client=self.main_client,
            send_callback=self._send,
        )

    async def _send(self, url, payload):
        self.journal.append("callback")
        self.sent.append({"url": url, "payload": payload})
        if isinstance(self.send_result, Exception):
            raise self.send_result
        return self.send_result

    def request(self, *, expires_in: float = 50, **overrides) -> KakaoTurnRequest:
        data = {
            "user_id": self.user_id,
            "block_id": "12",
            "request_id": str(uuid.uuid4()),
            "utterance": "동아리에서 축제 부스 운영했어요",
            "callback_url": CALLBACK_URL,
            "expires_at": datetime.now(UTC) + timedelta(seconds=expires_in),
        }
        return KakaoTurnRequest.model_validate({**data, **overrides})

    async def accept_and_run(self, request: KakaoTurnRequest):
        await self.service.accept(SESSION_ID, request)
        await asyncio.gather(*list(kakao_service._RUNNING))
        return await self.store.get(request.request_id)


@pytest.fixture
def h(clean_db, user_id) -> Harness:
    return Harness(clean_db, user_id, events=[result_event("축제 부스 운영 경험을 정리했어요.")])


def result_event(text: str, kind: str = "result") -> MessageCompleteEvent:
    return MessageCompleteEvent(
        message=CompletedMessage(
            request_id="r",
            session_id=SESSION_ID,
            response_kind=kind,
            ai_response=text,
            committed=kind == "result",
        )
    )


def error_event(code: str = "llm_error") -> ErrorEvent:
    return ErrorEvent(error=ErrorEventPayload(code=code, message="실패"))


# ===== 접수 =====


@pytest.mark.asyncio
async def test_accept_rejects_disallowed_callback_host_before_creating_work(h):
    request = h.request(callback_url="https://evil.example.com/x")

    with pytest.raises(InvalidRequestError):
        await h.service.accept(SESSION_ID, request)

    assert await h.store.get(request.request_id) is None


@pytest.mark.asyncio
async def test_accept_rejects_unknown_session_and_block_mismatch(h):
    with pytest.raises(SessionNotFoundError):
        await h.service.accept(str(uuid.uuid4()), h.request())
    with pytest.raises(InvalidRequestError):
        await h.service.accept(SESSION_ID, h.request(block_id="99"))


@pytest.mark.asyncio
async def test_same_request_twice_runs_once(h):
    request = h.request()

    await h.service.accept(SESSION_ID, request)
    await h.service.accept(SESSION_ID, request)  # 같은 본문 재접수 → 추가 작업 없음
    await asyncio.gather(*list(kakao_service._RUNNING))

    assert len(h.experience.prepare_calls) == 1
    assert len(h.sent) == 1


@pytest.mark.asyncio
async def test_same_request_id_with_different_body_is_conflict(h):
    request = h.request()
    await h.service.accept(SESSION_ID, request)

    other = request.model_copy(update={"utterance": "다른 내용"})
    with pytest.raises(IdempotencyKeyReusedError):
        await h.service.accept(SESSION_ID, other)
    await asyncio.gather(*list(kakao_service._RUNNING))


@pytest.mark.asyncio
async def test_concurrent_accept_creates_single_row(h):
    request = h.request()

    await asyncio.gather(*[h.service.accept(SESSION_ID, request) for _ in range(8)])
    await asyncio.gather(*list(kakao_service._RUNNING))

    assert len(h.experience.prepare_calls) == 1


# ===== 정상 흐름 =====


@pytest.mark.asyncio
async def test_success_flow(h):
    request = h.request()

    row = await h.accept_and_run(request)

    assert row.state == "SUCCEEDED"
    assert row.delivery_status == "SUCCESS"
    assert row.callback_url is None  # 1회용 URL 은 시도 뒤 즉시 비운다
    assert h.journal == ["callback", "complete"]  # 한도 환불 없음
    assert h.notifier.complete_calls == [{"outcome": "SUCCEEDED", "delivery_status": "SUCCESS"}]
    assert h.experience.prepare_calls[0]["channel"] == "KAKAO"
    assert h.experience.prepare_calls[0]["user_message"] == request.utterance
    outputs = h.sent[0]["payload"]["template"]["outputs"]
    assert "축제 부스 운영 경험을 정리했어요." in outputs[0]["simpleText"]["text"]
    assert h.sent[0]["url"] == CALLBACK_URL


@pytest.mark.asyncio
async def test_fallback_answer_is_success(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[result_event("경험 이야기를 들려주세요.", "fallback")])

    row = await h.accept_and_run(h.request())

    assert row.state == "SUCCEEDED"
    assert h.notifier.usage_calls == 0


@pytest.mark.asyncio
async def test_busy_session_waits_then_runs(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[result_event("완료")], busy_times=3)

    row = await h.accept_and_run(h.request())

    assert row.state == "SUCCEEDED"
    assert len(h.experience.prepare_calls) == 4  # 웹 턴이 끝나길 기다려 직렬화한다


# ===== 실패 =====


@pytest.mark.asyncio
async def test_confirmed_failure_sends_failure_then_refunds_then_notifies(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[error_event()])
    h.main_client.result = False  # 커밋 없음 확정

    row = await h.accept_and_run(h.request())

    assert row.state == "FAILED"
    assert h.journal == ["callback", "usage", "complete"]  # 콜백 → 한도 복구 → 완료 통지
    assert h.notifier.complete_calls[0]["outcome"] == "FAILED"
    card = h.sent[0]["payload"]["template"]["outputs"][0]["textCard"]
    assert "오류" in card["description"]


@pytest.mark.asyncio
async def test_usage_failure_still_attempts_turn_complete(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[error_event()])
    h.notifier.usage_ok = False

    row = await h.accept_and_run(h.request())

    assert row.state == "FAILED"
    assert h.journal == ["callback", "usage", "complete"]
    assert row.usage_reported is False  # 실패한 호출은 재시도 대상으로 남는다
    assert row.complete_notified is True


@pytest.mark.asyncio
async def test_failure_after_commit_is_success_without_refund(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[error_event()])
    h.main_client.result = True  # 오류 이벤트가 났지만 실제로는 커밋됨

    row = await h.accept_and_run(h.request())

    assert row.state == "SUCCEEDED"
    assert h.notifier.usage_calls == 0


@pytest.mark.asyncio
async def test_unconfirmed_commit_is_not_refunded_and_resolved_later(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[error_event()])
    h.main_client.result = RuntimeError("조회 실패")

    row = await h.accept_and_run(h.request())

    assert row.state == "COMMIT_UNKNOWN"
    assert h.notifier.usage_calls == 0  # 확인 전 환불 금지
    assert h.notifier.complete_calls == [
        {"outcome": "COMMIT_UNKNOWN", "delivery_status": "SUCCESS"}
    ]
    assert "오류가 발생" not in str(h.sent[0]["payload"])  # 성공·실패를 단정하지 않는다

    # 여전히 불명이면 그대로 둔다.
    await h.service.recover_once()
    assert (await h.store.get(row.request_id)).state == "COMMIT_UNKNOWN"
    assert h.notifier.usage_calls == 0

    # 결과가 확인되면 확정하고 그때 정리한다. 콜백은 다시 보내지 않는다.
    h.main_client.result = False
    await h.service.recover_once()
    resolved = await h.store.get(row.request_id)
    assert resolved.state == "FAILED"
    assert h.notifier.usage_calls == 1
    assert h.notifier.complete_calls[-1]["outcome"] == "FAILED"
    assert len(h.sent) == 1


@pytest.mark.asyncio
async def test_unconfirmed_commit_resolved_as_committed(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[error_event()])
    h.main_client.result = RuntimeError("조회 실패")
    row = await h.accept_and_run(h.request())

    h.main_client.result = True
    await h.service.recover_once()

    assert (await h.store.get(row.request_id)).state == "SUCCEEDED"
    assert h.notifier.usage_calls == 0


# ===== 전달 실패 =====


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["FAIL", "UNKNOWN"])
async def test_delivery_failure_keeps_success_without_refund_or_rerun(clean_db, user_id, delivery):
    h = Harness(clean_db, user_id, events=[result_event("완료")])
    h.send_result = delivery

    row = await h.accept_and_run(h.request())
    await h.service.recover_once()  # 복구 루프가 돌아도 재실행·재전송·환불이 없어야 한다

    final = await h.store.get(row.request_id)
    assert final.state == "SUCCEEDED"
    assert final.delivery_status == delivery
    assert h.notifier.usage_calls == 0
    assert h.experience.execute_calls == 1
    assert len(h.sent) == 1
    assert h.notifier.complete_calls[0] == {"outcome": "SUCCEEDED", "delivery_status": delivery}


@pytest.mark.asyncio
async def test_callback_exception_still_runs_cleanup(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[error_event()])
    h.send_result = RuntimeError("boom")

    row = await h.accept_and_run(h.request())

    assert row.delivery_status == "UNKNOWN"
    assert row.callback_url is None
    assert h.journal == ["callback", "usage", "complete"]
    assert row.state == "FAILED"


@pytest.mark.asyncio
async def test_failed_notification_is_retried_without_resending_callback(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[result_event("완료")])
    h.notifier.complete_ok = False
    row = await h.accept_and_run(h.request())
    assert row.complete_notified is False

    h.notifier.complete_ok = True
    await h.service.recover_once()

    assert (await h.store.get(row.request_id)).complete_notified is True
    assert len(h.notifier.complete_calls) == 2
    assert len(h.sent) == 1
    assert h.experience.execute_calls == 1


# ===== 기한 =====


@pytest.mark.asyncio
async def test_deadline_stops_work_and_sends_failure_before_expiry(clean_db, user_id):
    h = Harness(clean_db, user_id, hang=True)
    # 45초 기준 중단(= expires_at - 5초)이 곧 오고, 콜백 기한(expires_at)은 아직 남는다.
    request = h.request(expires_in=5.3)

    row = await h.accept_and_run(request)

    assert row.state == "EXPIRED"
    assert row.delivery_status == "SUCCESS"  # 50초 전에 실패 답변을 시도한다
    assert h.journal == ["callback", "usage", "complete"]
    assert h.notifier.complete_calls[0]["outcome"] == "FAILED"  # EXPIRED 는 FAILED 로 통지
    assert h.experience.repository.failed  # 잡고 있던 세션을 풀어 준다


@pytest.mark.asyncio
async def test_expired_before_start_does_not_run_or_attempt_callback(clean_db, user_id):
    h = Harness(clean_db, user_id, events=[result_event("완료")])

    row = await h.accept_and_run(h.request(expires_in=-1))

    assert row.state == "EXPIRED"
    assert row.delivery_status == "NOT_ATTEMPTED"
    assert h.experience.prepare_calls == []  # 기한 뒤에는 새 실행을 시작하지 않는다
    assert h.sent == []
    assert h.journal == ["usage", "complete"]  # 정리는 계속한다
    assert row.callback_url is None


# ===== 재시작·소유권 =====


@pytest.mark.asyncio
async def test_recovery_resumes_after_crash_without_reexecution(h):
    request = h.request()
    _, row = await h.store.accept(
        request_id=request.request_id,
        user_id=h.user_id,
        session_id=SESSION_ID,
        block_id="12",
        request_hash="x" * 64,
        utterance=request.utterance,
        callback_url=CALLBACK_URL,
        expires_at=request.expires_at,
    )
    claimed = await h.store.claim_worker(request.request_id)
    # 결과를 남기자마자 프로세스가 죽은 상황
    await h.store.record_outcome(
        request.request_id,
        claimed.worker_token,
        outcome="SUCCEEDED",
        reply_payload={"version": "2.0", "template": {"outputs": []}},
    )
    await h.store.release_worker(request.request_id, claimed.worker_token)

    assert await h.service.recover_once() == 1

    final = await h.store.get(request.request_id)
    assert final.state == "SUCCEEDED"
    assert h.experience.execute_calls == 0
    assert len(h.sent) == 1
    assert row.state == "ACCEPTED"


@pytest.mark.asyncio
async def test_crash_after_delivery_claim_does_not_resend(h):
    request = h.request()
    await h.store.accept(
        request_id=request.request_id,
        user_id=h.user_id,
        session_id=SESSION_ID,
        block_id="12",
        request_hash="x" * 64,
        utterance="u",
        callback_url=CALLBACK_URL,
        expires_at=request.expires_at,
    )
    claimed = await h.store.claim_worker(request.request_id)
    await h.store.record_outcome(
        request.request_id,
        claimed.worker_token,
        outcome="SUCCEEDED",
        reply_payload={"version": "2.0", "template": {"outputs": []}},
    )
    assert await h.store.claim_delivery(request.request_id) == CALLBACK_URL
    await h.store.release_worker(request.request_id, claimed.worker_token)  # 전송 도중 사망

    await h.service.recover_once()

    final = await h.store.get(request.request_id)
    assert h.sent == []  # 1회용 URL 이라 재전송하지 않는다
    assert final.delivery_status == "UNKNOWN"
    assert final.state == "SUCCEEDED"
    assert final.callback_url is None


@pytest.mark.asyncio
async def test_only_one_worker_claims_a_turn(h):
    request = h.request()
    await h.store.accept(
        request_id=request.request_id,
        user_id=h.user_id,
        session_id=SESSION_ID,
        block_id="12",
        request_hash="x" * 64,
        utterance="u",
        callback_url=CALLBACK_URL,
        expires_at=request.expires_at,
    )

    claims = await asyncio.gather(*[h.store.claim_worker(request.request_id) for _ in range(8)])

    assert sum(1 for c in claims if c is not None) == 1


@pytest.mark.asyncio
async def test_only_one_caller_gets_delivery_ownership(h):
    request = h.request()
    await h.store.accept(
        request_id=request.request_id,
        user_id=h.user_id,
        session_id=SESSION_ID,
        block_id="12",
        request_hash="x" * 64,
        utterance="u",
        callback_url=CALLBACK_URL,
        expires_at=request.expires_at,
    )

    urls = await asyncio.gather(*[h.store.claim_delivery(request.request_id) for _ in range(8)])

    assert [u for u in urls if u] == [CALLBACK_URL]


# ===== 상태 조회 =====


@pytest.mark.asyncio
async def test_status_hides_terminal_state_until_delivery_attempt_is_done(h):
    request = h.request()
    await h.store.accept(
        request_id=request.request_id,
        user_id=h.user_id,
        session_id=SESSION_ID,
        block_id="12",
        request_hash="x" * 64,
        utterance="u",
        callback_url=CALLBACK_URL,
        expires_at=request.expires_at,
    )
    claimed = await h.store.claim_worker(request.request_id)
    await h.store.record_outcome(
        request.request_id, claimed.worker_token, outcome="SUCCEEDED", reply_payload={}
    )

    status = await h.service.get_status(request.request_id, h.user_id)

    assert status.state == "RUNNING"  # 결과는 있어도 콜백 시도가 끝나기 전엔 종결로 공개하지 않는다
    assert status.delivery_status == "NOT_ATTEMPTED"


@pytest.mark.asyncio
async def test_status_requires_matching_user(h):
    request = h.request()
    row = await h.accept_and_run(request)

    assert await h.service.get_status(row.request_id, "1") is None
    assert (await h.service.get_status(row.request_id, h.user_id)).state == "SUCCEEDED"
    assert await h.service.get_status(str(uuid.uuid4()), h.user_id) is None


@pytest.mark.asyncio
async def test_row_repr_hides_callback_url(h):
    row = await h.accept_and_run(h.request())
    await h.store.get(row.request_id)
    pending = h.request()
    _, created = await h.store.accept(
        request_id=pending.request_id,
        user_id=h.user_id,
        session_id=SESSION_ID,
        block_id="12",
        request_hash="y" * 64,
        utterance="u",
        callback_url=CALLBACK_URL,
        expires_at=pending.expires_at,
    )

    assert "secret-token" not in repr(created)


# ===== 기존 서비스 연동 (channel) =====


class RecordingRepository:
    def __init__(self) -> None:
        self.saved: list[dict] = []
        self.failed_marked = 0

    async def save_message(self, *args, **kwargs):
        self.saved.append(kwargs)

    async def mark_request_failed(self, *args, **kwargs):
        self.failed_marked += 1
        return RequestRow(
            user_id="1", session_id=SESSION_ID, request_id="r", request_hash="h", status="failed"
        )


class RecordingMainClient:
    def __init__(self) -> None:
        self.usage_reports = 0

    async def report_failed_usage(self, **kwargs):
        self.usage_reports += 1


def _prepared(channel: str) -> PreparedRequest:
    return PreparedRequest(
        user_id="1",
        session_id=SESSION_ID,
        request_id=str(uuid.uuid4()),
        request_hash="h",
        user_message="안녕",
        owner_token=str(uuid.uuid4()),
        channel=channel,
    )


@pytest.mark.asyncio
async def test_kakao_message_stores_only_the_summary_with_channel():
    repo = RecordingRepository()
    service = ExperienceMapService(repository=repo)
    long_text = "가" * 1500

    await service._save_message(_prepared("KAKAO"), {"ai_response": long_text}, None, None)
    await service._save_message(_prepared("WEB"), {"ai_response": long_text}, None, None)

    kakao, web = repo.saved
    assert kakao["channel"] == "KAKAO"
    assert len(kakao["ai_responses"]) == 1 and len(kakao["ai_responses"][0]) == 1000
    assert web["channel"] == "WEB"
    assert web["ai_responses"] == [long_text]  # 웹의 상세 답변은 그대로


@pytest.mark.asyncio
async def test_kakao_failure_does_not_refund_but_web_does():
    from features.experience_map.errors import LlmError

    repo, client = RecordingRepository(), RecordingMainClient()
    service = ExperienceMapService(repository=repo, main_client=client)

    await service._fail(_prepared("KAKAO"), LlmError())
    assert client.usage_reports == 0  # 카톡은 커밋 확인 뒤 카톡 턴 서비스가 환불한다

    await service._fail(_prepared("WEB"), LlmError())
    assert client.usage_reports == 1
