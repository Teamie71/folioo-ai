"""포트폴리오 Repository 테스트"""

import pytest

from features.portfolio.repository import PortfolioRepository


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


@pytest.mark.asyncio
async def test_setup_table_creates_pgcrypto_extension_before_portfolios_table():
    """setup_table은 pgcrypto 확장 생성 후 portfolios 테이블을 만든다."""
    pool = _DummyAcquirePool()
    repo = PortfolioRepository(pool)  # type: ignore[arg-type]

    await repo.setup_table()

    assert pool.connection.executed_sqls[0].strip() == "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
    assert "CREATE TABLE IF NOT EXISTS portfolios" in pool.connection.executed_sqls[1]
