"""첨삭 API 테스트"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import correction as correction_api
from features.correction.schemas import CorrectionStatus

CORRECTION_UUID = "11111111-1111-1111-1111-111111111111"


class DummyCorrectionService:
    def __init__(
        self,
        *,
        correction: dict | None = None,
        status_value: CorrectionStatus = CorrectionStatus.NOT_STARTED,
        status_error: Exception | None = None,
    ) -> None:
        self._correction = correction
        self._status_value = status_value
        self._status_error = status_error
        self.updated_company_insight: tuple[str, str] | None = None
        self.updated_emphasis_points: tuple[str, str] | None = None
        self.rag_started = False
        self.generation_started = False
        self.deleted_id: str | None = None

    async def create_correction(
        self,
        portfolio_id: str,
        user_id: str,
        company_name: str,
        job_title: str,
        job_description: str,
    ) -> dict:
        assert portfolio_id
        assert user_id
        assert company_name
        assert job_title
        assert job_description
        return {"id": "c-1", "status": CorrectionStatus.NOT_STARTED.value}

    async def get_correction(self, _correction_id: str) -> dict | None:
        return self._correction

    async def get_status(self, _correction_id: str) -> CorrectionStatus:
        if self._status_error is not None:
            raise self._status_error
        return self._status_value

    async def start_rag(self, _correction_id: str, _background_tasks) -> None:
        self.rag_started = True

    async def update_company_insight(self, correction_id: str, company_insight: str) -> None:
        self.updated_company_insight = (correction_id, company_insight)

    async def update_emphasis_points(self, correction_id: str, emphasis_points: str) -> None:
        self.updated_emphasis_points = (correction_id, emphasis_points)

    async def start_generation(self, _correction_id: str, _background_tasks) -> None:
        self.generation_started = True

    async def delete_correction(self, correction_id: str) -> None:
        self.deleted_id = correction_id


def _create_client(monkeypatch, service: DummyCorrectionService) -> TestClient:
    monkeypatch.setattr(correction_api, "get_correction_service", lambda: service)
    app = FastAPI()
    app.include_router(correction_api.router, prefix="/api/v1")
    return TestClient(app)


def test_create_correction_returns_201(monkeypatch):
    """첨삭 생성 요청이 성공하면 201을 반환한다."""
    client = _create_client(monkeypatch, DummyCorrectionService())

    response = client.post(
        "/api/v1/corrections",
        json={
            "portfolio_id": "portfolio-1",
            "user_id": "user-1",
            "company_name": "회사",
            "job_title": "백엔드",
            "job_description": "JD",
        },
    )

    assert response.status_code == 201
    assert response.json() == {"correction_id": "c-1", "status": "not_started"}


def test_get_correction_status_returns_404(monkeypatch):
    """상태 조회 시 첨삭이 없으면 404를 반환한다."""
    client = _create_client(
        monkeypatch,
        DummyCorrectionService(status_error=ValueError("첨삭을 찾을 수 없습니다")),
    )

    response = client.get(f"/api/v1/corrections/{CORRECTION_UUID}/status")

    assert response.status_code == 404


def test_start_rag_returns_409_when_status_invalid(monkeypatch):
    """RAG 시작은 not_started 상태가 아니면 409를 반환한다."""
    client = _create_client(
        monkeypatch,
        DummyCorrectionService(correction={"id": CORRECTION_UUID, "status": "doing_rag"}),
    )

    response = client.post(f"/api/v1/corrections/{CORRECTION_UUID}/rag")

    assert response.status_code == 409


def test_get_company_insight_returns_409_when_not_ready(monkeypatch):
    """기업 분석 조회는 company_insight 이전 상태에서 409를 반환한다."""
    client = _create_client(
        monkeypatch,
        DummyCorrectionService(correction={"id": CORRECTION_UUID, "status": "doing_rag"}),
    )

    response = client.get(f"/api/v1/corrections/{CORRECTION_UUID}/company-insight")

    assert response.status_code == 409


def test_update_company_insight_returns_200(monkeypatch):
    """기업 분석 수정이 성공하면 200을 반환한다."""
    service = DummyCorrectionService(
        correction={"id": CORRECTION_UUID, "status": CorrectionStatus.COMPANY_INSIGHT.value}
    )
    client = _create_client(monkeypatch, service)

    response = client.patch(
        f"/api/v1/corrections/{CORRECTION_UUID}/company-insight",
        json={"company_insight": "수정 내용"},
    )

    assert response.status_code == 200
    assert service.updated_company_insight == (CORRECTION_UUID, "수정 내용")


def test_start_generation_returns_202(monkeypatch):
    """첨삭 생성 시작이 성공하면 202를 반환한다."""
    service = DummyCorrectionService(
        correction={"id": CORRECTION_UUID, "status": CorrectionStatus.COMPANY_INSIGHT.value}
    )
    client = _create_client(monkeypatch, service)

    response = client.post(f"/api/v1/corrections/{CORRECTION_UUID}/generate")

    assert response.status_code == 202
    assert service.generation_started is True


def test_delete_correction_returns_204(monkeypatch):
    """첨삭 삭제가 성공하면 204를 반환한다."""
    service = DummyCorrectionService()
    client = _create_client(monkeypatch, service)

    response = client.delete(f"/api/v1/corrections/{CORRECTION_UUID}")

    assert response.status_code == 204
    assert service.deleted_id == CORRECTION_UUID
