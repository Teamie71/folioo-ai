"""Checkpointer 팩토리 테스트"""

from contextlib import asynccontextmanager

import pytest

from common.checkpointer import factory


class DummyCheckpointer:
    """테스트용 Checkpointer 더블"""

    def __init__(self):
        self.setup_called = False

    async def setup(self) -> None:
        """초기화 호출 여부 기록"""
        self.setup_called = True


@pytest.fixture(autouse=True)
def clear_checkpointer() -> None:
    """테스트마다 checkpointer 싱글톤 초기화"""
    factory.reset_checkpointer()
    yield
    factory.reset_checkpointer()


def _mock_from_conn_string(captured: dict):
    @asynccontextmanager
    async def _factory(db_url: str):
        dummy = DummyCheckpointer()
        captured["db_url"] = db_url
        captured["checkpointer"] = dummy
        yield dummy

    return _factory


@pytest.mark.asyncio
async def test_get_checkpointer_singleton(monkeypatch):
    """동일한 인스턴스가 반환되는지 테스트"""
    captured: dict[str, object] = {}
    monkeypatch.setenv("CHECKPOINT_DATABASE_URL", "postgresql://checkpoint-db")
    monkeypatch.setattr(
        factory.AsyncPostgresSaver,
        "from_conn_string",
        _mock_from_conn_string(captured),
    )

    async with factory.setup_checkpointer():
        first = factory.get_checkpointer()
        second = factory.get_checkpointer()

        assert first is second
        assert captured["db_url"] == "postgresql://checkpoint-db"
        assert captured["checkpointer"].setup_called is True


@pytest.mark.asyncio
async def test_get_checkpointer_uses_database_url_fallback(monkeypatch):
    """전용 URL이 없으면 DATABASE_URL을 재사용한다."""
    captured: dict[str, object] = {}
    monkeypatch.delenv("CHECKPOINT_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://shared-db")
    monkeypatch.setattr(
        factory.AsyncPostgresSaver,
        "from_conn_string",
        _mock_from_conn_string(captured),
    )

    async with factory.setup_checkpointer():
        assert factory.get_checkpointer() is captured["checkpointer"]

    assert captured["db_url"] == "postgresql://shared-db"


@pytest.mark.asyncio
async def test_reset_checkpointer_returns_new_instance(monkeypatch):
    """reset 후 새로운 인스턴스가 생성되는지 테스트"""
    created: list[DummyCheckpointer] = []

    @asynccontextmanager
    async def _from_conn_string(_: str):
        dummy = DummyCheckpointer()
        created.append(dummy)
        yield dummy

    monkeypatch.setenv("CHECKPOINT_DATABASE_URL", "postgresql://checkpoint-db")
    monkeypatch.setattr(factory.AsyncPostgresSaver, "from_conn_string", _from_conn_string)

    async with factory.setup_checkpointer():
        first = factory.get_checkpointer()
        factory.reset_checkpointer()

        with pytest.raises(RuntimeError):
            factory.get_checkpointer()

    async with factory.setup_checkpointer():
        second = factory.get_checkpointer()

    assert first is not second
    assert created == [first, second]


@pytest.mark.asyncio
async def test_setup_checkpointer_requires_database_url(monkeypatch):
    """체크포인터 DB URL이 없으면 예외를 발생시킨다."""
    monkeypatch.delenv("CHECKPOINT_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="CHECKPOINT_DATABASE_URL 또는 DATABASE_URL"):
        async with factory.setup_checkpointer():
            pass


def test_get_checkpointer_raises_when_not_initialized():
    """초기화 전 get_checkpointer 호출 시 예외를 발생시킨다."""
    with pytest.raises(RuntimeError, match="Checkpointer가 초기화되지 않았습니다"):
        factory.get_checkpointer()
