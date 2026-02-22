"""포트폴리오 서비스 테스트"""

from datetime import UTC, datetime

import pytest

from features.portfolio.schemas import PortfolioOutput, PortfolioStatus
from features.portfolio.service import PortfolioService


class DummyInterviewService:
    def __init__(self, state: dict | None):
        self._state = state

    async def get_session_state(self, _session_id: str) -> dict | None:
        return self._state


class DummyBackgroundTasks:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def add_task(self, fn, *args):
        self.calls.append({"fn": fn, "args": args})


class DummyRepo:
    def __init__(self, row: dict | None = None):
        self.row = row
        self.created_args = None
        self.updated_status = None
        self.updated_result = None

    async def get_by_session_id(self, _session_id: str) -> dict | None:
        return self.row

    async def create(self, session_id: str, user_id: str, experience_name: str) -> str:
        self.created_args = {
            "session_id": session_id,
            "user_id": user_id,
            "experience_name": experience_name,
        }
        return "new-id"

    async def update_status(self, portfolio_id: str, status: str, error_message: str | None = None) -> None:
        self.updated_status = {
            "portfolio_id": portfolio_id,
            "status": status,
            "error_message": error_message,
        }

    async def update_result(self, portfolio_id: str, output: PortfolioOutput) -> None:
        self.updated_result = {"portfolio_id": portfolio_id, "output": output}

    async def get_by_id(self, _portfolio_id: str) -> dict | None:
        return self.row

    async def update_contribution_rate(self, _portfolio_id: str, _rate: int) -> None:
        return None


class DummyGenerator:
    def __init__(self, output: PortfolioOutput | None = None, exc: Exception | None = None):
        default_output = PortfolioOutput(
            detail_info="상세",
            assigned_task="담당",
            problem_solving="해결",
            lessons_learned="배운점",
        )
        self.output = output or default_output
        self.exc = exc

    def generate(self, _collected_data: dict, _experience_name: str) -> PortfolioOutput:
        if self.exc is not None:
            raise self.exc
        return self.output


@pytest.mark.asyncio
async def test_start_generation_creates_and_schedules_background_task(monkeypatch: pytest.MonkeyPatch):
    """완료된 세션이면 generating 레코드를 만들고 background task를 등록한다."""
    state = {
        "all_stages_complete": True,
        "collected_data": {"stage_1": {}},
        "experience_name": "프로젝트A",
        "user_id": "user-1",
    }
    repo = DummyRepo()
    service = PortfolioService(
        repository=repo,
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(state),
    )

    tasks = DummyBackgroundTasks()
    portfolio_id = await service.start_generation("session-1", "user-1", background_tasks=tasks)

    assert portfolio_id == "new-id"
    assert repo.created_args == {
        "session_id": "session-1",
        "user_id": "user-1",
        "experience_name": "프로젝트A",
    }
    assert repo.updated_status == {
        "portfolio_id": "new-id",
        "status": PortfolioStatus.GENERATING.value,
        "error_message": None,
    }
    assert len(tasks.calls) == 1


@pytest.mark.asyncio
async def test_start_generation_returns_existing_id_for_duplicate_session():
    """동일 세션의 generating/completed 포트폴리오가 있으면 기존 ID를 반환한다."""
    state = {"all_stages_complete": True, "collected_data": {}, "experience_name": "x", "user_id": "u"}
    repo = DummyRepo(row={"id": "existing-id", "status": PortfolioStatus.GENERATING.value})
    service = PortfolioService(
        repository=repo,
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(state),
    )

    portfolio_id = await service.start_generation("session-1", "u", background_tasks=DummyBackgroundTasks())

    assert portfolio_id == "existing-id"
    assert repo.created_args is None


@pytest.mark.asyncio
async def test_background_generation_failure_updates_failed_status():
    """Background Task 실패 시 예외를 전파하지 않고 failed 상태를 저장한다."""
    repo = DummyRepo()
    service = PortfolioService(
        repository=repo,
        generator=DummyGenerator(exc=RuntimeError("boom")),
        interview_service=DummyInterviewService(None),
    )

    await service._generate_portfolio_background("pid", {}, "exp")

    assert repo.updated_result is None
    assert repo.updated_status == {
        "portfolio_id": "pid",
        "status": PortfolioStatus.FAILED.value,
        "error_message": "boom",
    }


@pytest.mark.asyncio
async def test_get_status_and_get_result_for_completed_row():
    """상태/결과 조회가 완료 레코드를 올바르게 매핑한다."""
    row = {
        "id": "pid",
        "session_id": "sid",
        "user_id": "uid",
        "experience_name": "exp",
        "status": PortfolioStatus.COMPLETED.value,
        "contribution_rate": 55,
        "detail_info": "상세",
        "assigned_task": "담당",
        "problem_solving": "해결",
        "lessons_learned": "배운점",
        "created_at": datetime.now(UTC),
        "error_message": None,
    }
    service = PortfolioService(
        repository=DummyRepo(row=row),
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(None),
    )

    status = await service.get_status("pid")
    result = await service.get_result("pid")

    assert status.status == PortfolioStatus.COMPLETED
    assert result is not None
    assert result.output is not None
    assert result.output.problem_solving == "해결"
