"""포트폴리오 API 테스트"""

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import portfolio as portfolio_api
from features.portfolio.schemas import PortfolioOutput, PortfolioResult, PortfolioStatus


class DummyRepo:
    def __init__(self, row: dict | None = None):
        self._row = row

    async def get_by_session_id(self, _session_id: str) -> dict | None:
        return self._row


class DummyPortfolioService:
    def __init__(
        self,
        *,
        repo_row: dict | None = None,
        start_generation_error: Exception | None = None,
        result: PortfolioResult | None = None,
        status_error: Exception | None = None,
    ) -> None:
        self._repository = DummyRepo(repo_row)
        self._start_generation_error = start_generation_error
        self._result = result
        self._status_error = status_error
        self.updated_rate: tuple[str, int] | None = None

    async def has_generating_or_completed(self, _session_id: str) -> bool:
        row = await self._repository.get_by_session_id(_session_id)
        return bool(
            row and row["status"] in {PortfolioStatus.GENERATING.value, PortfolioStatus.COMPLETED.value}
        )

    async def exists(self, _portfolio_id: str) -> bool:
        return self._result is not None

    async def start_generation(self, session_id: str, user_id: str, background_tasks) -> str:
        if self._start_generation_error is not None:
            raise self._start_generation_error
        assert session_id
        assert user_id
        assert background_tasks is not None
        return "portfolio-1"

    async def get_status(self, _portfolio_id: str):
        if self._status_error is not None:
            raise self._status_error
        return {"status": PortfolioStatus.GENERATING, "progress_message": "진행 중"}

    async def get_result(self, _portfolio_id: str) -> PortfolioResult | None:
        return self._result

    async def get_by_session(self, _session_id: str) -> PortfolioResult | None:
        return self._result

    async def update_contribution_rate(self, portfolio_id: str, rate: int) -> None:
        self.updated_rate = (portfolio_id, rate)


def _create_client(monkeypatch, service: DummyPortfolioService) -> TestClient:
    monkeypatch.setattr(portfolio_api, "get_portfolio_service", lambda: service)
    app = FastAPI()
    app.include_router(portfolio_api.router, prefix="/api/v1")
    return TestClient(app)


def test_generate_portfolio_accepted(monkeypatch):
    """생성 요청 시 202를 반환한다."""
    client = _create_client(monkeypatch, DummyPortfolioService())

    response = client.post(
        "/api/v1/portfolio/generate",
        json={"session_id": "session-1", "user_id": "user-1"},
    )

    assert response.status_code == 202
    assert response.json() == {"portfolio_id": "portfolio-1", "status": "generating"}


def test_generate_portfolio_returns_409_for_existing(monkeypatch):
    """동일 세션 생성 중/완료 상태면 409를 반환한다."""
    client = _create_client(
        monkeypatch,
        DummyPortfolioService(repo_row={"id": "existing", "status": PortfolioStatus.GENERATING.value}),
    )

    response = client.post(
        "/api/v1/portfolio/generate",
        json={"session_id": "session-1", "user_id": "user-1"},
    )

    assert response.status_code == 409


def test_generate_portfolio_returns_400_for_incomplete_interview(monkeypatch):
    """인터뷰 미완료 상태 오류는 400으로 매핑한다."""
    client = _create_client(
        monkeypatch,
        DummyPortfolioService(
            start_generation_error=ValueError("인터뷰가 완료되지 않아 포트폴리오를 생성할 수 없습니다.")
        ),
    )

    response = client.post(
        "/api/v1/portfolio/generate",
        json={"session_id": "session-1", "user_id": "user-1"},
    )

    assert response.status_code == 400


def test_get_portfolio_result_returns_404_when_missing(monkeypatch):
    """포트폴리오 결과가 없으면 404를 반환한다."""
    client = _create_client(monkeypatch, DummyPortfolioService(result=None))

    response = client.get("/api/v1/portfolio/portfolio-1")

    assert response.status_code == 404


def test_update_contribution_rate_returns_200(monkeypatch):
    """기여도 수정 요청이 성공하면 200을 반환한다."""
    result = PortfolioResult(
        portfolio_id="portfolio-1",
        session_id="session-1",
        user_id="user-1",
        experience_name="경험",
        status=PortfolioStatus.COMPLETED,
        contribution_rate=10,
        output=PortfolioOutput(
            detail_info="상세",
            assigned_task="담당",
            problem_solving="해결",
            lessons_learned="배운점",
        ),
        created_at=datetime.now(UTC),
    )
    service = DummyPortfolioService(result=result)
    client = _create_client(monkeypatch, service)

    response = client.patch(
        "/api/v1/portfolio/portfolio-1/contribution-rate",
        json={"contribution_rate": 35},
    )

    assert response.status_code == 200
    assert service.updated_rate == ("portfolio-1", 35)
