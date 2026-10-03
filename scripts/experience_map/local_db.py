#!/usr/bin/env python3
"""경험정리 로컬 개발용 PostgreSQL 관리

`pgserver` 가 번들한 PostgreSQL 바이너리로 사용자 권한 클러스터를 띄운다.
sudo 도 Docker 도 필요 없다.

경험 맵 DB 와 checkpoint DB 를 **서로 다른 database** 로 만든다. 같은 DB 를 쓰면
LangGraph checkpoint 테이블이 경험 맵 DB 에 생성된다 (태스크 3.01).

사용법:
    uv run --group local-db python scripts/experience_map/local_db.py init
    uv run --group local-db python scripts/experience_map/local_db.py status
    uv run --group local-db python scripts/experience_map/local_db.py stop
    uv run --group local-db python scripts/experience_map/local_db.py psql expmap
    uv run --group local-db python scripts/experience_map/local_db.py reset
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import pgserver
except ImportError:
    sys.exit("pgserver 가 설치되지 않았습니다.\n  uv sync --group local-db\n를 먼저 실행하세요.")

REPO_ROOT = Path(__file__).resolve().parents[2]
PGDATA = REPO_ROOT / ".local" / "pgdata"
LOGFILE = REPO_ROOT / ".local" / "postgres.log"
SCHEMA = Path(__file__).resolve().parent / "schema.sql"

# 시스템 PostgreSQL(5432)과 겹치지 않게 비켜 둔다.
PORT = int(os.getenv("EXPMAP_LOCAL_PG_PORT", "55432"))
HOST = "127.0.0.1"
USER = "postgres"

EXPMAP_DB = "folioo_expmap"
CHECKPOINT_DB = "folioo_checkpoint"


PG_BIN = Path(pgserver.__file__).parent / "pginstall" / "bin"


def _bin(name: str) -> str:
    """pgserver 가 번들한 바이너리 경로

    `pgserver.initdb` 같은 모듈 속성은 래퍼 함수라 subprocess 에 넘길 수 없다.
    번들 디렉터리에서 실제 실행 파일을 찾는다.
    """
    path = PG_BIN / name
    if not path.exists():
        sys.exit(f"{name} 바이너리를 찾지 못했습니다: {path}")
    return str(path)


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=False, text=True, capture_output=True, **kwargs)


def _psql(
    database: str, sql: str | None = None, file: Path | None = None
) -> subprocess.CompletedProcess:
    argv = [
        _bin("psql"),
        "-h",
        HOST,
        "-p",
        str(PORT),
        "-U",
        USER,
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    if sql:
        argv += ["-c", sql]
    if file:
        argv += ["-f", str(file)]
    return _run(argv)


def is_running() -> bool:
    result = _run([_bin("pg_ctl"), "-D", str(PGDATA), "status"])
    return result.returncode == 0


def database_url(database: str) -> str:
    return f"postgresql://{USER}@{HOST}:{PORT}/{database}"


def cmd_init(args: argparse.Namespace) -> int:
    """클러스터 생성 → 기동 → database 2개 생성 → 스키마 적용"""
    PGDATA.parent.mkdir(parents=True, exist_ok=True)

    if not (PGDATA / "PG_VERSION").exists():
        print(f"클러스터 생성 중... ({PGDATA})")
        result = _run(
            [_bin("initdb"), "-D", str(PGDATA), "-U", USER, "--encoding=UTF8", "--locale=C"]
        )
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            return 1
    else:
        print(f"클러스터가 이미 있습니다 ({PGDATA})")

    if not is_running():
        print(f"기동 중... (포트 {PORT})")
        result = _run(
            [
                _bin("pg_ctl"),
                "-D",
                str(PGDATA),
                "-l",
                str(LOGFILE),
                "-o",
                f"-p {PORT} -h {HOST}",
                "-w",
                "start",
            ]
        )
        if result.returncode != 0:
            print(result.stdout + result.stderr, file=sys.stderr)
            print(f"로그: {LOGFILE}", file=sys.stderr)
            return 1
    else:
        print("이미 실행 중입니다")

    for database in (EXPMAP_DB, CHECKPOINT_DB):
        exists = _psql("postgres", f"SELECT 1 FROM pg_database WHERE datname = '{database}'")
        if "1 row" in exists.stdout:
            print(f"  {database} — 이미 있음")
            continue
        created = _run([_bin("createdb"), "-h", HOST, "-p", str(PORT), "-U", USER, database])
        if created.returncode != 0:
            print(created.stderr, file=sys.stderr)
            return 1
        print(f"  {database} — 생성")

    # 스키마는 경험 맵 DB 에만 적용한다. checkpoint DB 는 LangGraph 가 스스로 만든다.
    print(f"스키마 적용 중... ({SCHEMA.name})")
    applied = _psql(EXPMAP_DB, file=SCHEMA)
    if applied.returncode != 0:
        print(applied.stderr, file=sys.stderr)
        return 1

    print("\n완료. `.env` 에 아래를 넣으세요.\n")
    print(f"DATABASE_URL={database_url(EXPMAP_DB)}")
    print(f"CHECKPOINT_DATABASE_URL={database_url(CHECKPOINT_DB)}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    if is_running():
        print("이미 실행 중입니다")
        return 0
    result = _run(
        [
            _bin("pg_ctl"),
            "-D",
            str(PGDATA),
            "-l",
            str(LOGFILE),
            "-o",
            f"-p {PORT} -h {HOST}",
            "-w",
            "start",
        ]
    )
    print(result.stdout + result.stderr)
    return result.returncode


def cmd_stop(args: argparse.Namespace) -> int:
    if not is_running():
        print("실행 중이 아닙니다")
        return 0
    result = _run([_bin("pg_ctl"), "-D", str(PGDATA), "-m", "fast", "-w", "stop"])
    print(result.stdout + result.stderr)
    return result.returncode


def cmd_status(args: argparse.Namespace) -> int:
    if not PGDATA.exists():
        print(f"클러스터 없음 — `init` 을 먼저 실행하세요 ({PGDATA})")
        return 1

    running = is_running()
    print(f"클러스터 : {PGDATA}")
    print(f"상태     : {'running' if running else 'stopped'} (포트 {PORT})")
    if not running:
        return 1

    for database in (EXPMAP_DB, CHECKPOINT_DB):
        tables = _psql(
            database,
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'",
        )
        count = tables.stdout.strip().split("\n")[2].strip() if tables.returncode == 0 else "?"
        print(f"  {database:20s} 테이블 {count}개  {database_url(database)}")
    return 0


def cmd_psql(args: argparse.Namespace) -> int:
    """대화형 psql 접속"""
    database = {"expmap": EXPMAP_DB, "checkpoint": CHECKPOINT_DB}.get(args.database, args.database)
    return subprocess.call([_bin("psql"), "-h", HOST, "-p", str(PORT), "-U", USER, "-d", database])


def cmd_reset(args: argparse.Namespace) -> int:
    """클러스터를 통째로 지우고 다시 만든다"""
    if not args.yes:
        answer = input(f"{PGDATA} 를 삭제합니다. 계속할까요? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("취소했습니다")
            return 1

    if is_running():
        _run([_bin("pg_ctl"), "-D", str(PGDATA), "-m", "immediate", "-w", "stop"])
    if PGDATA.exists():
        shutil.rmtree(PGDATA)
        print(f"삭제: {PGDATA}")
    return cmd_init(args)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="클러스터 생성·기동·스키마 적용 (멱등)")
    sub.add_parser("start", help="기동")
    sub.add_parser("stop", help="정지")
    sub.add_parser("status", help="상태 조회")

    psql_parser = sub.add_parser("psql", help="대화형 psql")
    psql_parser.add_argument(
        "database", nargs="?", default="expmap", help="expmap | checkpoint | <이름>"
    )

    reset_parser = sub.add_parser("reset", help="클러스터 삭제 후 재생성")
    reset_parser.add_argument("-y", "--yes", action="store_true", help="확인 없이 진행")

    args = parser.parse_args()
    handlers = {
        "init": cmd_init,
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "psql": cmd_psql,
        "reset": cmd_reset,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
