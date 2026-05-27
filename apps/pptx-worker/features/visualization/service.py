"""PPTX 시각화 작업 위임 서비스 경계."""

from dataclasses import dataclass
from typing import Literal


class RetryableError(Exception):
    """Cloud Tasks 가 재시도해야 하는 일시적 실패."""


class FatalError(Exception):
    """재시도해도 복구되지 않는 치명적 실패."""

    def __init__(self, message: str, *, error_code: str = "VISUALIZATION_FATAL") -> None:
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GenerateVisualizationTask:
    """초기 PPTX 생성 작업."""

    message_type: Literal["viz.generate"]
    job_id: str
    portfolio_id: str
    user_id: str
    template_id: str
    idempotency_key: str
    callback_base_url: str
    schema_version: int


@dataclass(frozen=True, slots=True)
class RegenerateVisualizationTask:
    """단일 슬라이드 재생성 또는 retry 작업."""

    message_type: Literal["viz.regenerate"]
    job_id: str
    slide_id: str
    user_request: str | None
    is_retry: bool
    idempotency_key: str
    callback_base_url: str
    schema_version: int


class VisualizationTaskService:
    """후속 생성/재생성 파이프라인이 구현할 서비스 인터페이스."""

    async def generate(self, task: GenerateVisualizationTask) -> None:
        """초기 PPTX 생성 파이프라인으로 위임한다."""
        raise RetryableError("초기 PPTX 생성 파이프라인이 아직 연결되지 않았습니다.")

    async def regenerate(self, task: RegenerateVisualizationTask) -> None:
        """단일 슬라이드 재생성 파이프라인으로 위임한다."""
        raise RetryableError("PPTX 슬라이드 재생성 파이프라인이 아직 연결되지 않았습니다.")


_service: VisualizationTaskService | None = None


def get_visualization_task_service() -> VisualizationTaskService:
    """시각화 작업 서비스 싱글톤 반환."""
    global _service

    if _service is None:
        _service = VisualizationTaskService()
    return _service


def reset_visualization_task_service() -> None:
    """테스트용 시각화 작업 서비스 싱글톤 초기화."""
    global _service

    _service = None


__all__ = [
    "FatalError",
    "GenerateVisualizationTask",
    "RegenerateVisualizationTask",
    "RetryableError",
    "VisualizationTaskService",
    "get_visualization_task_service",
    "reset_visualization_task_service",
]
