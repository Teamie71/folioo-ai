"""PPTX 워커 운영 메트릭."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Literal

FailureReason = Literal["timeout", "oom", "other"]

FAILURE_REASONS: tuple[FailureReason, ...] = ("timeout", "oom", "other")
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """메트릭 테스트와 렌더링에 쓰는 현재 값 스냅샷."""

    soffice_rss_bytes: int
    soffice_conversion_duration_seconds: tuple[float, ...]
    soffice_conversion_failures_total: dict[FailureReason, int]
    worker_oom_kill_total: int
    tmp_disk_bytes_used: int
    font_fallback_warnings_total: int

    def quantile(self, quantile: float) -> float:
        """저장된 duration 에서 nearest-rank 분위수를 계산한다."""
        if not self.soffice_conversion_duration_seconds:
            return 0.0
        values = sorted(self.soffice_conversion_duration_seconds)
        index = max(0, math.ceil(quantile * len(values)) - 1)
        return values[index]


class WorkerMetricsRegistry:
    """단일 워커 프로세스에서 쓰는 thread-safe 메트릭 저장소."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._soffice_rss_bytes = 0
        self._soffice_conversion_durations: list[float] = []
        self._soffice_conversion_failures = dict.fromkeys(FAILURE_REASONS, 0)
        self._worker_oom_kill_total = 0
        self._tmp_disk_bytes_used = 0
        self._font_fallback_warnings_total = 0

    def observe_soffice_conversion_success(
        self,
        *,
        duration_seconds: float,
        rss_bytes: int | None,
    ) -> None:
        """성공한 soffice 변환의 duration 과 RSS 를 기록한다."""
        with self._lock:
            self._soffice_conversion_durations.append(max(0.0, duration_seconds))
            if rss_bytes is not None and rss_bytes >= 0:
                self._soffice_rss_bytes = int(rss_bytes)

    def record_soffice_conversion_failure(self, reason: str) -> FailureReason:
        """soffice 변환 실패를 안정적인 reason 라벨로 기록한다."""
        normalized = normalize_failure_reason(reason)
        with self._lock:
            self._soffice_conversion_failures[normalized] += 1
        if normalized == "oom":
            self.record_worker_oom_kill()
        return normalized

    def record_worker_oom_kill(self, count: int = 1) -> None:
        """OOM kill 카운터를 증가시킨다."""
        if count <= 0:
            return
        with self._lock:
            self._worker_oom_kill_total += count

    def record_font_fallback_warnings(self, count: int = 1) -> None:
        """폰트 fallback 경고 카운터를 증가시킨다."""
        if count <= 0:
            return
        with self._lock:
            self._font_fallback_warnings_total += count

    def set_tmp_disk_bytes_used(self, value: int) -> None:
        """관측 시점의 임시 디스크 사용량 gauge 를 설정한다."""
        with self._lock:
            self._tmp_disk_bytes_used = max(0, int(value))

    def snapshot(self) -> MetricsSnapshot:
        """현재 메트릭 값을 복사해 반환한다."""
        with self._lock:
            return MetricsSnapshot(
                soffice_rss_bytes=self._soffice_rss_bytes,
                soffice_conversion_duration_seconds=tuple(self._soffice_conversion_durations),
                soffice_conversion_failures_total=dict(self._soffice_conversion_failures),
                worker_oom_kill_total=self._worker_oom_kill_total,
                tmp_disk_bytes_used=self._tmp_disk_bytes_used,
                font_fallback_warnings_total=self._font_fallback_warnings_total,
            )

    def render_prometheus(
        self,
        *,
        worker_jobs_processed_total: int,
        worker_ready_for_recycle: bool,
        worker_concurrent_active: int,
    ) -> str:
        """Prometheus text exposition 포맷으로 메트릭을 렌더링한다."""
        snapshot = self.snapshot()
        lines = [
            "# HELP soffice_rss_bytes Last observed LibreOffice child peak RSS in bytes.",
            "# TYPE soffice_rss_bytes gauge",
            f"soffice_rss_bytes {snapshot.soffice_rss_bytes}",
            "# HELP soffice_conversion_duration_seconds LibreOffice conversion duration summary.",
            "# TYPE soffice_conversion_duration_seconds summary",
        ]
        for quantile in (0.5, 0.95, 0.99):
            lines.append(
                "soffice_conversion_duration_seconds"
                f'{{quantile="{quantile:g}"}} {snapshot.quantile(quantile):.6g}'
            )
        duration_sum = sum(snapshot.soffice_conversion_duration_seconds)
        lines.extend(
            [
                f"soffice_conversion_duration_seconds_sum {duration_sum:.6g}",
                "soffice_conversion_duration_seconds_count "
                f"{len(snapshot.soffice_conversion_duration_seconds)}",
                "# HELP soffice_conversion_failures_total LibreOffice conversion failures by reason.",
                "# TYPE soffice_conversion_failures_total counter",
            ]
        )
        for reason in FAILURE_REASONS:
            lines.append(
                f'soffice_conversion_failures_total{{reason="{reason}"}} '
                f"{snapshot.soffice_conversion_failures_total[reason]}"
            )
        lines.extend(
            [
                "# HELP worker_oom_kill_total LibreOffice or worker OOM kill detections.",
                "# TYPE worker_oom_kill_total counter",
                f"worker_oom_kill_total {snapshot.worker_oom_kill_total}",
                "# HELP worker_jobs_processed_total Worker lifetime processed conversion count.",
                "# TYPE worker_jobs_processed_total counter",
                f"worker_jobs_processed_total {max(0, int(worker_jobs_processed_total))}",
                "# HELP worker_ready_for_recycle Worker recycle readiness signal.",
                "# TYPE worker_ready_for_recycle gauge",
                f"worker_ready_for_recycle {1 if worker_ready_for_recycle else 0}",
                "# HELP worker_concurrent_active Active Cloud Tasks requests in this worker.",
                "# TYPE worker_concurrent_active gauge",
                f"worker_concurrent_active {max(0, int(worker_concurrent_active))}",
                "# HELP tmp_disk_bytes_used Observed bytes used under the worker temp root.",
                "# TYPE tmp_disk_bytes_used gauge",
                f"tmp_disk_bytes_used {snapshot.tmp_disk_bytes_used}",
                "# HELP font_fallback_warnings_total LibreOffice font fallback warnings.",
                "# TYPE font_fallback_warnings_total counter",
                f"font_fallback_warnings_total {snapshot.font_fallback_warnings_total}",
            ]
        )
        return "\n".join(lines) + "\n"


def normalize_failure_reason(reason: str) -> FailureReason:
    """외부 예외/로그를 안정적인 실패 reason 라벨로 정규화한다."""
    normalized = reason.lower().strip()
    if normalized in FAILURE_REASONS:
        return normalized  # type: ignore[return-value]
    if "timeout" in normalized or "timed out" in normalized:
        return "timeout"
    if "oom" in normalized or "out of memory" in normalized or "sigkill" in normalized:
        return "oom"
    return "other"


def safe_directory_size(path: Path) -> int:
    """디렉터리 크기를 best-effort 로 계산한다."""
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0

    total = 0
    for root, _, filenames in os.walk(path):
        for filename in filenames:
            file_path = Path(root) / filename
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    return total


_metrics: WorkerMetricsRegistry | None = None


def get_worker_metrics() -> WorkerMetricsRegistry:
    """워커 메트릭 싱글톤 반환."""
    global _metrics

    if _metrics is None:
        _metrics = WorkerMetricsRegistry()
    return _metrics


def set_worker_metrics(metrics: WorkerMetricsRegistry | None) -> None:
    """테스트용 워커 메트릭 저장소 교체."""
    global _metrics

    _metrics = metrics


__all__ = [
    "FAILURE_REASONS",
    "PROMETHEUS_CONTENT_TYPE",
    "FailureReason",
    "MetricsSnapshot",
    "WorkerMetricsRegistry",
    "get_worker_metrics",
    "normalize_failure_reason",
    "safe_directory_size",
    "set_worker_metrics",
]
