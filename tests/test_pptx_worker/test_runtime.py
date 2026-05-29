"""PPTX 워커 런타임 상태 관리 테스트."""

import asyncio

import pytest
from pptx_worker.runtime import InFlightTaskRegistry


@pytest.mark.asyncio
async def test_in_flight_registry_blocks_same_execution_key() -> None:
    """같은 payload 실행 키는 중복 claim 할 수 없다."""
    registry = InFlightTaskRegistry()

    claim = await registry.try_acquire(
        execution_key="viz.generate:job:job-1:idempotency:task-1",
        target_key="viz.generate:job:job-1",
    )
    duplicate = await registry.try_acquire(
        execution_key="viz.generate:job:job-1:idempotency:task-1",
        target_key="viz.generate:job:job-2",
    )

    assert claim is not None
    assert duplicate is None


@pytest.mark.asyncio
async def test_in_flight_registry_blocks_same_target_key_with_different_execution_key() -> None:
    """다른 payload 라도 같은 job/slide 대상이면 중복 claim 할 수 없다."""
    registry = InFlightTaskRegistry()

    claim = await registry.try_acquire(
        execution_key="viz.regenerate:job:job-1:slide:slide-1:idempotency:task-1",
        target_key="viz.regenerate:job:job-1:slide:slide-1",
    )
    duplicate = await registry.try_acquire(
        execution_key="viz.regenerate:job:job-1:slide:slide-1:idempotency:task-2",
        target_key="viz.regenerate:job:job-1:slide:slide-1",
    )

    assert claim is not None
    assert duplicate is None


@pytest.mark.asyncio
async def test_in_flight_registry_allows_only_one_concurrent_acquire() -> None:
    """동시에 같은 대상을 claim 해도 하나만 성공한다."""
    registry = InFlightTaskRegistry()

    claims = await asyncio.gather(
        *[
            registry.try_acquire(
                execution_key=f"viz.generate:job:job-1:idempotency:task-{index}",
                target_key="viz.generate:job:job-1",
            )
            for index in range(10)
        ]
    )

    assert sum(claim is not None for claim in claims) == 1


@pytest.mark.asyncio
async def test_in_flight_registry_releases_keys_for_reacquire() -> None:
    """claim 해제 후 같은 실행 키와 대상 키를 다시 claim 할 수 있다."""
    registry = InFlightTaskRegistry()
    execution_key = "viz.generate:job:job-1:idempotency:task-1"
    target_key = "viz.generate:job:job-1"

    claim = await registry.try_acquire(execution_key=execution_key, target_key=target_key)
    assert claim is not None
    assert await registry.is_in_flight(execution_key=execution_key, target_key=target_key) is True

    await claim.release()
    reacquired = await registry.try_acquire(execution_key=execution_key, target_key=target_key)

    assert await registry.is_in_flight(execution_key=execution_key, target_key=target_key) is True
    assert reacquired is not None


@pytest.mark.asyncio
async def test_in_flight_registry_context_manager_releases_after_exception() -> None:
    """context manager 는 처리 중 예외가 나도 claim 을 해제한다."""
    registry = InFlightTaskRegistry()
    execution_key = "viz.generate:job:job-1:idempotency:task-1"
    target_key = "viz.generate:job:job-1"
    claim = await registry.try_acquire(execution_key=execution_key, target_key=target_key)
    assert claim is not None

    with pytest.raises(RuntimeError):
        async with claim:
            raise RuntimeError("pipeline failed")

    reacquired = await registry.try_acquire(execution_key=execution_key, target_key=target_key)

    assert reacquired is not None
