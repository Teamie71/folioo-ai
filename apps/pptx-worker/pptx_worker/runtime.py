"""PPTX 워커 런타임 상태 관리."""

import asyncio
import inspect
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

ShutdownCallback = Callable[[], Any | Awaitable[Any]]

DEFAULT_RECYCLE_AFTER = 20


def _load_recycle_after() -> int:
    """환경변수에서 워커 재활용 임계값을 읽는다."""
    raw_value = os.getenv("PPTX_WORKER_RECYCLE_AFTER", str(DEFAULT_RECYCLE_AFTER))
    try:
        recycle_after = int(raw_value)
    except ValueError as exc:
        raise ValueError("PPTX_WORKER_RECYCLE_AFTER는 정수여야 합니다.") from exc
    if recycle_after <= 0:
        raise ValueError("PPTX_WORKER_RECYCLE_AFTER는 1 이상이어야 합니다.")
    return recycle_after


def _exit_process() -> None:
    """Cloud Run 이 새 인스턴스를 띄우도록 현재 프로세스를 종료한다."""
    os._exit(0)


class WorkerRuntime:
    """워커 active/lifetime 카운터와 재활용 신호를 관리한다."""

    def __init__(
        self,
        *,
        recycle_after: int | None = None,
        shutdown_callback: ShutdownCallback | None = None,
    ) -> None:
        self._recycle_after = recycle_after if recycle_after is not None else _load_recycle_after()
        if self._recycle_after <= 0:
            raise ValueError("recycle_after는 1 이상이어야 합니다.")
        self._shutdown_callback = shutdown_callback or _exit_process
        self._active_count = 0
        self._lifetime_processed = 0
        self._shutdown_requested = False
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def track_active(self):
        """요청 처리 중 active count 를 증가시키고 종료 시 감소시킨다."""
        async with self._lock:
            self._active_count += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active_count -= 1

    async def mark_processed(self) -> None:
        """실제로 위임 처리가 끝난 작업 수를 증가시킨다."""
        async with self._lock:
            self._lifetime_processed += 1

    async def snapshot(self) -> dict[str, int | bool | str]:
        """헬스체크 응답용 런타임 상태 반환."""
        async with self._lock:
            ready_for_recycle = self._lifetime_processed >= self._recycle_after
            return {
                "status": "ok",
                "concurrent_active": self._active_count,
                "lifetime_processed": self._lifetime_processed,
                "ready_for_recycle": ready_for_recycle,
            }

    async def shutdown_if_ready(self) -> bool:
        """재활용 임계치에 도달했고 active 요청이 없으면 종료 콜백을 실행한다."""
        async with self._lock:
            if (
                self._shutdown_requested
                or self._active_count > 0
                or self._lifetime_processed < self._recycle_after
            ):
                return False
            self._shutdown_requested = True

        result = self._shutdown_callback()
        if inspect.isawaitable(result):
            await result
        return True


_runtime: WorkerRuntime | None = None


def get_worker_runtime() -> WorkerRuntime:
    """워커 런타임 싱글톤 반환."""
    global _runtime

    if _runtime is None:
        _runtime = WorkerRuntime()
    return _runtime


def set_worker_runtime(runtime: WorkerRuntime | None) -> None:
    """테스트용 워커 런타임 교체."""
    global _runtime

    _runtime = runtime


__all__ = [
    "DEFAULT_RECYCLE_AFTER",
    "WorkerRuntime",
    "get_worker_runtime",
    "set_worker_runtime",
]
