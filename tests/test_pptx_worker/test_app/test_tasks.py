"""PPTX 워커 Cloud Tasks push 핸들러 테스트."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pptx_worker.api import tasks as tasks_api
from pptx_worker.main import create_app
from pptx_worker.metrics import WorkerMetricsRegistry, set_worker_metrics
from pptx_worker.runtime import WorkerRuntime, set_worker_runtime

from features.visualization.service import (
    FatalError,
    GenerateVisualizationTask,
    RegenerateVisualizationTask,
    RetryableError,
    VisualizationTaskService,
)


class FakeMainClient:
    """메인 백엔드 클라이언트 대역."""

    def __init__(self) -> None:
        self.job_context: dict[str, Any] = {"status": "pending"}
        self.slide_context: dict[str, Any] = {"status": "regenerating", "slide_order": 3}
        self.job_context_calls: list[str] = []
        self.slide_context_calls: list[tuple[str, str]] = []
        self.job_events: list[dict[str, Any]] = []
        self.slide_events: list[dict[str, Any]] = []
        self.closed = False
        self.close_error: Exception | None = None
        self.job_event_error: Exception | None = None
        self.slide_event_error: Exception | None = None

    async def get_job_context(self, job_id: str) -> dict[str, Any]:
        self.job_context_calls.append(job_id)
        return dict(self.job_context)

    async def get_slide_context(self, job_id: str, slide_id: str) -> dict[str, Any]:
        self.slide_context_calls.append((job_id, slide_id))
        return dict(self.slide_context)

    async def send_job_event(self, job_id: str, **kwargs: Any) -> None:
        if self.job_event_error is not None:
            raise self.job_event_error
        self.job_events.append({"job_id": job_id, **kwargs})

    async def send_slide_event(self, job_id: str, slide_id: str, **kwargs: Any) -> None:
        if self.slide_event_error is not None:
            raise self.slide_event_error
        self.slide_events.append({"job_id": job_id, "slide_id": slide_id, **kwargs})

    async def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class FakeVisualizationTaskService(VisualizationTaskService):
    """시각화 파이프라인 서비스 대역."""

    def __init__(self, runtime: WorkerRuntime) -> None:
        self.runtime = runtime
        self.generate_calls: list[GenerateVisualizationTask] = []
        self.regenerate_calls: list[RegenerateVisualizationTask] = []
        self.generate_error: Exception | None = None
        self.regenerate_error: Exception | None = None
        self.generate_conversion_count = 0
        self.regenerate_conversion_count = 0

    async def generate(self, task: GenerateVisualizationTask) -> None:
        self.generate_calls.append(task)
        if self.generate_error is not None:
            raise self.generate_error
        for _ in range(self.generate_conversion_count):
            await self.runtime.mark_processed()

    async def regenerate(self, task: RegenerateVisualizationTask) -> None:
        self.regenerate_calls.append(task)
        if self.regenerate_error is not None:
            raise self.regenerate_error
        for _ in range(self.regenerate_conversion_count):
            await self.runtime.mark_processed()


@pytest.fixture()
def worker_client(monkeypatch):
    """테스트용 워커 앱과 fake 의존성을 구성한다."""
    main_client = FakeMainClient()
    shutdown_calls: list[str] = []
    runtime = WorkerRuntime(
        recycle_after=2,
        shutdown_callback=lambda: shutdown_calls.append("shutdown"),
    )
    set_worker_metrics(WorkerMetricsRegistry())
    service = FakeVisualizationTaskService(runtime)

    tasks_api.set_main_client_factory(lambda callback_base_url: main_client)
    monkeypatch.setattr(tasks_api, "_get_task_service", lambda: service)
    set_worker_runtime(runtime)

    with TestClient(create_app()) as client:
        yield client, main_client, service, runtime, shutdown_calls

    tasks_api.reset_main_client_factory()
    set_worker_metrics(None)
    set_worker_runtime(None)


def generate_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messageType": "viz.generate",
        "jobId": "job-1",
        "portfolioId": "portfolio-1",
        "userId": "user-1",
        "templateId": "blue",
        "idempotencyKey": "idem-generate",
        "callbackBaseUrl": "http://main.local",
        "schemaVersion": 1,
    }
    payload.update(overrides)
    return payload


def regenerate_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messageType": "viz.regenerate",
        "jobId": "job-1",
        "slideId": "slide-3",
        "userRequest": "표를 더 간결하게 바꿔줘",
        "isRetry": False,
        "idempotencyKey": "idem-regenerate",
        "callbackBaseUrl": "http://main.local",
        "schemaVersion": 1,
    }
    payload.update(overrides)
    return payload


def test_generate_push_parses_payload_delegates_and_returns_200(worker_client):
    client, main_client, service, _, _ = worker_client

    response = client.post("/tasks/visualizations/generate", json=generate_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert main_client.job_context_calls == ["job-1"]
    assert len(service.generate_calls) == 1
    task = service.generate_calls[0]
    assert task.message_type == "viz.generate"
    assert task.job_id == "job-1"
    assert task.portfolio_id == "portfolio-1"
    assert task.template_id == "blue"
    assert task.callback_base_url == "http://main.local"
    assert main_client.closed is True


def test_regenerate_push_parses_payload_delegates_and_returns_200(worker_client):
    client, main_client, service, _, _ = worker_client

    response = client.post("/tasks/visualizations/regenerate", json=regenerate_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert main_client.slide_context_calls == [("job-1", "slide-3")]
    assert len(service.regenerate_calls) == 1
    task = service.regenerate_calls[0]
    assert task.message_type == "viz.regenerate"
    assert task.job_id == "job-1"
    assert task.slide_id == "slide-3"
    assert task.user_request == "표를 더 간결하게 바꿔줘"
    assert task.is_retry is False


def test_retry_payload_can_omit_user_request(worker_client):
    client, _, service, _, _ = worker_client

    payload = regenerate_payload(isRetry=True)
    payload.pop("userRequest")
    response = client.post("/tasks/visualizations/regenerate", json=payload)

    assert response.status_code == 200
    assert service.regenerate_calls[0].is_retry is True
    assert service.regenerate_calls[0].user_request is None


def test_terminal_generate_push_is_acked_without_reexecution(worker_client):
    client, main_client, service, _, _ = worker_client
    main_client.job_context = {"status": "completed"}

    response = client.post("/tasks/visualizations/generate", json=generate_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert service.generate_calls == []
    assert client.get("/health").json()["lifetime_processed"] == 0


def test_terminal_regenerate_push_is_acked_without_reexecution(worker_client):
    client, main_client, service, _, _ = worker_client
    main_client.slide_context = {"status": "completed", "slide_order": 3}

    response = client.post("/tasks/visualizations/regenerate", json=regenerate_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert service.regenerate_calls == []
    assert client.get("/health").json()["lifetime_processed"] == 0


def test_retryable_error_returns_503_for_cloud_tasks_retry(worker_client):
    client, _, service, _, _ = worker_client
    service.generate_error = RetryableError("LLM 일시 실패")

    response = client.post("/tasks/visualizations/generate", json=generate_payload())

    assert response.status_code == 503
    assert response.json()["status"] == "retryable_failure"
    assert client.get("/health").json()["lifetime_processed"] == 0


def test_fatal_generate_error_sends_job_callback_then_acks(worker_client):
    client, main_client, service, _, _ = worker_client
    service.generate_error = FatalError("템플릿 없음", error_code="TEMPLATE_NOT_FOUND")

    response = client.post("/tasks/visualizations/generate", json=generate_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "fatal_acked"
    assert main_client.job_events == [
        {
            "job_id": "job-1",
            "event": "all_completed",
            "idempotency_key": "idem-generate",
            "schema_version": 1,
            "summary": {"completed": 0, "failed": 1},
            "error_code": "TEMPLATE_NOT_FOUND",
            "occurred_at": main_client.job_events[0]["occurred_at"],
        }
    ]
    assert client.get("/health").json()["lifetime_processed"] == 0


def test_fatal_regenerate_error_sends_slide_callback_then_acks(worker_client):
    client, main_client, service, _, _ = worker_client
    service.regenerate_error = FatalError("렌더링 불가", error_code="RENDER_FAILED")

    response = client.post("/tasks/visualizations/regenerate", json=regenerate_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "fatal_acked"
    assert main_client.slide_events == [
        {
            "job_id": "job-1",
            "slide_id": "slide-3",
            "event": "slide_preview_error",
            "slide_order": 3,
            "idempotency_key": "idem-regenerate",
            "schema_version": 1,
            "message": "렌더링 불가",
            "retryable": False,
            "occurred_at": main_client.slide_events[0]["occurred_at"],
        }
    ]
    assert client.get("/health").json()["lifetime_processed"] == 0


def test_fatal_callback_failure_returns_503_for_retry(worker_client):
    client, main_client, service, _, _ = worker_client
    service.generate_error = FatalError("템플릿 없음")
    main_client.job_event_error = RuntimeError("callback timeout")

    response = client.post("/tasks/visualizations/generate", json=generate_payload())

    assert response.status_code == 503
    assert response.json()["status"] == "retryable_failure"


def test_close_failure_does_not_override_ack_response(worker_client):
    client, main_client, service, _, _ = worker_client
    main_client.close_error = RuntimeError("close failed")

    response = client.post("/tasks/visualizations/generate", json=generate_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(service.generate_calls) == 1


def test_health_reports_runtime_counters_and_recycle_readiness(worker_client):
    client, _, service, _, shutdown_calls = worker_client
    service.generate_conversion_count = 1

    initial = client.get("/health")
    assert initial.status_code == 200
    assert initial.json()["concurrent_active"] == 0
    assert initial.json()["lifetime_processed"] == 0
    assert initial.json()["ready_for_recycle"] is False

    first = client.post("/tasks/visualizations/generate", json=generate_payload(jobId="job-1"))
    after_first = client.get("/health")
    assert first.status_code == 200
    assert after_first.json()["lifetime_processed"] == 1
    assert after_first.json()["ready_for_recycle"] is False
    assert shutdown_calls == []

    second = client.post("/tasks/visualizations/generate", json=generate_payload(jobId="job-2"))
    after_second = client.get("/health")
    assert second.status_code == 200
    assert after_second.json()["concurrent_active"] == 0
    assert after_second.json()["lifetime_processed"] == 2
    assert after_second.json()["ready_for_recycle"] is True
    assert shutdown_calls == ["shutdown"]


def test_metrics_exposes_runtime_counter_and_recycle_signal(worker_client):
    """메트릭의 처리 카운터와 재활용 신호는 /health 와 같은 런타임 소스를 쓴다."""
    client, _, service, _, _ = worker_client
    service.generate_conversion_count = 2

    response = client.post("/tasks/visualizations/generate", json=generate_payload(jobId="job-1"))
    health = client.get("/health").json()
    metrics = client.get("/metrics")

    assert response.status_code == 200
    assert health["lifetime_processed"] == 2
    assert health["ready_for_recycle"] is True
    assert metrics.status_code == 200
    assert "worker_jobs_processed_total 2" in metrics.text
    assert "worker_ready_for_recycle 1" in metrics.text
    assert 'soffice_conversion_failures_total{reason="timeout"} 0' in metrics.text


@pytest.mark.asyncio
async def test_runtime_snapshot_reports_active_count():
    runtime = WorkerRuntime(recycle_after=2, shutdown_callback=lambda: None)

    async with runtime.track_active():
        snapshot = await runtime.snapshot()

    assert snapshot["concurrent_active"] == 1
    assert (await runtime.snapshot())["concurrent_active"] == 0


@pytest.mark.asyncio
async def test_runtime_shutdown_failure_allows_later_retry():
    calls = 0

    def shutdown_callback() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("shutdown failed")

    runtime = WorkerRuntime(recycle_after=1, shutdown_callback=shutdown_callback)
    await runtime.mark_processed()

    assert await runtime.shutdown_if_ready() is False
    assert await runtime.shutdown_if_ready() is True
    assert calls == 2
