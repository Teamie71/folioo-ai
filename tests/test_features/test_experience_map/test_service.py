"""경험정리 서비스 테스트 (실제 PostgreSQL + mock graph)

재시도 주 경로와 실행권 상실 처리를 검증합니다. Codex 리뷰 1~4의 회귀 테스트입니다.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from app.schemas.experience_map import ExperienceMapEvent, NodeStatusEvent
from features.experience_map.errors import (
    RequestNotFoundError,
    RetryExpiredError,
    RetryNotAllowedError,
    SessionBusyError,
)
from features.experience_map.graph_runner import MockGraphRunner
from features.experience_map.repository import ExperienceMapRepository
from features.experience_map.service import ExperienceMapService
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


async def run_stream(service: ExperienceMapService, prepared) -> list[str]:
    return [event.model_dump()["type"] async for event in service.stream(prepared)]


async def make_failed_request(service, repo, user_id) -> tuple[str, str]:
    """실패 상태의 요청을 만든다. `(session_id, request_id)`"""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_failed(user_id, request_id, error={"code": "llm_error"})
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
    await repo.claim_request(user_id, session_id, newer, "b" * 64)
    await repo.mark_request_completed(user_id, newer)

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
    await repo.mark_request_failed(user_id, request_id, error={"code": "lease_expired"})
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
                await repo.mark_request_failed(user_id, request_id, error={"code": "lease_expired"})
                await repo.retry_request(user_id, request_id)
        return collected

    types = await asyncio.wait_for(consume(), timeout=4)

    assert types[-1] == "error"
    assert "processing_complete" not in types
