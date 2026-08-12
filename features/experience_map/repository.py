"""경험정리 세션·요청 Repository (asyncpg 직접 쿼리)

**`ai_experience_request`가 API 상태의 유일한 기준입니다** (에이전트 문서 7-3).
checkpoint status를 API 상태로 계산하지 않습니다. 둘이 어긋나면 사용자에게는
"실패했는데 재시도 버튼이 없는" 상태가 보입니다.

동시성은 DB가 막습니다.

- 세션당 running 요청 1건 — `uq_ai_experience_request_running` partial unique index
- 여러 worker가 같은 세션을 동시에 claim하면 하나만 INSERT에 성공합니다

시각은 전부 **DB의 `now()`** 를 씁니다. worker마다 시계가 다르면 lease 만료 판정이
갈립니다.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import asyncpg

from features.experience_map.config import (
    COMPLETED_REQUEST_RETENTION_DAYS,
    LEASE_RENEW_INTERVAL_SECONDS,
    get_settings,
)

logger = logging.getLogger(__name__)

REQUEST_COLUMNS = """
    user_id, session_id, request_id, request_hash, status, failed_node,
    retryable, retry_expires_at, lease_expires_at, owner_token, base_map_version,
    committed_version, input_meta, result, suggestion, error, created_at, updated_at
"""


def _as_dict(value: Any) -> dict[str, Any] | None:
    """asyncpg가 jsonb를 문자열로 돌려주는 경우를 흡수한다."""
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


@dataclass(frozen=True)
class SessionRow:
    """`ai_experience_session` 한 행"""

    user_id: str
    session_id: str
    active_gap: dict[str, Any] | None

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> "SessionRow":
        return cls(
            user_id=str(record["user_id"]),
            session_id=str(record["session_id"]),
            active_gap=_as_dict(record["active_gap"]),
        )


@dataclass(frozen=True)
class RequestRow:
    """`ai_experience_request` 한 행"""

    user_id: str
    session_id: str
    request_id: str
    request_hash: str
    status: str
    failed_node: str | None = None
    retryable: bool = False
    retry_expires_at: datetime | None = None
    lease_expires_at: datetime | None = None
    owner_token: str | None = None
    """현재 실행권을 가진 worker 의 표식. 상태를 바꾸려면 이 값을 알아야 한다."""

    base_map_version: int | None = None
    committed_version: int | None = None
    input_meta: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    suggestion: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> "RequestRow":
        return cls(
            user_id=str(record["user_id"]),
            session_id=str(record["session_id"]),
            request_id=str(record["request_id"]),
            request_hash=record["request_hash"],
            status=record["status"],
            failed_node=record["failed_node"],
            retryable=record["retryable"],
            retry_expires_at=record["retry_expires_at"],
            lease_expires_at=record["lease_expires_at"],
            owner_token=str(record["owner_token"]) if record["owner_token"] else None,
            base_map_version=record["base_map_version"],
            committed_version=record["committed_version"],
            input_meta=_as_dict(record["input_meta"]),
            result=_as_dict(record["result"]),
            suggestion=_as_dict(record["suggestion"]),
            error=_as_dict(record["error"]),
        )


class ClaimOutcome(Enum):
    """`claim_request` 결과. 호출자가 HTTP 응답을 결정한다."""

    CLAIMED = "claimed"
    """새 요청을 잡았다. 그래프를 실행한다."""

    REPLAY = "replay"
    """같은 요청이 이미 완료됐다. 저장된 이벤트를 재전송한다 (API 명세 2-5)."""

    SESSION_BUSY = "session_busy"
    """이 세션에 실행 중인 요청이 있다. `409 session_busy`."""

    HASH_MISMATCH = "hash_mismatch"
    """같은 `request_id`에 다른 입력이다. `409 idempotency_key_reused`."""

    RETRY_REQUIRED = "retry_required"
    """같은 요청이 실패 상태다. chat이 아니라 retry API를 쓴다. `409 retry_not_allowed`."""

    RETRY_NOT_ALLOWED = "retry_not_allowed"
    """재시도 대상이 아니다 (실패가 아니거나 이미 재시도 자격을 잃었다)."""

    RETRY_EXPIRED = "retry_expired"
    """재시도 TTL 이 지났다. `410 retry_expired`."""

    RETRY_NOT_FOUND = "retry_not_found"
    """요청 자체가 없다. `404 request_not_found`."""


@dataclass(frozen=True)
class ClaimResult:
    outcome: ClaimOutcome
    request: RequestRow | None = None


class ExperienceMapRepository:
    """세션·요청 상태 저장소"""

    def __init__(self, pool: asyncpg.Pool, *, lease_seconds: int | None = None) -> None:
        self._pool = pool
        self._lease_seconds = (
            lease_seconds if lease_seconds is not None else get_settings().request_lease_seconds
        )

    # ===== 세션 =====

    async def get_or_create_session(self, user_id: str) -> SessionRow:
        """사용자의 세션을 반환한다. 없으면 만든다.

        사용자당 세션은 1개다. 여러 worker가 동시에 호출해도 `ON CONFLICT`로
        하나만 삽입되고 나머지는 기존 행을 받는다.
        """
        record = await self._pool.fetchrow(
            """
            INSERT INTO ai_experience_session (user_id, session_id)
                 VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
                    SET updated_at = now()
              RETURNING user_id, session_id, active_gap
            """,
            int(user_id),
            uuid.uuid4(),
        )
        return SessionRow.from_record(record)

    async def get_session(self, user_id: str, session_id: str) -> SessionRow | None:
        """세션을 조회한다. **소유자가 아니면 `None`이다.**"""
        record = await self._pool.fetchrow(
            """
            SELECT user_id, session_id, active_gap
              FROM ai_experience_session
             WHERE user_id = $1 AND session_id = $2
            """,
            int(user_id),
            uuid.UUID(session_id),
        )
        return SessionRow.from_record(record) if record else None

    async def save_active_gap(self, user_id: str, gap: dict[str, Any] | None) -> None:
        """직전 턴의 gap을 저장한다. gap이 없으면 `None`으로 비운다 (5-10)."""
        await self._pool.execute(
            """
            UPDATE ai_experience_session
               SET active_gap = $2::jsonb, updated_at = now()
             WHERE user_id = $1
            """,
            int(user_id),
            json.dumps(gap, ensure_ascii=False) if gap is not None else None,
        )

    # ===== 요청 =====

    async def claim_request(
        self,
        user_id: str,
        session_id: str,
        request_id: str,
        request_hash: str,
        input_meta: dict[str, Any] | None = None,
    ) -> ClaimResult:
        """요청을 원자적으로 잡는다.

        멱등성 규칙은 API 명세 2-5를 따른다. 같은 `request_id`가 이미 있으면
        hash와 status로 결과가 갈린다.

        Returns:
            ClaimResult: `outcome`이 `CLAIMED`일 때만 그래프를 실행한다
        """
        # 만료된 running 요청이 남아 있으면 새 요청을 영원히 막는다 (명세 3-3).
        await self.expire_stale_running_requests(session_id=session_id)

        existing = await self.get_request(user_id, request_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                return ClaimResult(ClaimOutcome.HASH_MISMATCH, existing)
            if existing.status == "completed":
                return ClaimResult(ClaimOutcome.REPLAY, existing)
            if existing.status == "failed":
                return ClaimResult(ClaimOutcome.RETRY_REQUIRED, existing)
            return ClaimResult(ClaimOutcome.SESSION_BUSY, existing)

        try:
            record = await self._pool.fetchrow(
                f"""
                INSERT INTO ai_experience_request
                            (user_id, session_id, request_id, request_hash, status,
                             input_meta, lease_expires_at, owner_token)
                     VALUES ($1, $2, $3, $4, 'running',
                             $5::jsonb, now() + make_interval(secs => $6), $7)
                  RETURNING {REQUEST_COLUMNS}
                """,
                int(user_id),
                uuid.UUID(session_id),
                uuid.UUID(request_id),
                request_hash,
                json.dumps(input_meta or {}, ensure_ascii=False),
                self._lease_seconds,
                uuid.uuid4(),
            )
        except asyncpg.UniqueViolationError:
            # partial unique index 가 세션의 두 번째 running 을 막았다.
            return ClaimResult(ClaimOutcome.SESSION_BUSY)

        # 새 요청이 시작되면 이전 실패 요청은 더 이상 재시도할 수 없다 (9절 4번).
        await self._disable_previous_retries(user_id, session_id, request_id)

        return ClaimResult(ClaimOutcome.CLAIMED, RequestRow.from_record(record))

    async def retry_request(self, user_id: str, request_id: str) -> ClaimResult:
        """실패한 요청을 **원자적으로** `running` 으로 되돌린다.

        `claim_request()` 로는 할 수 없다. 그쪽은 기존 `failed` 행에 `RETRY_REQUIRED`
        를 돌려줄 뿐 행을 바꾸지 않아서, 재시도가 `failed` 인 채로 실행되고 lease
        갱신도 동시 재시도 차단도 동작하지 않는다.

        조건 검사와 상태 전이를 **하나의 UPDATE** 로 처리한다. 따로 SELECT 해서
        확인하면 그 사이에 다른 worker 가 끼어들 수 있다.

        Returns:
            ClaimResult: `CLAIMED` 면 새 `owner_token` 이 담겨 있다
        """
        try:
            record = await self._pool.fetchrow(
                f"""
                UPDATE ai_experience_request
                   SET status = 'running',
                       lease_expires_at = now() + make_interval(secs => $3),
                       owner_token = $4,
                       error = NULL,
                       failed_node = NULL,
                       retryable = false,
                       retry_expires_at = NULL,
                       updated_at = now()
                 WHERE user_id = $1 AND request_id = $2
                   AND status = 'failed'
                   AND retryable
                   AND (retry_expires_at IS NULL OR retry_expires_at > now())
             RETURNING {REQUEST_COLUMNS}
                """,
                int(user_id),
                uuid.UUID(request_id),
                self._lease_seconds,
                uuid.uuid4(),
            )
        except asyncpg.UniqueViolationError:
            # 같은 세션에 이미 running 이 있다. 세션당 1건 제약을 그대로 쓴다.
            return ClaimResult(ClaimOutcome.SESSION_BUSY)

        if record is not None:
            return ClaimResult(ClaimOutcome.CLAIMED, RequestRow.from_record(record))

        # 0행이다. 사유를 나누기 위해서만 조회한다 (경쟁에 민감한 경로가 아니다).
        existing = await self.get_request(user_id, request_id)
        if existing is None:
            return ClaimResult(ClaimOutcome.RETRY_NOT_FOUND)
        if existing.status == "completed":
            return ClaimResult(ClaimOutcome.REPLAY, existing)
        if existing.status == "running":
            return ClaimResult(ClaimOutcome.SESSION_BUSY, existing)
        if existing.retryable and existing.retry_expires_at is not None:
            return ClaimResult(ClaimOutcome.RETRY_EXPIRED, existing)
        return ClaimResult(ClaimOutcome.RETRY_NOT_ALLOWED, existing)

    async def get_request(self, user_id: str, request_id: str) -> RequestRow | None:
        """요청을 조회한다. **소유자가 아니면 `None`이다.**"""
        record = await self._pool.fetchrow(
            f"SELECT {REQUEST_COLUMNS} FROM ai_experience_request "
            "WHERE user_id = $1 AND request_id = $2",
            int(user_id),
            uuid.UUID(request_id),
        )
        return RequestRow.from_record(record) if record else None

    async def get_latest_request(self, user_id: str, session_id: str) -> RequestRow | None:
        """세션의 마지막 요청. `GET /state`와 재시도 허용 판정에 쓴다."""
        record = await self._pool.fetchrow(
            f"SELECT {REQUEST_COLUMNS} FROM ai_experience_request "
            "WHERE user_id = $1 AND session_id = $2 ORDER BY created_at DESC LIMIT 1",
            int(user_id),
            uuid.UUID(session_id),
        )
        return RequestRow.from_record(record) if record else None

    async def renew_request_lease(
        self, user_id: str, request_id: str, owner_token: str | None = None
    ) -> bool:
        """lease를 연장한다.

        `owner_token` 을 주면 **내가 실행권의 주인일 때만** 연장한다. 행이
        `running` 인 것과 내가 그 running 의 주인인 것은 다르다 — 만료 정리 뒤
        다른 worker 가 재시도로 가져갔다면 행은 `running` 이지만 내 것이 아니다.

        Returns:
            bool: 연장했으면 `True`. 아니면 `False` — 호출자는 실행을 중단해야 한다
        """
        result = await self._pool.execute(
            """
            UPDATE ai_experience_request
               SET lease_expires_at = now() + make_interval(secs => $3),
                   updated_at = now()
             WHERE user_id = $1 AND request_id = $2 AND status = 'running'
               AND ($4::uuid IS NULL OR owner_token = $4::uuid)
            """,
            int(user_id),
            uuid.UUID(request_id),
            self._lease_seconds,
            uuid.UUID(owner_token) if owner_token else None,
        )
        return result.endswith(" 1")

    async def mark_request_completed(
        self,
        user_id: str,
        request_id: str,
        *,
        result: dict[str, Any] | None = None,
        suggestion: dict[str, Any] | None = None,
        committed_version: int | None = None,
        owner_token: str | None = None,
    ) -> RequestRow | None:
        """요청을 완료로 저장한다. lease를 비워 정리 대상에서 제외한다.

        **실행권을 가진 worker 만 쓸 수 있다.** `owner_token` 이 맞지 않으면
        `None` 을 돌려주고 아무것도 바꾸지 않는다. lease 를 잃은 worker 가 뒤늦게
        끝나서 다른 worker 의 결과를 덮는 것을 막는다.
        """
        record = await self._pool.fetchrow(
            f"""
            UPDATE ai_experience_request
               SET status = 'completed',
                   result = COALESCE($3::jsonb, result),
                   suggestion = COALESCE($4::jsonb, suggestion),
                   committed_version = COALESCE($5, committed_version),
                   retryable = false,
                   lease_expires_at = NULL,
                   owner_token = NULL,
                   updated_at = now()
             WHERE user_id = $1 AND request_id = $2
               AND status = 'running'
               AND ($6::uuid IS NULL OR owner_token = $6::uuid)
         RETURNING {REQUEST_COLUMNS}
            """,
            int(user_id),
            uuid.UUID(request_id),
            json.dumps(result, ensure_ascii=False) if result is not None else None,
            json.dumps(suggestion, ensure_ascii=False) if suggestion is not None else None,
            committed_version,
            uuid.UUID(owner_token) if owner_token else None,
        )
        if record is None:
            logger.warning("완료 처리를 건너뜁니다 — 실행권이 없습니다 (request_id=%s)", request_id)
            return None
        return RequestRow.from_record(record)

    async def mark_request_failed(
        self,
        user_id: str,
        request_id: str,
        *,
        error: dict[str, Any],
        failed_node: str | None = None,
        retryable: bool = True,
        owner_token: str | None = None,
    ) -> RequestRow | None:
        """요청을 실패로 저장한다.

        `retryable`이면 재시도 TTL을 함께 건다. 프론트는 `failed` + `retryable`
        일 때만 재시도 버튼을 노출한다.

        완료 처리와 같은 소유권 규칙을 따른다. `owner_token` 이 맞지 않으면
        `None` 을 돌려주고 아무것도 바꾸지 않는다.
        """
        record = await self._pool.fetchrow(
            f"""
            UPDATE ai_experience_request
               SET status = 'failed',
                   error = $3::jsonb,
                   failed_node = $4,
                   retryable = $5,
                   retry_expires_at = CASE WHEN $5
                        THEN now() + make_interval(secs => $6) ELSE NULL END,
                   lease_expires_at = NULL,
                   owner_token = NULL,
                   updated_at = now()
             WHERE user_id = $1 AND request_id = $2
               AND status = 'running'
               AND ($7::uuid IS NULL OR owner_token = $7::uuid)
         RETURNING {REQUEST_COLUMNS}
            """,
            int(user_id),
            uuid.UUID(request_id),
            json.dumps(error, ensure_ascii=False),
            failed_node,
            retryable,
            get_settings().retry_ttl_seconds,
            uuid.UUID(owner_token) if owner_token else None,
        )
        if record is None:
            logger.warning("실패 처리를 건너뜁니다 — 실행권이 없습니다 (request_id=%s)", request_id)
            return None
        return RequestRow.from_record(record)

    # ===== 정리 =====

    async def expire_stale_running_requests(
        self, session_id: str | None = None
    ) -> list[RequestRow]:
        """lease가 지난 running 요청을 failed로 전환한다.

        프로세스가 죽어 lease를 갱신하지 못한 요청을 풀어 준다. 이렇게 하지
        않으면 partial unique index 때문에 그 세션이 영구히 잠긴다.

        > ⚠️ 태스크 3.22에서 **이 전환 전에 `GET /commit/{request_id}`로 커밋
        > 여부를 먼저 확인**하도록 바꾼다 (API 명세 4-3). 커밋은 성공했는데
        > 응답만 유실된 요청을 failed로 만들면 사용자가 같은 내용을 두 번
        > 커밋하게 된다.
        """
        records = await self._pool.fetch(
            f"""
            UPDATE ai_experience_request
               SET status = 'failed',
                   retryable = true,
                   retry_expires_at = now() + make_interval(secs => $2),
                   error = jsonb_build_object(
                       'code', 'lease_expired',
                       'message', '요청이 중단되었습니다.'),
                   lease_expires_at = NULL,
                   -- token 을 비워 옛 worker 의 뒤늦은 쓰기를 전부 무효화한다.
                   owner_token = NULL,
                   updated_at = now()
             WHERE status = 'running'
               AND lease_expires_at IS NOT NULL
               AND lease_expires_at < now()
               AND ($1::uuid IS NULL OR session_id = $1::uuid)
         RETURNING {REQUEST_COLUMNS}
            """,
            uuid.UUID(session_id) if session_id else None,
            get_settings().retry_ttl_seconds,
        )
        if records:
            logger.info("만료된 running 요청 정리 (count=%d)", len(records))
        return [RequestRow.from_record(record) for record in records]

    async def purge_old_completed_requests(self, retention_days: int | None = None) -> int:
        """보관 기간이 지난 완료 요청을 삭제한다 (기본 30일)."""
        days = retention_days if retention_days is not None else COMPLETED_REQUEST_RETENTION_DAYS
        result = await self._pool.execute(
            """
            DELETE FROM ai_experience_request
             WHERE status IN ('completed', 'failed')
               AND updated_at < now() - make_interval(days => $1)
            """,
            days,
        )
        deleted = int(result.rsplit(" ", 1)[-1])
        if deleted:
            logger.info("오래된 요청 정리 (count=%d, days=%d)", deleted, days)
        return deleted

    async def _disable_previous_retries(
        self, user_id: str, session_id: str, current_request_id: str
    ) -> None:
        """새 요청이 시작되면 이전 실패 요청의 재시도를 막는다."""
        await self._pool.execute(
            """
            UPDATE ai_experience_request
               SET retryable = false, retry_expires_at = NULL, updated_at = now()
             WHERE user_id = $1 AND session_id = $2 AND request_id <> $3
               AND status = 'failed' AND retryable
            """,
            int(user_id),
            uuid.UUID(session_id),
            uuid.UUID(current_request_id),
        )


class LeaseRenewer:
    """실행 중인 요청의 lease를 주기적으로 갱신한다.

    갱신에 실패하면 **그 요청은 더 이상 우리 것이 아니다.** 다른 worker가
    가져갔거나 만료 정리에 걸린 것이므로 실행을 중단해야 한다. `lost`가 그
    신호이며, 호출자가 이를 보고 작업을 취소한다 (9절 4번).
    """

    def __init__(
        self,
        repository: ExperienceMapRepository,
        user_id: str,
        request_id: str,
        *,
        owner_token: str | None = None,
        interval_seconds: int = LEASE_RENEW_INTERVAL_SECONDS,
    ) -> None:
        self._repository = repository
        self._user_id = user_id
        self._request_id = request_id
        self._owner_token = owner_token
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self.lost = asyncio.Event()

    async def __aenter__(self) -> "LeaseRenewer":
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                renewed = await self._repository.renew_request_lease(
                    self._user_id, self._request_id, self._owner_token
                )
            except Exception:
                logger.exception("lease 갱신 실패 (request_id=%s)", self._request_id)
                self.lost.set()
                return
            if not renewed:
                logger.warning("lease 를 잃었습니다 (request_id=%s)", self._request_id)
                self.lost.set()
                return


_repository: ExperienceMapRepository | None = None


def get_repository() -> ExperienceMapRepository:
    """Repository 싱글톤 반환

    Raises:
        RuntimeError: 초기화 전에 호출된 경우
    """
    if _repository is None:
        raise RuntimeError(
            "경험정리 Repository 가 초기화되지 않았습니다. "
            "애플리케이션 시작 시 init_repository()를 호출하세요."
        )
    return _repository


def init_repository(pool: asyncpg.Pool) -> ExperienceMapRepository:
    """Repository 초기화 (앱 lifespan)"""
    global _repository
    _repository = ExperienceMapRepository(pool)
    return _repository


def set_repository(repository: ExperienceMapRepository | None) -> None:
    """Repository 주입 (테스트용)"""
    global _repository
    _repository = repository
