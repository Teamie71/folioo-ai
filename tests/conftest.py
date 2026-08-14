"""테스트 공용 fixture

경험정리 DB fixture 는 `tests/test_features` 와 `tests/test_app` 양쪽에서 쓰므로
루트 conftest 에 둔다.

**실제 PostgreSQL에 붙어서 돌립니다.** 동시성(세션당 running 1건)과 lease 만료는
asyncpg를 mock으로 감싸면 아무것도 검증하지 못합니다.

DB 접속 순서:

1. `EXPMAP_TEST_DATABASE_URL`
2. `DATABASE_URL` (로컬 개발 클러스터)

둘 다 붙지 않으면 **skip** 합니다. 로컬에서 DB 없이 나머지 테스트만 돌릴 수
있게 하기 위한 것입니다. 로컬 세팅은
`docs/architecture/experience-map-local-dev.md` 를 보세요.

단, `EXPMAP_REQUIRE_DB` 가 켜져 있으면 skip 하지 않고 **실패**합니다. CI 는 이
값을 켜고 돌립니다 — 자세한 이유는 `_db_unavailable` 주석을 보세요.

각 테스트는 **테스트 전용 user_id 대역(9_000_000+)** 을 써서 개발 중인 데이터와
섞이지 않게 합니다.

풀은 테스트마다 새로 엽니다. pytest-asyncio의 이벤트 루프가 테스트 단위라
session 스코프 풀을 공유하면 커넥션이 다른 루프에 묶입니다.
"""

import os
import pathlib
from typing import NoReturn

import asyncpg
import pytest
import pytest_asyncio

from features.experience_map.map_context import ExperienceMapSnapshot, build_map_snapshot

try:
    from dotenv import load_dotenv

    # 로컬 개발 DB 주소는 .env 에 있다. pytest 는 이를 자동으로 읽지 않는다.
    load_dotenv()
except ImportError:
    pass

SCHEMA_SQL = pathlib.Path(__file__).resolve().parents[1] / "scripts/experience_map/schema.sql"

# 개발 데이터와 겹치지 않는 대역
TEST_USER_ID_BASE = 9_000_000

REQUIRED_TABLES = ("ai_experience_session", "ai_experience_request")

# 켜져 있으면 DB 를 못 쓸 때 skip 대신 실패한다.
REQUIRE_DB_ENV = "EXPMAP_REQUIRE_DB"


def _db_unavailable(reason: str) -> NoReturn:
    """DB 를 못 쓸 때의 처리. 기본은 skip, `EXPMAP_REQUIRE_DB` 면 실패.

    **skip 은 초록불로 보입니다.** 세션당 running 1건·lease 만료·재시도 원자성
    테스트가 전부 이 fixture 에 걸려 있어서, DB 없이 돌리면 60여 개를 건너뛴 채
    "통과" 로 끝납니다. 정작 검증이 필요한 동시성 부분만 빠지는 셈입니다.

    그래서 CI 는 이 값을 켜고 돌립니다. DB 가 안 붙거나 스키마가 안 맞으면
    조용히 넘어가지 않고 빨간불이 됩니다.
    """
    if os.getenv(REQUIRE_DB_ENV, "").strip():
        pytest.fail(
            f"{REQUIRE_DB_ENV} 가 켜져 있는데 DB 테스트를 돌릴 수 없습니다: {reason}",
            pytrace=False,
        )
    pytest.skip(reason)


# 스키마 준비는 한 번만 확인한다.
_schema_state: str | None = None


def _database_url() -> str | None:
    for name in ("EXPMAP_TEST_DATABASE_URL", "DATABASE_URL"):
        url = os.getenv(name, "").strip()
        if url:
            return url
    return None


async def _ensure_schema(pool: asyncpg.Pool) -> str | None:
    """스키마를 준비한다. 못 쓰면 skip 사유 문자열을 돌려준다."""
    global _schema_state
    if _schema_state is not None:
        return None if _schema_state == "ok" else _schema_state

    # 스키마 파일이 있으면 적용한다 (멱등). 운영 DB 에는 메인 서버 migration 이
    # 이미 만들어 두므로 파일이 없어도 테이블만 있으면 된다.
    if SCHEMA_SQL.exists():
        try:
            async with pool.acquire() as conn:
                await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - 사유는 skip 메시지로 충분하다
            _schema_state = f"스키마를 적용하지 못했습니다: {exc}"
            return _schema_state

    rows = await pool.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY($1)",
        list(REQUIRED_TABLES),
    )
    missing = sorted(set(REQUIRED_TABLES) - {row["tablename"] for row in rows})
    if missing:
        _schema_state = (
            f"경험정리 테이블이 없습니다: {', '.join(missing)}. "
            "docs/architecture/experience-map-local-dev.md 를 참고해 스키마를 만드세요."
        )
        return _schema_state

    _schema_state = "ok"
    return None


@pytest_asyncio.fixture
async def db_pool():
    """경험 맵 DB 커넥션 풀. 붙지 않으면 skip."""
    url = _database_url()
    if not url:
        _db_unavailable("DATABASE_URL 이 없습니다. 로컬 DB 세팅 후 다시 실행하세요.")

    try:
        pool = await asyncpg.create_pool(url, min_size=1, max_size=5, timeout=3)
    except Exception as exc:  # noqa: BLE001 - 접속 실패 사유는 메시지로 충분하다
        _db_unavailable(f"경험 맵 DB에 접속할 수 없습니다: {exc}")

    try:
        reason = await _ensure_schema(pool)
        if reason:
            _db_unavailable(reason)
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def clean_db(db_pool):
    """테스트 대역 데이터를 앞뒤로 지운다."""

    async def _wipe() -> None:
        await db_pool.execute(
            "DELETE FROM ai_experience_request WHERE user_id >= $1", TEST_USER_ID_BASE
        )
        await db_pool.execute(
            "DELETE FROM ai_experience_session WHERE user_id >= $1", TEST_USER_ID_BASE
        )

    await _wipe()
    yield db_pool
    await _wipe()


@pytest.fixture
def user_id(request) -> str:
    """테스트마다 다른 user_id. 서로 침범하지 않는다."""
    return str(TEST_USER_ID_BASE + (abs(hash(request.node.nodeid)) % 500_000))


@pytest.fixture
def repository_snapshot():
    """메인 서버 소유 block DDL 없이 쓸 수 있는 읽기 전용 맵 snapshot fixture."""

    async def _get_map_snapshot(_repository, _user_id: str) -> ExperienceMapSnapshot:
        return build_map_snapshot([], map_version=1)

    return _get_map_snapshot
