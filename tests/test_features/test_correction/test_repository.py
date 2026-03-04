"""첨삭 Repository 테스트"""

import pytest

from features.correction.repository import CorrectionRepository


class _DummyTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _DummyConnection:
    def __init__(self) -> None:
        self.executed_sqls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def transaction(self) -> _DummyTransaction:
        return _DummyTransaction()

    async def execute(self, sql: str, *_args) -> str:
        self.executed_sqls.append(sql)
        return "OK"


class _DummyAcquirePool:
    def __init__(self) -> None:
        self.connection = _DummyConnection()

    def acquire(self) -> _DummyConnection:
        return self.connection


class _DummyFetchRowPool:
    def __init__(self, responses: list[dict | None]) -> None:
        self._responses = responses
        self.fetchrow_sqls: list[str] = []

    async def fetchrow(self, sql: str, *_args) -> dict | None:
        self.fetchrow_sqls.append(sql)
        if self._responses:
            return self._responses.pop(0)
        return {"id": "any"}


class _DummyFetchPool:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = responses
        self.fetch_sqls: list[str] = []

    async def fetch(self, sql: str, *_args) -> list[dict]:
        self.fetch_sqls.append(sql)
        return self._responses


@pytest.mark.asyncio
async def test_setup_table_creates_pgcrypto_extension_before_tables():
    """setup_table은 pgcrypto 확장 생성 후 테이블을 만든다."""
    pool = _DummyAcquirePool()
    repo = CorrectionRepository(pool)  # type: ignore[arg-type]

    await repo.setup_table()

    assert pool.connection.executed_sqls[0].strip() == "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
    assert "CREATE TABLE IF NOT EXISTS corrections" in pool.connection.executed_sqls[1]
    assert "CREATE TABLE IF NOT EXISTS rag_data" in pool.connection.executed_sqls[2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("update_status", ("missing-id", "processing")),
        ("update_result", ("missing-id", {"fields": []})),
        ("update_company_insight", ("missing-id", "insight")),
        ("update_emphasis_points", ("missing-id", "points")),
        ("delete", ("missing-id",)),
    ],
)
async def test_update_and_delete_raise_when_correction_is_missing(method_name: str, args: tuple):
    """없는 correction_id로 수정/삭제 시 ValueError를 발생시킨다."""
    pool = _DummyFetchRowPool(responses=[None])
    repo = CorrectionRepository(pool)  # type: ignore[arg-type]

    method = getattr(repo, method_name)

    with pytest.raises(ValueError, match="존재하지 않는 첨삭 ID입니다"):
        await method(*args)

    assert "RETURNING id" in pool.fetchrow_sqls[0]


@pytest.mark.asyncio
async def test_update_result_query_does_not_update_status():
    """update_result 쿼리는 result 컬럼만 갱신한다."""
    pool = _DummyFetchRowPool(responses=[{"id": "ok"}])
    repo = CorrectionRepository(pool)  # type: ignore[arg-type]

    await repo.update_result("existing-id", {"fields": []})

    assert "status" not in pool.fetchrow_sqls[0].lower()


@pytest.mark.asyncio
async def test_get_rag_data_returns_parsed_search_results():
    """get_rag_data는 JSON 문자열 search_results를 파싱해 반환한다."""
    pool = _DummyFetchPool(
        responses=[
            {
                "id": "r-1",
                "correction_id": "c-1",
                "search_query": "회사 백엔드",
                "search_results": '{"keywords": ["키워드1", "키워드2"], "results": [{"title": "검색 결과"}]}',
                "created_at": "2026-03-04T00:00:00Z",
            }
        ]
    )
    repo = CorrectionRepository(pool)  # type: ignore[arg-type]

    rows = await repo.get_rag_data("c-1")

    assert rows[0]["search_results"]["results"][0]["title"] == "검색 결과"
    assert rows[0]["search_results"]["keywords"] == ["키워드1", "키워드2"]
    assert "FROM rag_data" in pool.fetch_sqls[0]
