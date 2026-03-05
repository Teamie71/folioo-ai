"""포트폴리오 서비스 테스트"""

from unittest.mock import AsyncMock

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


class DummyGenerator:
    def __init__(self, output: PortfolioOutput | None = None, exc: Exception | None = None):
        default_output = PortfolioOutput(
            description="상세",
            contributions="담당",
            achievements="해결",
            insights="배운점",
        )
        self.output = output or default_output
        self.exc = exc

    def generate(self, _collected_data: dict, _experience_name: str) -> PortfolioOutput:
        if self.exc is not None:
            raise self.exc
        return self.output


@pytest.mark.asyncio
async def test_start_generation_schedules_background_task():
    """완료된 세션이면 background task를 등록하고 portfolio_id를 반환한다."""
    state = {
        "all_stages_complete": True,
        "collected_data": {"stage_1": {}},
        "experience_name": "프로젝트A",
        "user_id": "user-1",
    }
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(state),
        portfolio_client=AsyncMock(),
    )

    tasks = DummyBackgroundTasks()
    portfolio_id = await service.start_generation(
        portfolio_id=123,
        session_id="session-1",
        user_id="user-1",
        background_tasks=tasks,
    )

    assert portfolio_id == 123
    assert len(tasks.calls) == 1


@pytest.mark.asyncio
async def test_start_generation_raises_for_none_session():
    """세션을 찾을 수 없으면 ValueError를 발생시킨다."""
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(None),
        portfolio_client=AsyncMock(),
    )

    with pytest.raises(ValueError, match="세션을 찾을 수 없습니다"):
        await service.start_generation(
            portfolio_id=1,
            session_id="nonexistent",
            user_id="user-1",
            background_tasks=DummyBackgroundTasks(),
        )


@pytest.mark.asyncio
async def test_start_generation_raises_for_incomplete_interview():
    """인터뷰 미완료 시 ValueError를 발생시킨다."""
    state = {
        "all_stages_complete": False,
        "collected_data": {},
        "experience_name": "x",
        "user_id": "u",
    }
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(state),
        portfolio_client=AsyncMock(),
    )

    with pytest.raises(ValueError, match="인터뷰가 완료되지 않아"):
        await service.start_generation(
            portfolio_id=1,
            session_id="session-1",
            user_id="u",
            background_tasks=DummyBackgroundTasks(),
        )


@pytest.mark.asyncio
async def test_start_generation_raises_for_user_mismatch():
    """세션 사용자와 요청 사용자가 불일치하면 ValueError를 발생시킨다."""
    state = {
        "all_stages_complete": True,
        "collected_data": {},
        "experience_name": "x",
        "user_id": "user-a",
    }
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(state),
        portfolio_client=AsyncMock(),
    )

    with pytest.raises(ValueError, match="사용자가 일치하지 않습니다"):
        await service.start_generation(
            portfolio_id=1,
            session_id="session-1",
            user_id="user-b",
            background_tasks=DummyBackgroundTasks(),
        )


@pytest.mark.asyncio
async def test_background_generation_success_calls_update_result():
    """Background Task 성공 시 portfolio_client.update_result을 호출한다."""
    mock_client = AsyncMock()
    output = PortfolioOutput(
        description="설명",
        contributions="기여",
        achievements="성과",
        insights="인사이트",
    )
    service = PortfolioService(
        generator=DummyGenerator(output=output),
        interview_service=DummyInterviewService(None),
        portfolio_client=mock_client,
    )

    await service._generate_portfolio_background(42, {}, "exp")

    mock_client.update_result.assert_called_once_with(
        42,
        status="completed",
        description="설명",
        contributions="기여",
        achievements="성과",
        insights="인사이트",
    )


@pytest.mark.asyncio
async def test_background_generation_success_callback_failure_attempts_failed():
    """성공 콜백이 실패하면 failed 콜백을 시도한다."""
    mock_client = AsyncMock()
    mock_client.update_result.side_effect = [
        RuntimeError("메인 서버 연결 실패"),  # completed 콜백 실패
        None,  # failed 콜백 성공
    ]
    output = PortfolioOutput(
        description="d",
        contributions="c",
        achievements="a",
        insights="i",
    )
    service = PortfolioService(
        generator=DummyGenerator(output=output),
        interview_service=DummyInterviewService(None),
        portfolio_client=mock_client,
    )

    await service._generate_portfolio_background(42, {}, "exp")

    assert mock_client.update_result.call_count == 2
    mock_client.update_result.assert_any_call(
        42,
        status="completed",
        description="d",
        contributions="c",
        achievements="a",
        insights="i",
    )
    mock_client.update_result.assert_any_call(
        42,
        status="failed",
        error_message="메인 서버 연결 실패",
    )


@pytest.mark.asyncio
async def test_background_generation_failure_calls_failed_callback():
    """Background Task 실패 시 failed 콜백을 전송한다."""
    mock_client = AsyncMock()
    service = PortfolioService(
        generator=DummyGenerator(exc=RuntimeError("boom")),
        interview_service=DummyInterviewService(None),
        portfolio_client=mock_client,
    )

    await service._generate_portfolio_background(42, {}, "exp")

    mock_client.update_result.assert_called_once_with(
        42,
        status="failed",
        error_message="boom",
    )


@pytest.mark.asyncio
async def test_get_status_raises_for_non_numeric_portfolio_id_without_repository():
    """repository 미주입 시 비숫자형 portfolio_id는 조회할 수 없다."""
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(None),
        portfolio_client=AsyncMock(),
    )

    with pytest.raises(ValueError, match="포트폴리오를 찾을 수 없습니다"):
        await service.get_status("pid")


@pytest.mark.asyncio
async def test_get_result_returns_none_for_non_numeric_portfolio_id_without_repository():
    """repository 미주입 시 비숫자형 portfolio_id 결과 조회는 None을 반환한다."""
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(None),
        portfolio_client=AsyncMock(),
    )

    result = await service.get_result("pid")

    assert result is None


@pytest.mark.asyncio
async def test_get_status_uses_main_server_for_numeric_portfolio_id():
    """숫자형 portfolio_id는 메인 서버 조회를 사용한다."""
    mock_client = AsyncMock()
    mock_client.get_portfolio.return_value = {
        "id": 100,
        "status": PortfolioStatus.GENERATING.value,
    }
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(None),
        portfolio_client=mock_client,
    )

    response = await service.get_status("100")

    assert response.status == PortfolioStatus.GENERATING
    mock_client.get_portfolio.assert_called_once_with(100)


@pytest.mark.asyncio
async def test_get_result_uses_main_server_for_numeric_portfolio_id():
    """숫자형 portfolio_id 완료 데이터는 메인 서버 결과를 반환한다."""
    mock_client = AsyncMock()
    mock_client.get_portfolio.return_value = {
        "id": 100,
        "session_id": "session-1",
        "user_id": 101,
        "experience_name": "프로젝트",
        "status": PortfolioStatus.COMPLETED.value,
        "description": "상세",
        "contributions": "담당",
        "achievements": "해결",
        "insights": "배운점",
        "contribution_rate": 30,
    }
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(None),
        portfolio_client=mock_client,
    )

    result = await service.get_result("100")

    assert result is not None
    assert result.portfolio_id == "100"
    assert result.user_id == "101"
    assert result.output is not None
    assert result.output.contributions == "담당"


@pytest.mark.asyncio
async def test_update_contribution_rate_raises_for_numeric_portfolio_id():
    """숫자형 portfolio_id는 기여도 수정을 지원하지 않는다."""
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(None),
        portfolio_client=AsyncMock(),
    )

    with pytest.raises(ValueError, match="기여도 수정을 지원하지 않습니다"):
        await service.update_contribution_rate("100", 50)


@pytest.mark.asyncio
async def test_exists_returns_false_for_non_numeric_portfolio_id_without_repository():
    """repository 미주입 시 비숫자형 portfolio_id는 존재하지 않는 것으로 처리한다."""
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(None),
        portfolio_client=AsyncMock(),
    )

    exists = await service.exists("pid")

    assert exists is False


@pytest.mark.asyncio
async def test_update_contribution_rate_raises_when_repository_missing_for_non_numeric_id():
    """repository 미주입 시 비숫자형 portfolio_id 기여도 수정은 실패한다."""
    service = PortfolioService(
        generator=DummyGenerator(),
        interview_service=DummyInterviewService(None),
        portfolio_client=AsyncMock(),
    )

    with pytest.raises(ValueError, match="포트폴리오를 찾을 수 없습니다"):
        await service.update_contribution_rate("pid", 50)
