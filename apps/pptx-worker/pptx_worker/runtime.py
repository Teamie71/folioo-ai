"""PPTX 워커 런타임 상태 관리."""

import asyncio
import inspect
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from features.visualization.pptx.soffice_render import (
    ConversionCounter,
    InMemoryConversionCounter,
    should_recycle_worker,
)

logger = logging.getLogger(__name__)

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
        processed_counter: ConversionCounter | None = None,
    ) -> None:
        self._recycle_after = recycle_after if recycle_after is not None else _load_recycle_after()
        if self._recycle_after <= 0:
            raise ValueError("recycle_after는 1 이상이어야 합니다.")
        self._shutdown_callback = shutdown_callback or _exit_process
        self._active_count = 0
        self._processed_counter = processed_counter or InMemoryConversionCounter()
        self._shutdown_requested = False
        self._in_flight_tasks = InFlightTaskRegistry()
        self._lock = asyncio.Lock()

    @property
    def processed_counter(self) -> ConversionCounter:
        """PPTX 렌더러와 공유할 누적 변환 카운터."""
        return self._processed_counter

    @property
    def in_flight_tasks(self) -> "InFlightTaskRegistry":
        """현재 워커 프로세스에서 실행 중인 Cloud Tasks 작업 registry."""
        return self._in_flight_tasks

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
        """실제로 완료된 soffice 변환 횟수를 증가시킨다."""
        async with self._lock:
            self._processed_counter.increment()

    async def snapshot(self) -> dict[str, int | bool | str]:
        """헬스체크 응답용 런타임 상태 반환."""
        async with self._lock:
            lifetime_processed = self._processed_counter.value
            ready_for_recycle = should_recycle_worker(self._processed_counter, self._recycle_after)
            return {
                "status": "ok",
                "concurrent_active": self._active_count,
                "lifetime_processed": lifetime_processed,
                "ready_for_recycle": ready_for_recycle,
            }

    async def shutdown_if_ready(self) -> bool:
        """재활용 임계치에 도달했고 active 요청이 없으면 종료 콜백을 실행한다."""
        async with self._lock:
            if (
                self._shutdown_requested
                or self._active_count > 0
                or not should_recycle_worker(self._processed_counter, self._recycle_after)
            ):
                return False
            self._shutdown_requested = True

        try:
            result = self._shutdown_callback()
            if inspect.isawaitable(result):
                await result
        except Exception:
            async with self._lock:
                self._shutdown_requested = False
            logger.exception("워커 재활용 종료 콜백 실행 실패")
            return False
        return True


class InFlightTaskClaim:
    """worker-local in-flight 작업 claim."""

    def __init__(
        self,
        registry: "InFlightTaskRegistry",
        *,
        execution_key: str,
        target_key: str,
    ) -> None:
        self._registry = registry
        self._execution_key = execution_key
        self._target_key = target_key
        self._released = False

    @property
    def execution_key(self) -> str:
        """Cloud Tasks payload 단위 실행 키."""
        return self._execution_key

    @property
    def target_key(self) -> str:
        """job 또는 slide 단위 대상 키."""
        return self._target_key

    async def __aenter__(self) -> "InFlightTaskClaim":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        await self.release()

    async def release(self) -> None:
        """claim 을 해제한다."""
        if self._released:
            return
        await self._registry.release(
            execution_key=self._execution_key,
            target_key=self._target_key,
        )
        self._released = True


class InFlightTaskRegistry:
    """동일 워커 프로세스 안의 중복 Cloud Tasks 실행을 막는다."""

    def __init__(self) -> None:
        self._execution_keys: set[str] = set()
        self._target_keys: set[str] = set()
        self._lock = asyncio.Lock()

    async def try_acquire(
        self,
        *,
        execution_key: str,
        target_key: str,
    ) -> InFlightTaskClaim | None:
        """이미 실행 중인 payload 또는 대상이면 None, 아니면 claim 을 반환한다."""
        async with self._lock:
            if execution_key in self._execution_keys or target_key in self._target_keys:
                return None
            self._execution_keys.add(execution_key)
            self._target_keys.add(target_key)
        return InFlightTaskClaim(
            self,
            execution_key=execution_key,
            target_key=target_key,
        )

    async def is_in_flight(self, *, execution_key: str, target_key: str) -> bool:
        """이미 실행 중인 payload 또는 대상인지 확인한다."""
        async with self._lock:
            return execution_key in self._execution_keys or target_key in self._target_keys

    async def release(self, *, execution_key: str, target_key: str) -> None:
        """실행 완료 또는 실패 후 in-flight 키를 해제한다."""
        async with self._lock:
            self._execution_keys.discard(execution_key)
            self._target_keys.discard(target_key)


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
    "InFlightTaskClaim",
    "InFlightTaskRegistry",
    "WorkerRuntime",
    "get_worker_runtime",
    "set_worker_runtime",
]
