"""만료 lease 커밋 복구 서비스 단위 테스트"""

from dataclasses import replace

import pytest

from features.experience_map.main_client import CommitRecoveryResult
from features.experience_map.repository import RequestRow
from features.experience_map.schemas import CommitResult
from features.experience_map.service import ExperienceMapService


def expired_request() -> RequestRow:
    """복구 실행권을 가진 만료 요청 행을 만든다."""
    return RequestRow(
        user_id="9000001",
        session_id="550e8400-e29b-41d4-a716-446655440000",
        request_id="550e8400-e29b-41d4-a716-446655440001",
        request_hash="a" * 64,
        status="running",
        owner_token="550e8400-e29b-41d4-a716-446655440002",
    )


class RecoveryRepositoryStub:
    """복구 서비스가 쓰는 repository 표면만 제공한다."""

    def __init__(self, row: RequestRow) -> None:
        self._row = row
        self.completed: list[dict] = []
        self.failed: list[dict] = []

    async def claim_expired_request_for_recovery(self, session_id=None):
        row, self._row = self._row, None
        return row

    async def mark_request_completed(self, user_id, request_id, **kwargs):
        self.completed.append({"user_id": user_id, "request_id": request_id, **kwargs})
        return replace(expired_request(), status="completed", owner_token=None)

    async def mark_request_failed(self, user_id, request_id, **kwargs):
        self.failed.append({"user_id": user_id, "request_id": request_id, **kwargs})
        return replace(expired_request(), status="failed", owner_token=None)


class RecoveryClientStub:
    """메인 서버의 커밋 복구 결과를 고정한다."""

    def __init__(self, result: CommitRecoveryResult | Exception) -> None:
        self._result = result

    async def get_commit(self, request_id: str) -> CommitRecoveryResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.mark.asyncio
async def test_recovery_saves_main_server_commit_result():
    """커밋됨이면 새 owner token으로 completed를 저장한다."""
    row = expired_request()
    repository = RecoveryRepositoryStub(row)
    result = CommitResult(
        request_id=row.request_id,
        previous_version=3,
        map_version=4,
        revert_to_version=3,
        can_revert=True,
    )
    service = ExperienceMapService(
        repository=repository,
        main_client=RecoveryClientStub(CommitRecoveryResult(committed=True, result=result)),
    )

    await service._reconcile_stale_requests()

    assert repository.failed == []
    assert repository.completed == [
        {
            "user_id": row.user_id,
            "request_id": row.request_id,
            "result": result.model_dump(mode="json"),
            "committed_version": 4,
            "owner_token": row.owner_token,
        }
    ]


@pytest.mark.asyncio
async def test_recovery_marks_retryable_failure_when_commit_lookup_fails():
    """조회 실패도 실행권을 유지한 채 retryable failed로 마감한다."""
    row = expired_request()
    repository = RecoveryRepositoryStub(row)
    service = ExperienceMapService(
        repository=repository,
        main_client=RecoveryClientStub(RuntimeError("main server unavailable")),
    )

    await service._reconcile_stale_requests()

    assert repository.completed == []
    assert repository.failed == [
        {
            "user_id": row.user_id,
            "request_id": row.request_id,
            "error": {
                "code": "lease_expired",
                "message": "요청 연결이 끊어졌습니다. 다시 시도해 주세요.",
            },
            "retryable": True,
            "owner_token": row.owner_token,
        }
    ]
