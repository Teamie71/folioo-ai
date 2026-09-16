"""경험정리 서비스 테스트 (실제 PostgreSQL + mock graph)

재시도 주 경로와 실행권 상실 처리를 검증합니다. Codex 리뷰 1~4의 회귀 테스트입니다.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from app.schemas.experience_map import (
    CompletedMessage,
    ExperienceMapEvent,
    MessageCompleteEvent,
    NodeStatusEvent,
)
from features.experience_map.errors import (
    RequestNotFoundError,
    RetryExpiredError,
    RetryNotAllowedError,
    SessionBusyError,
)
from features.experience_map.graph_runner import MockGraphRunner
from features.experience_map.prompts.router import build_gap_context
from features.experience_map.repository import ExperienceMapRepository
from features.experience_map.service import ExperienceMapService, _build_state
from features.experience_map.state import ExperienceMapState

HASH_A = "a" * 64


def new_request_id() -> str:
    return str(uuid.uuid4())


@pytest_asyncio.fixture
async def repo(clean_db) -> ExperienceMapRepository:
    return ExperienceMapRepository(clean_db, lease_seconds=300)


@pytest_asyncio.fixture
async def service(repo) -> ExperienceMapService:
    return ExperienceMapService(repository=repo, runner=MockGraphRunner())


@pytest.fixture(autouse=True)
def map_snapshot(repository_snapshot, monkeypatch):
    """실제 block DDL 없이도 서비스의 상태 조립 경로를 검증한다.

    이 모듈은 요청 claim·재시도의 DB 원자성을 검증하고 그래프는 mock으로 둔다.
    block 테이블은 메인 서버가 소유하므로, 여기서 임의 DDL을 만들지 않고
    결정적인 빈 snapshot을 주입한다.
    """
    monkeypatch.setattr(ExperienceMapRepository, "get_map_snapshot", repository_snapshot)


async def run_stream(service: ExperienceMapService, prepared) -> list[str]:
    return [event.model_dump()["type"] async for event in service.stream(prepared)]


async def fail_current(repo, user_id: str, request_id: str, **kwargs) -> None:
    """현재 실행권으로 실패 처리한다 (테스트 준비용).

    `owner_token` 은 필수 인자다. 준비 코드도 실제 호출부처럼 현재 주인을 확인한다.
    """
    row = await repo.get_request(user_id, request_id)
    kwargs.setdefault("error", {"code": "llm_error"})
    await repo.mark_request_failed(user_id, request_id, owner_token=row.owner_token, **kwargs)


async def make_failed_request(service, repo, user_id) -> tuple[str, str]:
    """실패 상태의 요청을 만든다. `(session_id, request_id)`"""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await fail_current(repo, user_id, request_id)
    return session.session_id, request_id


# ===== 재시도 주 경로 (Codex 리뷰 1) =====


@pytest.mark.asyncio
async def test_retry_actually_reruns_the_graph(service, repo, user_id):
    """실패한 요청을 재시도하면 그래프가 실제로 다시 돈다.

    3.10 까지 이 경로를 검증하는 테스트가 없었다. `prepare_retry` 가 상태를
    바꾸지 않아 `failed` 인 채로 실행되고 있었다.
    """
    session_id, request_id = await make_failed_request(service, repo, user_id)

    prepared = await service.prepare_retry(user_id, session_id, request_id)

    assert prepared.is_retry is True
    assert prepared.owner_token is not None
    assert (await repo.get_request(user_id, request_id)).status == "running"

    types = await run_stream(service, prepared)
    assert "node_status" in types  # 재실행됐다
    assert types[-1] == "processing_complete"
    assert (await repo.get_request(user_id, request_id)).status == "completed"


@pytest.mark.asyncio
async def test_concurrent_retry_requests_only_one_proceeds(service, repo, user_id):
    """같은 실패 요청에 재시도가 동시에 와도 하나만 통과한다."""
    session_id, request_id = await make_failed_request(service, repo, user_id)

    results = await asyncio.gather(
        *[service.prepare_retry(user_id, session_id, request_id) for _ in range(5)],
        return_exceptions=True,
    )

    accepted = [r for r in results if not isinstance(r, Exception)]
    rejected = [r for r in results if isinstance(r, SessionBusyError)]
    assert len(accepted) == 1
    assert len(rejected) == 4


@pytest.mark.asyncio
async def test_retry_rejects_expired(service, repo, clean_db, user_id):
    session_id, request_id = await make_failed_request(service, repo, user_id)
    await clean_db.execute(
        "UPDATE ai_experience_request SET retry_expires_at = now() - interval '1 minute' "
        "WHERE user_id = $1",
        int(user_id),
    )

    with pytest.raises(RetryExpiredError):
        await service.prepare_retry(user_id, session_id, request_id)


@pytest.mark.asyncio
async def test_retry_only_latest_request(service, repo, user_id):
    """마지막 요청만 재시도할 수 있다 (9절 19번)."""
    session_id, old_request = await make_failed_request(service, repo, user_id)
    newer = new_request_id()
    claimed = await repo.claim_request(user_id, session_id, newer, "b" * 64)
    await repo.mark_request_completed(user_id, newer, owner_token=claimed.request.owner_token)

    with pytest.raises(RetryNotAllowedError):
        await service.prepare_retry(user_id, session_id, old_request)


@pytest.mark.asyncio
async def test_retry_unknown_request_is_not_found(service, repo, user_id):
    session = await repo.get_or_create_session(user_id)

    with pytest.raises(RequestNotFoundError):
        await service.prepare_retry(user_id, session.session_id, new_request_id())


# ===== 실행권 상실 (Codex 리뷰 2·3) =====


class _SlowRunner:
    """이벤트 사이에 침묵이 있는 실행기"""

    def __init__(self, gap_seconds: float) -> None:
        self._gap = gap_seconds

    async def run(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        yield NodeStatusEvent(node="router", status="running")
        await asyncio.sleep(self._gap)
        yield NodeStatusEvent(node="router", status="completed")

    async def resume(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        async for event in self.run(state):
            yield event


@pytest.mark.asyncio
async def test_lost_lease_does_not_overwrite_other_worker(repo, user_id):
    """실행권을 잃은 worker 는 DB 를 건드리지 않는다.

    다른 worker 가 같은 요청을 가져간 상태에서 옛 worker 가 끝나도, 그 결과가
    저장되면 안 된다.
    """
    service = ExperienceMapService(repository=repo, runner=MockGraphRunner())
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    prepared = await service.prepare_chat(
        user_id,
        session.session_id,
        request_id,
        user_message="정리해줘",
        context_experience_id=None,
        view=None,
        stored_files=[],
    )

    # 다른 경로가 이 요청을 가져간다 (만료 정리 → 재시도).
    await fail_current(repo, user_id, request_id, error={"code": "lease_expired"})
    await repo.retry_request(user_id, request_id)

    types = await run_stream(service, prepared)

    assert types[-1] == "error"
    assert "processing_complete" not in types
    # 새 주인의 상태가 그대로다.
    assert (await repo.get_request(user_id, request_id)).status == "running"


@pytest.mark.asyncio
async def test_lease_loss_interrupts_during_silence(repo, user_id):
    """조용한 구간에서 lease 를 잃어도 침묵이 끝나기 전에 끊는다 (Codex 리뷰 4).

    이벤트가 도착할 때만 확인하면 침묵이 끝날 때까지 모른다. 파일처리 120초처럼
    긴 침묵이 정상인 구조라 실제로 벌어진다.

    **감지 지연의 상한은 실행권 확인 주기(운영 30초)다.** 이 테스트는 주기를
    0.1초로 줄여 5초 침묵 안에 끊기는 것을 확인한다.
    """
    service = ExperienceMapService(
        repository=repo, runner=_SlowRunner(gap_seconds=5), lease_renew_interval=0.1
    )
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    prepared = await service.prepare_chat(
        user_id,
        session.session_id,
        request_id,
        user_message="정리해줘",
        context_experience_id=None,
        view=None,
        stored_files=[],
    )

    async def consume() -> list[str]:
        collected = []
        async for event in service.stream(prepared):
            collected.append(event.model_dump()["type"])
            if collected[-1] == "node_status":
                # 첫 이벤트 뒤 5초 침묵이 시작된다. 그 사이에 실행권을 뺏는다.
                await fail_current(repo, user_id, request_id, error={"code": "lease_expired"})
                await repo.retry_request(user_id, request_id)
        return collected

    types = await asyncio.wait_for(consume(), timeout=4)

    assert types[-1] == "error"
    assert "processing_complete" not in types


# ===== 직전 턴 제안(active_gap) 전달 =====


@pytest.mark.asyncio
async def test_active_gap_reaches_the_graph_state(service, repo, user_id):
    """세션에 저장된 제안이 다음 턴 state 까지 실린다.

    저장은 되는데 **읽는 쪽이 빠져 있었다.** 그러면 router 와 content_filter 가
    직전 질문을 못 봐서, 그 질문에 대한 짧은 답변을 무관한 입력으로 판정하고
    fallback 으로 보낸다 (명세 5-1).
    """
    session = await repo.get_or_create_session(user_id)
    gap = {
        "message": "이탈률이 감소한 주요 원인은 무엇인가요?",
        "path": "교내 커머스 리뉴얼 > 성과",
    }
    await repo.save_active_gap(user_id, gap)

    prepared = await service.prepare_chat(
        user_id=user_id,
        session_id=session.session_id,
        request_id=new_request_id(),
        user_message="입력 단계를 줄였기 때문",
        context_experience_id=None,
        view=None,
        stored_files=[],
    )
    state = await _build_state(prepared, repo)

    assert state["active_gap"] == gap
    assert "이탈률이 감소한 주요 원인은 무엇인가요?" in build_gap_context(state["active_gap"])


@pytest.mark.asyncio
async def test_retry_carries_active_gap_too(service, repo, user_id):
    """재시도도 같은 state 조립을 거치므로 제안이 실려야 한다."""
    session_id, request_id = await make_failed_request(service, repo, user_id)
    gap = {"message": "그때 맡은 역할은 무엇이었나요?", "path": "교내 커머스 리뉴얼 > 담당업무"}
    await repo.save_active_gap(user_id, gap)

    prepared = await service.prepare_retry(
        user_id=user_id, session_id=session_id, request_id=request_id
    )
    state = await _build_state(prepared, repo)

    assert state["active_gap"] == gap


@pytest.mark.asyncio
async def test_no_active_gap_gives_empty_context(service, repo, user_id):
    """제안이 없으면 빈 맥락이다. 라우터가 없는 질문을 지어내면 안 된다."""
    session = await repo.get_or_create_session(user_id)

    prepared = await service.prepare_chat(
        user_id=user_id,
        session_id=session.session_id,
        request_id=new_request_id(),
        user_message="교내 커머스 리뉴얼을 했다",
        context_experience_id=None,
        view=None,
        stored_files=[],
    )
    state = await _build_state(prepared, repo)

    assert state["active_gap"] is None
    assert build_gap_context(state["active_gap"]) == ""


# ===== fallback 응답의 재연결·멱등 재생 =====


class _FallbackOnlyRunner:
    """fallback message_complete만 내는 대역 실행기."""

    async def run(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        yield MessageCompleteEvent(
            message=CompletedMessage(
                request_id=state["request_id"],
                session_id=state["session_id"],
                response_kind="fallback",
                ai_response="지금은 도와드릴 수 없어요.",
                committed=False,
            )
        )

    async def resume(self, state: ExperienceMapState) -> AsyncIterator[ExperienceMapEvent]:
        async for event in self.run(state):
            yield event


@pytest.mark.asyncio
async def test_fallback_message_survives_idempotent_replay(repo, user_id):
    """fallback 완료 요청을 같은 request_id로 다시 부르면 안내 문구가 그대로 온다.

    실제로 지적된 문제다 — fallback은 result·suggestion을 남기지 않으므로,
    이 값을 별도로 저장하지 않으면 멱등 재생·SSE 재연결 시
    `processing_started → processing_complete`만 오고 안내 문구가 사라진다.
    """
    fallback_service = ExperienceMapService(repository=repo, runner=_FallbackOnlyRunner())
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()

    async def run_once():
        prepared = await fallback_service.prepare_chat(
            user_id=user_id,
            session_id=session.session_id,
            request_id=request_id,
            user_message="자기소개서 항목도 대신 써줘",
            context_experience_id=None,
            view=None,
            stored_files=[],
        )
        return [event async for event in fallback_service.stream(prepared)]

    first_run = await run_once()
    first_fallback = next(
        e
        for e in first_run
        if getattr(e, "message", None) and e.message.response_kind == "fallback"
    )
    assert first_fallback.message.ai_response == "지금은 도와드릴 수 없어요."

    replayed = await run_once()
    replayed_fallback = next(
        e for e in replayed if getattr(e, "message", None) and e.message.response_kind == "fallback"
    )
    assert replayed_fallback.message.ai_response == "지금은 도와드릴 수 없어요."


# ===== 대화 히스토리 저장 =====


@pytest.mark.asyncio
async def test_successful_turn_saves_message(service, repo, user_id):
    """정상 완료된 턴은 user_message와 모든 ai_response를 순서대로 남긴다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    prepared = await service.prepare_chat(
        user_id=user_id,
        session_id=session.session_id,
        request_id=request_id,
        user_message="결제 오류를 해결했다.",
        context_experience_id=None,
        view=None,
        stored_files=[],
    )

    async for _ in service.stream(prepared):
        pass

    rows, _ = await repo.list_messages(user_id, session.session_id, cursor=None, limit=50)

    assert len(rows) == 1
    assert rows[0].request_id == request_id
    assert rows[0].user_message == "결제 오류를 해결했다."
    assert rows[0].ai_responses == [
        "교내 커머스 리뉴얼 > 문제해결에 1개를 정리했어요.",
        "그 해결 방법을 고른 기준이 무엇이었나요?",
    ]


@pytest.mark.asyncio
async def test_fallback_turn_saves_message(user_id, repo):
    """fallback으로 끝난 턴도 대화 히스토리에 남는다."""
    fallback_service = ExperienceMapService(repository=repo, runner=_FallbackOnlyRunner())
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    prepared = await fallback_service.prepare_chat(
        user_id=user_id,
        session_id=session.session_id,
        request_id=request_id,
        user_message="자기소개서 항목도 대신 써줘",
        context_experience_id=None,
        view=None,
        stored_files=[],
    )

    async for _ in fallback_service.stream(prepared):
        pass

    rows, _ = await repo.list_messages(user_id, session.session_id, cursor=None, limit=50)

    assert len(rows) == 1
    assert rows[0].user_message == "자기소개서 항목도 대신 써줘"
    assert rows[0].ai_responses == ["지금은 도와드릴 수 없어요."]


@pytest.mark.asyncio
async def test_replaying_completed_request_does_not_duplicate_message(service, repo, user_id):
    """같은 request_id를 멱등 재생해도 대화 메시지는 한 번만 남는다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()

    async def run_once():
        prepared = await service.prepare_chat(
            user_id=user_id,
            session_id=session.session_id,
            request_id=request_id,
            user_message="결제 오류를 해결했다.",
            context_experience_id=None,
            view=None,
            stored_files=[],
        )
        async for _ in service.stream(prepared):
            pass

    await run_once()
    await run_once()  # 같은 request_id — 저장된 결과를 재생할 뿐, 다시 실행하지 않는다.

    rows, _ = await repo.list_messages(user_id, session.session_id, cursor=None, limit=50)

    assert len(rows) == 1


@pytest.mark.asyncio
async def test_lost_lease_does_not_save_message(service, repo, user_id):
    """실행권을 잃으면 대화 메시지도 남기지 않는다 (DB를 건드리지 않는다는 규칙과 동일).

    `test_lost_lease_does_not_overwrite_other_worker`와 같은 방식으로 재현한다 —
    스트림을 시작하기 전에 다른 경로가 이미 이 요청을 가져가서, 들고 있던
    `prepared`의 실행권이 못 쓰게 된 상태로 만든다.
    """
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    prepared = await service.prepare_chat(
        user_id,
        session.session_id,
        request_id,
        user_message="결제 오류를 해결했다.",
        context_experience_id=None,
        view=None,
        stored_files=[],
    )

    # 다른 경로가 이 요청을 가져간다 (만료 정리 → 재시도) — prepared 의 실행권이 stale해진다.
    await fail_current(repo, user_id, request_id, error={"code": "lease_expired"})
    await repo.retry_request(user_id, request_id)

    types = await run_stream(service, prepared)

    assert types[-1] == "error"
    rows, _ = await repo.list_messages(user_id, session.session_id, cursor=None, limit=50)
    assert rows == []
