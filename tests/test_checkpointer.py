"""Checkpointer 팩토리 테스트"""

from common.checkpointer.factory import get_checkpointer, reset_checkpointer


def test_get_checkpointer_singleton(tmp_path, monkeypatch):
    """동일한 인스턴스가 반환되는지 테스트"""
    reset_checkpointer()

    db_path = tmp_path / "checkpoints.sqlite"
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(db_path))

    first = get_checkpointer()
    second = get_checkpointer()

    assert first is second


def test_get_checkpointer_creates_directory(tmp_path, monkeypatch):
    """DB 경로의 디렉토리가 생성되는지 테스트"""
    reset_checkpointer()

    db_dir = tmp_path / "nested" / "dir"
    db_path = db_dir / "checkpoints.sqlite"
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(db_path))

    get_checkpointer()

    assert db_dir.exists()
    assert db_dir.is_dir()


def test_reset_checkpointer_returns_new_instance(tmp_path, monkeypatch):
    """reset 후 새로운 인스턴스가 생성되는지 테스트"""
    reset_checkpointer()

    db_path = tmp_path / "checkpoints.sqlite"
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(db_path))

    first = get_checkpointer()
    reset_checkpointer()
    second = get_checkpointer()

    assert first is not second


def test_get_checkpointer_default_path_creates_data_dir(tmp_path, monkeypatch):
    """환경변수 미설정 시 기본 경로(.data/)가 생성되는지 테스트"""
    reset_checkpointer()

    monkeypatch.delenv("CHECKPOINT_DB_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    get_checkpointer()

    assert (tmp_path / ".data").exists()
