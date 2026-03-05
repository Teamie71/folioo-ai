"""포트폴리오 API 테스트"""

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import portfolio as portfolio_api
from features.portfolio.schemas import PortfolioOutput, PortfolioResult, PortfolioStatus

PORTFOLIO_UUID = "11111111-1111-1111-1111-111111111111"


class DummyPortfolioService:
    def __init__(
        self,
        *,
        start_generation_error: Exception | None = None,
        result: PortfolioResult | None = None,
        status_error: Exception | None = None,
    ) -> None:
        self._start_generation_error = start_generation_error
        self._result = result
        self._status_error = status_error
        self.updated_rate: tuple[str, int] | None = None

    async def exists(self, _portfolio_id: str) -> bool:
        return self._result is not None

    async def start_generation(
        self, portfolio_id: int, session_id: str, user_id: str, background_tasks=None
    ) -> int:
        if self._start_generation_error is not None:
            raise self._start_generation_error
        assert session_id
        assert user_id
        assert background_tasks is not None
        return portfolio_id

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
        json={"portfolio_id": 100, "session_id": "session-1", "user_id": "user-1"},
    )

    assert response.status_code == 202
    assert response.json() == {"portfolio_id": "100", "status": "generating"}


def test_generate_portfolio_returns_400_for_incomplete_interview(monkeypatch):
    """인터뷰 미완료 상태 오류는 400으로 매핑한다."""
    client = _create_client(
        monkeypatch,
        DummyPortfolioService(
            start_generation_error=ValueError(
                "인터뷰가 완료되지 않아 포트폴리오를 생성할 수 없습니다."
            )
        ),
    )

    response = client.post(
        "/api/v1/portfolio/generate",
        json={"portfolio_id": 1, "session_id": "session-1", "user_id": "user-1"},
    )

    assert response.status_code == 400


def test_get_portfolio_result_returns_404_when_missing(monkeypatch):
    """포트폴리오 결과가 없으면 404를 반환한다."""
    client = _create_client(monkeypatch, DummyPortfolioService(result=None))

    response = client.get(f"/api/v1/portfolio/{PORTFOLIO_UUID}")

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
            description="상세",
            contributions="담당",
            achievements="해결",
            insights="배운점",
        ),
        created_at=datetime.now(UTC),
    )
    service = DummyPortfolioService(result=result)
    client = _create_client(monkeypatch, service)

    response = client.patch(
        f"/api/v1/portfolio/{PORTFOLIO_UUID}/contribution-rate",
        json={"contribution_rate": 35},
    )

    assert response.status_code == 200
    assert service.updated_rate == (PORTFOLIO_UUID, 35)


def test_get_portfolio_status_returns_200(monkeypatch):
    """포트폴리오 상태 조회 성공 시 200을 반환한다."""
    client = _create_client(monkeypatch, DummyPortfolioService())

    response = client.get(f"/api/v1/portfolio/{PORTFOLIO_UUID}/status")

    assert response.status_code == 200
    assert response.json()["status"] == PortfolioStatus.GENERATING.value


def test_get_portfolio_status_returns_404(monkeypatch):
    """포트폴리오가 없으면 status 엔드포인트는 404를 반환한다."""
    client = _create_client(
        monkeypatch,
        DummyPortfolioService(status_error=ValueError("포트폴리오를 찾을 수 없습니다")),
    )

    response = client.get(f"/api/v1/portfolio/{PORTFOLIO_UUID}/status")

    assert response.status_code == 404


def test_get_session_returns_200(monkeypatch):
    """세션 ID로 포트폴리오 조회 성공 시 200을 반환한다."""
    result = PortfolioResult(
        portfolio_id=PORTFOLIO_UUID,
        session_id="session-1",
        user_id="user-1",
        experience_name="경험",
        status=PortfolioStatus.COMPLETED,
        contribution_rate=50,
        output=PortfolioOutput(
            description="상세",
            contributions="담당",
            achievements="해결",
            insights="배운점",
        ),
        created_at=datetime.now(UTC),
    )
    client = _create_client(monkeypatch, DummyPortfolioService(result=result))

    response = client.get("/api/v1/portfolio/session/session-1")

    assert response.status_code == 200
    assert response.json()["portfolio_id"] == PORTFOLIO_UUID


def test_get_session_returns_404(monkeypatch):
    """포트폴리오가 없으면 세션 조회 엔드포인트는 404를 반환한다."""
    client = _create_client(monkeypatch, DummyPortfolioService(result=None))

    response = client.get("/api/v1/portfolio/session/session-1")

    assert response.status_code == 404
