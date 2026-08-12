"""경험정리 세션·요청 Repository 테스트 (실제 PostgreSQL)"""

import asyncio
import uuid

import pytest

from features.experience_map.repository import (
    ClaimOutcome,
    ExperienceMapRepository,
    LeaseRenewer,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture
def repo(clean_db) -> ExperienceMapRepository:
    return ExperienceMapRepository(clean_db, lease_seconds=300)


def new_request_id() -> str:
    return str(uuid.uuid4())


# ===== 세션 =====


@pytest.mark.asyncio
async def test_get_or_create_session_creates_once(repo, user_id):
    """사용자당 세션은 1개다. 두 번 불러도 같은 세션이다."""
    first = await repo.get_or_create_session(user_id)
    second = await repo.get_or_create_session(user_id)

    assert first.session_id == second.session_id
    assert first.user_id == user_id


@pytest.mark.asyncio
async def test_concurrent_session_creation_yields_one(repo, user_id):
    """여러 worker가 동시에 만들어도 세션은 하나다."""
    results = await asyncio.gather(*[repo.get_or_create_session(user_id) for _ in range(10)])

    assert len({r.session_id for r in results}) == 1


@pytest.mark.asyncio
async def test_get_session_blocks_other_user(repo, user_id):
    """다른 사용자의 세션은 조회되지 않는다."""
    session = await repo.get_or_create_session(user_id)
    other_user = str(int(user_id) + 1)

    assert await repo.get_session(user_id, session.session_id) is not None
    assert await repo.get_session(other_user, session.session_id) is None


@pytest.mark.asyncio
async def test_active_gap_round_trip(repo, user_id):
    """gap을 저장하고 다음 턴에 읽는다. 없으면 null로 비운다 (5-10)."""
    session = await repo.get_or_create_session(user_id)
    gap = {
        "gap_id": str(uuid.uuid4()),
        "gap_type": "extend_block",
        "anchor_block_id": "3055",
        "message": "그 해결 방법을 고른 기준이 무엇이었나요?",
        "created_request_id": str(uuid.uuid4()),
    }

    await repo.save_active_gap(user_id, gap)
    assert (await repo.get_session(user_id, session.session_id)).active_gap == gap

    await repo.save_active_gap(user_id, None)
    assert (await repo.get_session(user_id, session.session_id)).active_gap is None


# ===== 요청 claim =====


@pytest.mark.asyncio
async def test_claim_creates_running_request(repo, user_id):
    session = await repo.get_or_create_session(user_id)

    result = await repo.claim_request(user_id, session.session_id, new_request_id(), HASH_A)

    assert result.outcome is ClaimOutcome.CLAIMED
    assert result.request.status == "running"
    assert result.request.lease_expires_at is not None


@pytest.mark.asyncio
async def test_second_running_request_is_busy(repo, user_id):
    """세션당 running 은 1건이다."""
    session = await repo.get_or_create_session(user_id)
    await repo.claim_request(user_id, session.session_id, new_request_id(), HASH_A)

    result = await repo.claim_request(user_id, session.session_id, new_request_id(), HASH_B)

    assert result.outcome is ClaimOutcome.SESSION_BUSY


@pytest.mark.asyncio
async def test_concurrent_claims_only_one_wins(repo, user_id):
    """여러 worker가 같은 세션을 동시에 잡으면 하나만 성공한다.

    partial unique index 가 두 번째 INSERT 를 막는다.
    """
    session = await repo.get_or_create_session(user_id)

    results = await asyncio.gather(
        *[
            repo.claim_request(user_id, session.session_id, new_request_id(), HASH_A)
            for _ in range(8)
        ]
    )

    claimed = [r for r in results if r.outcome is ClaimOutcome.CLAIMED]
    busy = [r for r in results if r.outcome is ClaimOutcome.SESSION_BUSY]
    assert len(claimed) == 1
    assert len(busy) == 7


@pytest.mark.asyncio
async def test_same_request_same_hash_while_running_is_busy(repo, user_id):
    """같은 요청에 stream 을 두 번 붙이면 409 다 (명세 2-5)."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    result = await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    assert result.outcome is ClaimOutcome.SESSION_BUSY


@pytest.mark.asyncio
async def test_same_request_same_hash_completed_replays(repo, user_id):
    """완료된 요청은 저장 결과를 재전송한다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_completed(user_id, request_id, result={"map_version": 43})

    result = await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    assert result.outcome is ClaimOutcome.REPLAY
    assert result.request.result == {"map_version": 43}


@pytest.mark.asyncio
async def test_same_request_different_hash_conflicts(repo, user_id):
    """같은 request_id 에 다른 입력은 idempotency_key_reused 다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_completed(user_id, request_id)

    result = await repo.claim_request(user_id, session.session_id, request_id, HASH_B)

    assert result.outcome is ClaimOutcome.HASH_MISMATCH


@pytest.mark.asyncio
async def test_failed_request_requires_retry_api(repo, user_id):
    """실패한 요청은 chat 이 아니라 retry API 로 이어간다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_failed(user_id, request_id, error={"code": "llm_error"})

    result = await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    assert result.outcome is ClaimOutcome.RETRY_REQUIRED


@pytest.mark.asyncio
async def test_new_request_disables_previous_retry(repo, user_id):
    """새 요청을 시작하면 이전 실패 요청은 재시도할 수 없다."""
    session = await repo.get_or_create_session(user_id)
    old_request = new_request_id()
    await repo.claim_request(user_id, session.session_id, old_request, HASH_A)
    await repo.mark_request_failed(user_id, old_request, error={"code": "llm_error"})
    assert (await repo.get_request(user_id, old_request)).retryable is True

    await repo.claim_request(user_id, session.session_id, new_request_id(), HASH_B)

    assert (await repo.get_request(user_id, old_request)).retryable is False


# ===== 상태 전이 =====


@pytest.mark.asyncio
async def test_mark_completed_clears_lease(repo, user_id):
    """완료된 요청은 만료 정리 대상이 아니다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    row = await repo.mark_request_completed(
        user_id, request_id, result={"map_version": 43}, committed_version=43
    )

    assert row.status == "completed"
    assert row.lease_expires_at is None
    assert row.committed_version == 43
    assert row.retryable is False


@pytest.mark.asyncio
async def test_mark_failed_sets_retry_ttl(repo, user_id):
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    row = await repo.mark_request_failed(
        user_id, request_id, error={"code": "llm_error"}, failed_node="refine"
    )

    assert row.status == "failed"
    assert row.retryable is True
    assert row.retry_expires_at is not None
    assert row.failed_node == "refine"
    assert row.error == {"code": "llm_error"}


@pytest.mark.asyncio
async def test_non_retryable_failure_has_no_ttl(repo, user_id):
    """db_constraint_violation 처럼 재시도해도 같은 결과면 버튼을 주지 않는다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    row = await repo.mark_request_failed(
        user_id, request_id, error={"code": "db_constraint_violation"}, retryable=False
    )

    assert row.retryable is False
    assert row.retry_expires_at is None


@pytest.mark.asyncio
async def test_get_request_blocks_other_user(repo, user_id):
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    assert await repo.get_request(user_id, request_id) is not None
    assert await repo.get_request(str(int(user_id) + 1), request_id) is None


@pytest.mark.asyncio
async def test_get_latest_request(repo, user_id):
    session = await repo.get_or_create_session(user_id)
    first = new_request_id()
    await repo.claim_request(user_id, session.session_id, first, HASH_A)
    await repo.mark_request_completed(user_id, first)
    second = new_request_id()
    await repo.claim_request(user_id, session.session_id, second, HASH_B)

    assert (await repo.get_latest_request(user_id, session.session_id)).request_id == second


# ===== lease =====


@pytest.mark.asyncio
async def test_renew_lease_extends(repo, user_id):
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    claimed = await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    before = claimed.request.lease_expires_at

    assert await repo.renew_request_lease(user_id, request_id) is True
    assert (await repo.get_request(user_id, request_id)).lease_expires_at >= before


@pytest.mark.asyncio
async def test_renew_lease_fails_when_not_running(repo, user_id):
    """완료된 요청의 lease 는 갱신되지 않는다. 호출자는 실행을 멈춰야 한다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_completed(user_id, request_id)

    assert await repo.renew_request_lease(user_id, request_id) is False


@pytest.mark.asyncio
async def test_renew_lease_fails_for_other_user(repo, user_id):
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    assert await repo.renew_request_lease(str(int(user_id) + 1), request_id) is False


@pytest.mark.asyncio
async def test_expired_lease_stays_retryable(clean_db, user_id):
    """lease 만료만으로는 재시도 자격을 잃지 않는다.

    `GET /state` 가 만료 lease 를 정리하는 경로다. 사용자가 아직 새 요청을
    보내지 않았으므로 재시도 버튼이 남아야 한다.
    """
    repo = ExperienceMapRepository(clean_db, lease_seconds=-1)  # 이미 만료된 lease
    session = await repo.get_or_create_session(user_id)
    stale_request = new_request_id()
    await repo.claim_request(user_id, session.session_id, stale_request, HASH_A)

    expired = await repo.expire_stale_running_requests()

    assert stale_request in {row.request_id for row in expired}
    stale = await repo.get_request(user_id, stale_request)
    assert stale.status == "failed"
    assert stale.retryable is True
    assert stale.error["code"] == "lease_expired"


@pytest.mark.asyncio
async def test_expired_lease_frees_the_session(clean_db, user_id):
    """프로세스가 죽어 lease 를 놓치면 세션이 풀린다.

    풀리지 않으면 partial unique index 때문에 그 세션이 영구히 잠긴다.
    새 요청이 시작됐으므로 이전 요청은 재시도 대상에서 빠진다 (9절 4번).
    """
    repo = ExperienceMapRepository(clean_db, lease_seconds=-1)
    session = await repo.get_or_create_session(user_id)
    stale_request = new_request_id()
    await repo.claim_request(user_id, session.session_id, stale_request, HASH_A)

    result = await repo.claim_request(user_id, session.session_id, new_request_id(), HASH_B)

    assert result.outcome is ClaimOutcome.CLAIMED
    stale = await repo.get_request(user_id, stale_request)
    assert stale.status == "failed"
    assert stale.error["code"] == "lease_expired"
    assert stale.retryable is False


@pytest.mark.asyncio
async def test_expire_leaves_live_requests_alone(repo, user_id):
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    expired = await repo.expire_stale_running_requests()

    assert all(row.request_id != request_id for row in expired)
    assert (await repo.get_request(user_id, request_id)).status == "running"


@pytest.mark.asyncio
async def test_lease_renewer_signals_loss(clean_db, user_id):
    """lease 를 잃으면 lost 이벤트가 켜진다. 호출자가 실행을 취소한다."""
    repo = ExperienceMapRepository(clean_db, lease_seconds=300)
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)

    async with LeaseRenewer(repo, user_id, request_id, interval_seconds=0.05) as renewer:
        await asyncio.sleep(0.15)
        assert not renewer.lost.is_set()

        # 다른 경로가 요청을 끝내면 우리 lease 는 사라진다.
        await repo.mark_request_completed(user_id, request_id)
        await asyncio.wait_for(renewer.lost.wait(), timeout=2)


# ===== 보관 정리 =====


@pytest.mark.asyncio
async def test_purge_old_requests(repo, clean_db, user_id):
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_completed(user_id, request_id)
    await clean_db.execute(
        "UPDATE ai_experience_request SET updated_at = now() - interval '31 days' "
        "WHERE user_id = $1",
        int(user_id),
    )

    assert await repo.purge_old_completed_requests() >= 1
    assert await repo.get_request(user_id, request_id) is None


@pytest.mark.asyncio
async def test_purge_keeps_recent_requests(repo, user_id):
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_completed(user_id, request_id)

    await repo.purge_old_completed_requests()

    assert await repo.get_request(user_id, request_id) is not None


# ===== 재시도 원자성 (Codex 리뷰 1·3) =====


@pytest.mark.asyncio
async def test_retry_transitions_to_running(repo, user_id):
    """재시도는 요청을 실제로 `running` 으로 되돌린다.

    이게 안 되면 `failed` 인 채로 그래프가 돌고 lease 갱신도 실패한다.
    """
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_failed(user_id, request_id, error={"code": "llm_error"})

    result = await repo.retry_request(user_id, request_id)

    assert result.outcome is ClaimOutcome.CLAIMED
    row = await repo.get_request(user_id, request_id)
    assert row.status == "running"
    assert row.lease_expires_at is not None
    assert row.owner_token is not None
    assert row.error is None
    assert await repo.renew_request_lease(user_id, request_id, row.owner_token) is True


@pytest.mark.asyncio
async def test_concurrent_retries_only_one_wins(repo, user_id):
    """같은 실패 요청에 재시도를 동시에 보내도 하나만 잡는다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_failed(user_id, request_id, error={"code": "llm_error"})

    results = await asyncio.gather(*[repo.retry_request(user_id, request_id) for _ in range(8)])

    claimed = [r for r in results if r.outcome is ClaimOutcome.CLAIMED]
    assert len(claimed) == 1
    assert len({r.request.owner_token for r in claimed}) == 1


@pytest.mark.asyncio
async def test_retry_issues_new_owner_token(repo, user_id):
    """재시도할 때마다 새 실행권을 발급한다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    claimed = await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    first_token = claimed.request.owner_token
    await repo.mark_request_failed(user_id, request_id, error={"code": "llm_error"})

    retried = await repo.retry_request(user_id, request_id)

    assert retried.request.owner_token != first_token


@pytest.mark.asyncio
async def test_retry_rejects_expired_ttl(clean_db, user_id):
    repo = ExperienceMapRepository(clean_db, lease_seconds=300)
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_failed(user_id, request_id, error={"code": "llm_error"})
    await clean_db.execute(
        "UPDATE ai_experience_request SET retry_expires_at = now() - interval '1 minute' "
        "WHERE user_id = $1 AND request_id = $2",
        int(user_id),
        uuid.UUID(request_id),
    )

    assert (await repo.retry_request(user_id, request_id)).outcome is ClaimOutcome.RETRY_EXPIRED


@pytest.mark.asyncio
async def test_retry_on_completed_request_replays(repo, user_id):
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    await repo.mark_request_completed(user_id, request_id, result={"map_version": 43})

    assert (await repo.retry_request(user_id, request_id)).outcome is ClaimOutcome.REPLAY


@pytest.mark.asyncio
async def test_retry_blocked_by_other_running_request(repo, user_id):
    """세션에 다른 running 이 있으면 재시도는 세션당 1건 제약에 걸린다."""
    session = await repo.get_or_create_session(user_id)
    failed_request = new_request_id()
    await repo.claim_request(user_id, session.session_id, failed_request, HASH_A)
    await repo.mark_request_failed(user_id, failed_request, error={"code": "llm_error"})
    await repo.claim_request(user_id, session.session_id, new_request_id(), HASH_B)

    # 새 요청이 시작되면 이전 실패는 retryable 이 내려간다 (9절 4번).
    assert (await repo.retry_request(user_id, failed_request)).outcome in {
        ClaimOutcome.SESSION_BUSY,
        ClaimOutcome.RETRY_NOT_ALLOWED,
    }


@pytest.mark.asyncio
async def test_retry_unknown_request(repo, user_id):
    await repo.get_or_create_session(user_id)

    result = await repo.retry_request(user_id, new_request_id())

    assert result.outcome is ClaimOutcome.RETRY_NOT_FOUND


# ===== 실행권 (Codex 리뷰 2·3) =====


@pytest.mark.asyncio
async def test_completed_request_cannot_be_overwritten(repo, user_id):
    """lease 를 잃은 옛 worker 가 완료된 요청을 실패로 되돌리지 못한다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    claimed = await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    token = claimed.request.owner_token
    await repo.mark_request_completed(user_id, request_id, result={"map_version": 43})

    overwritten = await repo.mark_request_failed(
        user_id, request_id, error={"code": "llm_error"}, owner_token=token
    )

    assert overwritten is None
    assert (await repo.get_request(user_id, request_id)).status == "completed"


@pytest.mark.asyncio
async def test_stale_token_cannot_renew_or_finish(repo, user_id):
    """재시도로 주인이 바뀌면 이전 worker 의 쓰기가 전부 무시된다."""
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    first = await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    stale_token = first.request.owner_token
    await repo.mark_request_failed(user_id, request_id, error={"code": "llm_error"})
    retried = await repo.retry_request(user_id, request_id)
    fresh_token = retried.request.owner_token

    assert await repo.renew_request_lease(user_id, request_id, stale_token) is False
    assert (
        await repo.mark_request_completed(
            user_id, request_id, result={"map_version": 1}, owner_token=stale_token
        )
        is None
    )
    assert (
        await repo.mark_request_failed(
            user_id, request_id, error={"code": "llm_error"}, owner_token=stale_token
        )
        is None
    )

    # 현재 주인은 정상적으로 쓸 수 있다.
    assert await repo.renew_request_lease(user_id, request_id, fresh_token) is True
    assert (
        await repo.mark_request_completed(
            user_id, request_id, result={"map_version": 43}, owner_token=fresh_token
        )
        is not None
    )


@pytest.mark.asyncio
async def test_expiry_clears_owner_token(clean_db, user_id):
    """만료 정리는 실행권을 회수한다. 옛 worker 의 뒤늦은 쓰기를 막는다."""
    repo = ExperienceMapRepository(clean_db, lease_seconds=-1)
    session = await repo.get_or_create_session(user_id)
    request_id = new_request_id()
    claimed = await repo.claim_request(user_id, session.session_id, request_id, HASH_A)
    stale_token = claimed.request.owner_token

    await repo.expire_stale_running_requests()

    assert (await repo.get_request(user_id, request_id)).owner_token is None
    assert await repo.renew_request_lease(user_id, request_id, stale_token) is False
