"""경험 맵 DB 커넥션 풀 테스트"""

import pytest

from common.db import connection


class DummyPool:
    """테스트용 asyncpg 풀 더블"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def is_closing(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def clear_pool(monkeypatch):
    """테스트마다 풀 싱글톤과 관련 환경변수 초기화"""
    connection._pool = None
    for name in ("DB_POOL_MIN_SIZE", "DB_POOL_MAX_SIZE", "DB_STATEMENT_TIMEOUT_MS"):
        monkeypatch.delenv(name, raising=False)
    yield
    connection._pool = None


def _mock_create_pool(captured: dict):
    async def _factory(db_url: str, **kwargs):
        captured["db_url"] = db_url
        captured["kwargs"] = kwargs
        return DummyPool(**kwargs)

    return _factory


@pytest.mark.asyncio
async def test_create_pool_uses_database_url(monkeypatch):
    """DATABASE_URL로 경험 맵 DB 풀을 생성한다."""
    captured: dict[str, object] = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://experience-map-db")
    monkeypatch.setattr(connection.asyncpg, "create_pool", _mock_create_pool(captured))

    pool = await connection.create_pool()

    assert pool is connection.get_pool()
    assert captured["db_url"] == "postgresql://experience-map-db"


@pytest.mark.asyncio
async def test_create_pool_applies_default_size_and_statement_timeout(monkeypatch):
    """풀 크기와 statement timeout 기본값이 적용된다."""
    captured: dict[str, object] = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://experience-map-db")
    monkeypatch.setattr(connection.asyncpg, "create_pool", _mock_create_pool(captured))

    await connection.create_pool()

    kwargs = captured["kwargs"]
    assert kwargs["min_size"] == connection.DEFAULT_POOL_MIN_SIZE
    assert kwargs["max_size"] == connection.DEFAULT_POOL_MAX_SIZE
    assert kwargs["server_settings"] == {
        "statement_timeout": str(connection.DEFAULT_STATEMENT_TIMEOUT_MS)
    }
    assert kwargs["command_timeout"] == connection.DEFAULT_STATEMENT_TIMEOUT_MS / 1000


@pytest.mark.asyncio
async def test_create_pool_reads_size_and_timeout_from_env(monkeypatch):
    """풀 크기와 statement timeout을 환경변수로 조정할 수 있다."""
    captured: dict[str, object] = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://experience-map-db")
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "8")
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "3000")
    monkeypatch.setattr(connection.asyncpg, "create_pool", _mock_create_pool(captured))

    await connection.create_pool()

    kwargs = captured["kwargs"]
    assert kwargs["min_size"] == 2
    assert kwargs["max_size"] == 8
    assert kwargs["server_settings"] == {"statement_timeout": "3000"}
    assert kwargs["command_timeout"] == 3.0


@pytest.mark.asyncio
async def test_create_pool_falls_back_on_invalid_env(monkeypatch):
    """정수가 아닌 설정값은 기본값으로 대체한다."""
    captured: dict[str, object] = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://experience-map-db")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "many")
    monkeypatch.setattr(connection.asyncpg, "create_pool", _mock_create_pool(captured))

    await connection.create_pool()

    assert captured["kwargs"]["max_size"] == connection.DEFAULT_POOL_MAX_SIZE


@pytest.mark.asyncio
async def test_create_pool_is_idempotent(monkeypatch):
    """이미 유효한 풀이 있으면 재사용한다."""
    calls: list[str] = []

    async def _factory(db_url: str, **kwargs):
        calls.append(db_url)
        return DummyPool(**kwargs)

    monkeypatch.setenv("DATABASE_URL", "postgresql://experience-map-db")
    monkeypatch.setattr(connection.asyncpg, "create_pool", _factory)

    first = await connection.create_pool()
    second = await connection.create_pool()

    assert first is second
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_create_pool_requires_database_url(monkeypatch):
    """DATABASE_URL이 없으면 예외를 발생시킨다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL 환경변수"):
        await connection.create_pool()


@pytest.mark.asyncio
async def test_close_pool_closes_and_clears_singleton(monkeypatch):
    """close_pool이 풀을 닫고 싱글톤을 비운다."""
    captured: dict[str, object] = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://experience-map-db")
    monkeypatch.setattr(connection.asyncpg, "create_pool", _mock_create_pool(captured))

    pool = await connection.create_pool()
    await connection.close_pool()

    assert pool.closed is True
    with pytest.raises(RuntimeError, match="DB 커넥션 풀이 초기화되지 않았습니다"):
        connection.get_pool()


def test_get_pool_raises_when_not_initialized():
    """초기화 전 get_pool 호출 시 예외를 발생시킨다."""
    with pytest.raises(RuntimeError, match="DB 커넥션 풀이 초기화되지 않았습니다"):
        connection.get_pool()


def test_get_pool_status_disconnected_when_not_initialized():
    """풀이 없으면 disconnected를 반환한다."""
    assert connection.get_pool_status() == "disconnected"


@pytest.mark.asyncio
async def test_get_pool_status_reflects_pool_state(monkeypatch):
    """풀 생성·종료에 따라 상태가 바뀐다."""
    captured: dict[str, object] = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://experience-map-db")
    monkeypatch.setattr(connection.asyncpg, "create_pool", _mock_create_pool(captured))

    await connection.create_pool()
    assert connection.get_pool_status() == "connected"

    await connection.close_pool()
    assert connection.get_pool_status() == "disconnected"
