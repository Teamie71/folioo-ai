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
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import asyncpg

from features.experience_map.config import (
    COMPLETED_REQUEST_RETENTION_DAYS,
    LEASE_RENEW_INTERVAL_SECONDS,
    get_settings,
)
from features.experience_map.map_context import (
    ExperienceMapSnapshot,
    MapBlockRow,
    build_map_snapshot,
)

logger = logging.getLogger(__name__)

REQUEST_COLUMNS = """
    user_id, session_id, request_id, request_hash, status, failed_node,
    retryable, retry_expires_at, lease_expires_at, owner_token, base_map_version,
    committed_version, input_meta, result, suggestion, error, created_at, updated_at
"""


def _require_token(owner_token: str | None) -> uuid.UUID:
    """실행권 표식을 UUID 로 바꾼다. 없으면 거부한다.

    **선택 인자로 두면 보호가 조용히 꺼진다.** 호출부가 실수로 빠뜨려도 테스트가
    통과하고, 운영에서만 남의 결과를 덮는다.
    """
    if not owner_token:
        raise ValueError("owner_token 이 필요합니다. 실행권 없이 상태를 바꿀 수 없습니다.")
    return uuid.UUID(owner_token)


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

    async def get_map_snapshot(self, user_id: str) -> ExperienceMapSnapshot:
        """사용자의 최신 경험 맵을 읽어 LLM 안전 snapshot으로 만든다."""
        # 메인 서버 block DDL 없이 수동 UI에서 LLM 수정 흐름을 확인하는 개발 전용 경로다.
        # 테스트 UI 라우트가 아예 등록되지 않는 기본·운영 환경에서는 절대 실행되지 않는다.
        if os.getenv("EXPERIENCE_MAP_TEST_UI_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }:
            from features.experience_map.test_runtime import get_test_map_store

            return await get_test_map_store().snapshot(str(user_id))

        version = await self._pool.fetchval(
            "SELECT map_version FROM experience_map WHERE user_id = $1", int(user_id)
        )
        if version is None:
            from features.experience_map.errors import MapNotInitializedError

            raise MapNotInitializedError()
        records = await self._pool.fetch(
            """
            SELECT b.id, b.parent_id, b.level, b.kind, b.position, b.content,
                   COALESCE(b.placeholder, k.placeholder) AS placeholder,
                   k.is_text_editable, k.is_deletable
              FROM block b JOIN block_kind k ON k.kind = b.kind
             WHERE b.user_id = $1
             ORDER BY b.level, b.parent_id, b.position, b.id
            """,
            int(user_id),
        )
        rows = [
            MapBlockRow(
                block_id=str(record["id"]),
                parent_id=str(record["parent_id"]) if record["parent_id"] else None,
                level=record["level"],
                kind=record["kind"],
                position=record["position"],
                content=record["content"],
                placeholder=record["placeholder"],
                is_text_editable=record["is_text_editable"],
                is_deletable=record["is_deletable"],
            )
            for record in records
        ]
        return build_map_snapshot(rows, map_version=int(version))

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

        **한 트랜잭션 안에서** 행을 잠그고, 사유를 판정하고, 전이시킨다.
        판정과 전이가 나뉘면 그 사이에 다른 worker 가 끼어들어 **실제와 다른
        사유**를 사용자에게 보여줄 수 있다 — 이미 성공했는데 "만료됐다"고
        하는 식이다.

        Returns:
            ClaimResult: `CLAIMED` 면 새 `owner_token` 이 담겨 있다
        """
        async with self._pool.acquire() as conn, conn.transaction():
            # 행을 잠근 뒤 판정한다. 이 트랜잭션이 끝날 때까지 아무도 못 바꾼다.
            existing = await conn.fetchrow(
                f"SELECT {REQUEST_COLUMNS} FROM ai_experience_request "
                "WHERE user_id = $1 AND request_id = $2 FOR UPDATE",
                int(user_id),
                uuid.UUID(request_id),
            )

            if existing is None:
                return ClaimResult(ClaimOutcome.RETRY_NOT_FOUND)

            row = RequestRow.from_record(existing)
            if row.status == "completed":
                return ClaimResult(ClaimOutcome.REPLAY, row)
            if row.status == "running":
                return ClaimResult(ClaimOutcome.SESSION_BUSY, row)
            if not row.retryable:
                return ClaimResult(ClaimOutcome.RETRY_NOT_ALLOWED, row)
            if row.retry_expires_at is not None and row.retry_expires_at <= datetime.now(UTC):
                return ClaimResult(ClaimOutcome.RETRY_EXPIRED, row)

            try:
                # partial unique index 위반은 PostgreSQL 트랜잭션 전체를 abort 시킨다.
                # savepoint 안에서만 UPDATE를 실행해야 예외를 SESSION_BUSY로 바꾼 뒤
                # 바깥 트랜잭션이 정상 종료할 수 있다.
                async with conn.transaction():
                    record = await conn.fetchrow(
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
                     RETURNING {REQUEST_COLUMNS}
                        """,
                        int(user_id),
                        uuid.UUID(request_id),
                        self._lease_seconds,
                        uuid.uuid4(),
                    )
            except asyncpg.UniqueViolationError:
                # 같은 세션의 다른 요청이 running 이다. 세션당 1건 제약을 그대로 쓴다.
                return ClaimResult(ClaimOutcome.SESSION_BUSY, row)

            return ClaimResult(ClaimOutcome.CLAIMED, RequestRow.from_record(record))

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

    async def renew_request_lease(self, user_id: str, request_id: str, owner_token: str) -> bool:
        """lease를 연장한다.

        **`owner_token` 은 필수다.** 행이 `running` 인 것과 내가 그 running 의
        주인인 것은 다르다 — 만료 정리 뒤 다른 worker 가 재시도로 가져갔다면
        행은 `running` 이지만 내 것이 아니다.

        Returns:
            bool: 연장했으면 `True`. 아니면 `False` — 호출자는 실행을 중단해야 한다

        Raises:
            ValueError: `owner_token` 이 비어 있음
        """
        token = _require_token(owner_token)
        result = await self._pool.execute(
            """
            UPDATE ai_experience_request
               SET lease_expires_at = now() + make_interval(secs => $3),
                   updated_at = now()
             WHERE user_id = $1 AND request_id = $2 AND status = 'running'
               AND owner_token = $4::uuid
            """,
            int(user_id),
            uuid.UUID(request_id),
            self._lease_seconds,
            token,
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
        owner_token: str,
    ) -> RequestRow | None:
        """요청을 완료로 저장한다. lease를 비워 정리 대상에서 제외한다.

        **실행권을 가진 worker 만 쓸 수 있다.** `owner_token` 이 맞지 않으면
        `None` 을 돌려주고 아무것도 바꾸지 않는다. lease 를 잃은 worker 가 뒤늦게
        끝나서 다른 worker 의 결과를 덮는 것을 막는다.

        Raises:
            ValueError: `owner_token` 이 비어 있음
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
               AND owner_token = $6::uuid
         RETURNING {REQUEST_COLUMNS}
            """,
            int(user_id),
            uuid.UUID(request_id),
            json.dumps(result, ensure_ascii=False) if result is not None else None,
            json.dumps(suggestion, ensure_ascii=False) if suggestion is not None else None,
            committed_version,
            _require_token(owner_token),
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
        owner_token: str,
    ) -> RequestRow | None:
        """요청을 실패로 저장한다.

        `retryable`이면 재시도 TTL을 함께 건다. 프론트는 `failed` + `retryable`
        일 때만 재시도 버튼을 노출한다.

        완료 처리와 같은 소유권 규칙을 따른다. `owner_token` 이 맞지 않으면
        `None` 을 돌려주고 아무것도 바꾸지 않는다.

        Raises:
            ValueError: `owner_token` 이 비어 있음
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
               AND owner_token = $7::uuid
         RETURNING {REQUEST_COLUMNS}
            """,
            int(user_id),
            uuid.UUID(request_id),
            json.dumps(error, ensure_ascii=False),
            failed_node,
            retryable,
            get_settings().retry_ttl_seconds,
            _require_token(owner_token),
        )
        if record is None:
            logger.warning("실패 처리를 건너뜁니다 — 실행권이 없습니다 (request_id=%s)", request_id)
            return None
        return RequestRow.from_record(record)

    # ===== 정리 =====

    async def claim_expired_request_for_recovery(
        self, session_id: str | None = None
    ) -> RequestRow | None:
        """만료된 요청 하나의 복구 실행권을 원자적으로 가져온다.

        메인 서버의 ``GET /commit/{request_id}`` 조회는 DB 트랜잭션 밖에서 해야
        한다. 먼저 이 메서드로 새 lease와 owner token을 발급하면, 여러 worker가
        동시에 정리해도 한 worker만 해당 요청의 조회·마감을 수행한다.
        """
        token = uuid.uuid4()
        record = await self._pool.fetchrow(
            f"""
            WITH candidate AS (
                SELECT ctid
                  FROM ai_experience_request
                 WHERE status = 'running'
                   AND lease_expires_at IS NOT NULL
                   AND lease_expires_at < now()
                   AND ($1::uuid IS NULL OR session_id = $1::uuid)
                 ORDER BY lease_expires_at
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE ai_experience_request AS request
               SET owner_token = $2::uuid,
                   lease_expires_at = now() + make_interval(secs => $3),
                   updated_at = now()
             WHERE request.ctid = (SELECT ctid FROM candidate)
         RETURNING {REQUEST_COLUMNS}
            """,
            uuid.UUID(session_id) if session_id else None,
            token,
            self._lease_seconds,
        )
        return RequestRow.from_record(record) if record else None

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
        owner_token: str,
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
