"""PPTX 워커 메트릭 저장소 테스트."""

from concurrent.futures import ThreadPoolExecutor

from pptx_worker.metrics import WorkerMetricsRegistry, get_worker_metrics, set_worker_metrics


def test_metrics_registry_keeps_recent_duration_samples_only() -> None:
    """duration 샘플은 고정 크기 버퍼에 최근 값만 유지한다."""
    metrics = WorkerMetricsRegistry(max_duration_samples=2)

    for duration in (1.0, 2.0, 3.0):
        metrics.observe_soffice_conversion_success(
            duration_seconds=duration,
            rss_bytes=None,
        )

    snapshot = metrics.snapshot()
    assert snapshot.soffice_conversion_duration_seconds == (2.0, 3.0)
    assert snapshot.quantile(0.5) == 2.0
    assert snapshot.quantile(0.95) == 3.0


def test_worker_metrics_singleton_is_shared_between_threads() -> None:
    """첫 접근이 병렬이어도 메트릭 싱글톤은 하나만 생성된다."""
    set_worker_metrics(None)

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            registries = list(executor.map(lambda _: get_worker_metrics(), range(64)))

        assert len({id(registry) for registry in registries}) == 1
    finally:
        set_worker_metrics(None)
