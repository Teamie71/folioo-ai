"""카톡 턴 접수·실행 상태 저장소 (`ai_kakao_turn`)

모든 상태 전이는 **조건부 UPDATE 하나**로 처리한다. 접수 중복, worker 실행권, 콜백
전송 소유권 모두 DB가 경합을 가린다 — 프로세스 메모리에는 상태를 두지 않는다.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import asyncpg

from features.experience_map.kakao.config import RETENTION_HOURS, WORKER_LEASE_SECONDS

logger = logging.getLogger(__name__)

TURN_COLUMNS = """
    request_id, user_id, session_id, block_id, request_hash, utterance, callback_url,
    expires_at, state, outcome, delivery_status, reply_payload, delivery_claimed_at,
    usage_reported, complete_notified, worker_lease_expires_at, worker_token
"""


@dataclass(frozen=True)
class TurnRow:
    """`ai_kakao_turn` 한 행. `callback_url` 은 repr 에서 숨긴다."""

    request_id: str
    user_id: str
    session_id: str
    block_id: str
    request_hash: str
    utterance: str
    expires_at: datetime
    state: str
    delivery_status: str
    usage_reported: bool
    complete_notified: bool
    callback_url: str | None = None
    outcome: str | None = None
    reply_payload: dict[str, Any] | None = None
    delivery_claimed_at: datetime | None = None
    worker_lease_expires_at: datetime | None = None
    worker_token: str | None = None

    def __repr__(self) -> str:  # 콜백 URL 이 로그에 섞이지 않게 한다.
        return (
            f"TurnRow(request_id={self.request_id!r}, state={self.state!r}, "
            f"outcome={self.outcome!r}, delivery_status={self.delivery_status!r})"
        )

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> "TurnRow":
        payload = record["reply_payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return cls(
            request_id=str(record["request_id"]),
            user_id=str(record["user_id"]),
            session_id=str(record["session_id"]),
            block_id=record["block_id"],
            request_hash=record["request_hash"],
            utterance=record["utterance"],
            callback_url=record["callback_url"],
            expires_at=record["expires_at"],
            state=record["state"],
            outcome=record["outcome"],
            delivery_status=record["delivery_status"],
            reply_payload=payload,
            delivery_claimed_at=record["delivery_claimed_at"],
            usage_reported=record["usage_reported"],
            complete_notified=record["complete_notified"],
            worker_lease_expires_at=record["worker_lease_expires_at"],
            worker_token=str(record["worker_token"]) if record["worker_token"] else None,
        )


class AcceptOutcome(Enum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    """같은 request_id·같은 본문. 추가 작업 없이 202."""

    MISMATCH = "mismatch"
    """같은 request_id 에 다른 본문·사용자·세션. 409."""


class KakaoTurnStore:
    """카톡 턴 저장소"""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def accept(
        self,
        *,
        request_id: str,
        user_id: str,
        session_id: str,
        block_id: str,
        request_hash: str,
        utterance: str,
        callback_url: str,
        expires_at: datetime,
    ) -> tuple[AcceptOutcome, TurnRow]:
        """접수를 영속화한다. 같은 request_id 의 동시 접수도 한 번만 만들어진다."""
        record = await self._pool.fetchrow(
            f"""
            INSERT INTO ai_kakao_turn
                   (request_id, user_id, session_id, block_id, request_hash, utterance,
                    callback_url, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (request_id) DO NOTHING
            RETURNING {TURN_COLUMNS}
            """,
            uuid.UUID(request_id),
            int(user_id),
            uuid.UUID(session_id),
            block_id,
            request_hash,
            utterance,
            callback_url,
            expires_at,
        )
        if record is not None:
            return AcceptOutcome.CREATED, TurnRow.from_record(record)

        existing = await self._pool.fetchrow(
            f"SELECT {TURN_COLUMNS} FROM ai_kakao_turn WHERE request_id = $1",
            uuid.UUID(request_id),
        )
        if existing is None:  # 삽입 직후 정리된 극히 드문 경합. 호출자가 다시 시도한다.
            raise RuntimeError("접수 기록을 확인할 수 없습니다.")
        row = TurnRow.from_record(existing)
        same = (
            row.request_hash == request_hash
            and row.user_id == user_id
            and row.session_id == session_id
        )
        return (AcceptOutcome.DUPLICATE if same else AcceptOutcome.MISMATCH), row

    async def get(self, request_id: str, user_id: str | None = None) -> TurnRow | None:
        """request_id 로 조회한다. `user_id` 를 주면 일치할 때만 반환한다."""
        record = await self._pool.fetchrow(
            f"""
            SELECT {TURN_COLUMNS} FROM ai_kakao_turn
             WHERE request_id = $1 AND ($2::integer IS NULL OR user_id = $2)
            """,
            uuid.UUID(request_id),
            int(user_id) if user_id is not None else None,
        )
        return TurnRow.from_record(record) if record else None

    # ===== worker 실행권 =====

    async def claim_worker(self, request_id: str) -> TurnRow | None:
        """실행권을 원자적으로 얻는다. 살아 있는 다른 worker 가 있으면 `None`.

        ACCEPTED 는 RUNNING 으로 올린다. 이미 종결된 턴은 잡지 않는다.
        """
        record = await self._pool.fetchrow(
            f"""
            UPDATE ai_kakao_turn
               SET state = CASE WHEN state = 'ACCEPTED' THEN 'RUNNING' ELSE state END,
                   worker_token = $2,
                   worker_lease_expires_at = now() + make_interval(secs => $3),
                   updated_at = now()
             WHERE request_id = $1
               AND (state IN ('ACCEPTED', 'RUNNING', 'COMMIT_UNKNOWN') OR complete_notified = false)
               AND (worker_lease_expires_at IS NULL OR worker_lease_expires_at < now())
         RETURNING {TURN_COLUMNS}
            """,
            uuid.UUID(request_id),
            uuid.uuid4(),
            WORKER_LEASE_SECONDS,
        )
        return TurnRow.from_record(record) if record else None

    async def renew_worker(self, request_id: str, worker_token: str) -> bool:
        """실행권을 연장한다. 잃었으면 `False`."""
        result = await self._pool.execute(
            """
            UPDATE ai_kakao_turn
               SET worker_lease_expires_at = now() + make_interval(secs => $3),
                   updated_at = now()
             WHERE request_id = $1 AND worker_token = $2
            """,
            uuid.UUID(request_id),
            uuid.UUID(worker_token),
            WORKER_LEASE_SECONDS,
        )
        return result.endswith(" 1")

    async def release_worker(self, request_id: str, worker_token: str) -> None:
        """실행권을 반납한다. 다음 복구 스캔이 바로 이어받을 수 있다."""
        await self._pool.execute(
            """
            UPDATE ai_kakao_turn
               SET worker_lease_expires_at = NULL, worker_token = NULL, updated_at = now()
             WHERE request_id = $1 AND worker_token = $2
            """,
            uuid.UUID(request_id),
            uuid.UUID(worker_token),
        )

    # ===== 결과·전달·정리 =====

    async def record_outcome(
        self,
        request_id: str,
        worker_token: str,
        *,
        outcome: str,
        reply_payload: dict[str, Any] | None,
    ) -> bool:
        """턴 결과와 콜백 본문을 먼저 남긴다. 공개 `state` 는 아직 바꾸지 않는다.

        이미 결과가 있으면 덮지 않는다 (결과를 서로 뒤집지 않는다).
        """
        result = await self._pool.execute(
            """
            UPDATE ai_kakao_turn
               SET outcome = $3, reply_payload = $4::jsonb, updated_at = now()
             WHERE request_id = $1 AND worker_token = $2 AND outcome IS NULL
            """,
            uuid.UUID(request_id),
            uuid.UUID(worker_token),
            outcome,
            json.dumps(reply_payload, ensure_ascii=False) if reply_payload is not None else None,
        )
        return result.endswith(" 1")

    async def resolve_commit_unknown(
        self, request_id: str, worker_token: str, outcome: str
    ) -> bool:
        """확인된 커밋 결과로 COMMIT_UNKNOWN 을 확정한다.

        한도 정리와 완료 통지를 확정 결과 기준으로 다시 하도록 플래그를 되돌린다.
        콜백은 이미 한 번 소비됐으므로 다시 보내지 않는다.
        """
        result = await self._pool.execute(
            """
            UPDATE ai_kakao_turn
               SET outcome = $3, state = $3, usage_reported = false,
                   complete_notified = false, updated_at = now()
             WHERE request_id = $1 AND worker_token = $2 AND state = 'COMMIT_UNKNOWN'
            """,
            uuid.UUID(request_id),
            uuid.UUID(worker_token),
            outcome,
        )
        return result.endswith(" 1")

    async def claim_delivery(self, request_id: str) -> str | None:
        """콜백 전송 소유권을 원자적으로 가져오고 URL 을 돌려준다.

        worker·기한 처리·재시작 복구 중 **먼저 채운 쪽만** 전송한다. 이미 소유권이
        있거나 URL 이 비었으면 `None`.
        """
        return await self._pool.fetchval(
            """
            UPDATE ai_kakao_turn
               SET delivery_claimed_at = now(), updated_at = now()
             WHERE request_id = $1 AND delivery_claimed_at IS NULL AND callback_url IS NOT NULL
         RETURNING callback_url
            """,
            uuid.UUID(request_id),
        )

    async def finish_delivery(self, request_id: str, delivery_status: str) -> None:
        """전달 시도 결과를 남기고 1회용 URL 을 즉시 비운다.

        `delivery_claimed_at` 도 채워 이후 누구도 다시 전송하지 않게 한다 (기한 때문에
        시도조차 못 한 NOT_ATTEMPTED 포함).
        """
        await self._pool.execute(
            """
            UPDATE ai_kakao_turn
               SET delivery_status = $2,
                   callback_url = NULL,
                   delivery_claimed_at = COALESCE(delivery_claimed_at, now()),
                   updated_at = now()
             WHERE request_id = $1
            """,
            uuid.UUID(request_id),
            delivery_status,
        )

    async def publish_state(self, request_id: str, state: str) -> None:
        """공개 상태를 정한다. 종결 상태는 뒤집지 않는다."""
        await self._pool.execute(
            """
            UPDATE ai_kakao_turn
               SET state = $2, updated_at = now()
             WHERE request_id = $1
               AND (state IN ('ACCEPTED', 'RUNNING', 'COMMIT_UNKNOWN'))
            """,
            uuid.UUID(request_id),
            state,
        )

    async def mark_usage_reported(self, request_id: str) -> None:
        await self._pool.execute(
            "UPDATE ai_kakao_turn SET usage_reported = true, updated_at = now() "
            "WHERE request_id = $1",
            uuid.UUID(request_id),
        )

    async def mark_complete_notified(self, request_id: str) -> None:
        await self._pool.execute(
            "UPDATE ai_kakao_turn SET complete_notified = true, updated_at = now() "
            "WHERE request_id = $1",
            uuid.UUID(request_id),
        )

    async def list_recoverable(self, limit: int = 20) -> list[str]:
        """이어받아야 할 턴의 request_id.

        worker 실행권이 없거나 만료된 미종결 턴, 종결됐지만 완료 통지·한도 정리가 남은
        턴이다. COMMIT_UNKNOWN 도 결과 확인을 위해 포함한다.
        """
        records = await self._pool.fetch(
            """
            SELECT request_id FROM ai_kakao_turn
             WHERE (state IN ('ACCEPTED', 'RUNNING', 'COMMIT_UNKNOWN') OR complete_notified = false)
               AND (worker_lease_expires_at IS NULL OR worker_lease_expires_at < now())
             ORDER BY updated_at
             LIMIT $1
            """,
            limit,
        )
        return [str(r["request_id"]) for r in records]

    async def purge_old(self) -> int:
        """보존 기간이 지난 종결 기록을 지운다. 정리가 끝난 것만 지운다."""
        result = await self._pool.execute(
            """
            DELETE FROM ai_kakao_turn
             WHERE complete_notified = true
               AND state IN ('SUCCEEDED', 'FAILED', 'EXPIRED')
               AND updated_at < now() - make_interval(hours => $1)
            """,
            RETENTION_HOURS,
        )
        return int(result.split()[-1])


_store: KakaoTurnStore | None = None


def init_store(pool: asyncpg.Pool) -> KakaoTurnStore:
    global _store
    _store = KakaoTurnStore(pool)
    return _store


def get_store() -> KakaoTurnStore:
    if _store is None:
        raise RuntimeError("카톡 턴 저장소가 초기화되지 않았습니다.")
    return _store


def set_store(store: KakaoTurnStore | None) -> None:
    """저장소 주입 (테스트·종료용)"""
    global _store
    _store = store
