"""첨삭 API 테스트 (httpx 클라이언트 기반)"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import correction as correction_api
from common.clients import base_client as base_client_module
from common.clients import correction_client as correction_client_module

CORRECTION_ID = "123"


class DummyCorrectionClient:
    """API 테스트용 CorrectionClient Mock"""

    def __init__(self, *, correction: dict | None = None) -> None:
        self._correction = correction
        self.updated_company_insight: tuple[int, str] | None = None
        self.updated_emphasis_points: tuple[int, str] | None = None
        self.deleted_id: int | None = None

    async def get_correction(self, correction_id: int) -> dict:
        if self._correction is None:
            raise base_client_module.MainServerError(
                status_code=404, detail=f"첨삭을 찾을 수 없습니다: {correction_id}"
            )
        return self._correction

    async def update_company_insight(self, correction_id: int, company_insight: str) -> dict:
        self.updated_company_insight = (correction_id, company_insight)
        return {"id": correction_id}

    async def update_emphasis_points(self, correction_id: int, emphasis_points: str) -> dict:
        self.updated_emphasis_points = (correction_id, emphasis_points)
        return {"id": correction_id}

    async def delete_correction(self, correction_id: int) -> None:
        self.deleted_id = correction_id


class DummyCorrectionService:
    """API 테스트용 CorrectionService Mock"""

    def __init__(
        self,
        *,
        rag_error: Exception | None = None,
        gen_error: Exception | None = None,
        retry_error: Exception | None = None,
    ) -> None:
        self.rag_started = False
        self.generation_started = False
        self.retry_started = False
        self._rag_error = rag_error
        self._gen_error = gen_error
        self._retry_error = retry_error

    async def start_rag(self, correction_id: int, background_tasks) -> None:
        if self._rag_error:
            raise self._rag_error
        self.rag_started = True

    async def start_generation(self, correction_id: int, background_tasks) -> None:
        if self._gen_error:
            raise self._gen_error
        self.generation_started = True

    async def retry(self, correction_id: int, background_tasks) -> None:
        if self._retry_error:
            raise self._retry_error
        self.retry_started = True


def _create_client(
    monkeypatch,
    correction_client: DummyCorrectionClient,
    service: DummyCorrectionService | None = None,
) -> TestClient:
    monkeypatch.setattr(
        correction_client_module, "get_correction_client", lambda: correction_client
    )
    monkeypatch.setattr(correction_api, "get_correction_client", lambda: correction_client)
    if service is not None:
        monkeypatch.setattr(correction_api, "get_correction_service", lambda: service)
    app = FastAPI()
    app.include_router(correction_api.router, prefix="/api/v1")
    return TestClient(app)


# ------------------------------------------------------------------
# 결과 조회
# ------------------------------------------------------------------


def test_get_correction_result_returns_200(monkeypatch):
    """첨삭 결과 조회가 성공하면 200을 반환한다."""
    cc = DummyCorrectionClient(correction={"id": 123, "status": "DONE", "result": None})
    client = _create_client(monkeypatch, cc)

    response = client.get(f"/api/v1/corrections/{CORRECTION_ID}")

    assert response.status_code == 200
    assert response.json()["correction_id"] == "123"
    assert response.json()["status"] == "done"


def test_get_correction_result_returns_404(monkeypatch):
    """첨삭이 없으면 404를 반환한다."""
    cc = DummyCorrectionClient(correction=None)
    client = _create_client(monkeypatch, cc)

    response = client.get(f"/api/v1/corrections/{CORRECTION_ID}")

    assert response.status_code == 404


# ------------------------------------------------------------------
# 상태 조회
# ------------------------------------------------------------------


def test_get_correction_status_returns_200(monkeypatch):
    """상태 조회가 성공하면 200을 반환한다."""
    cc = DummyCorrectionClient(correction={"id": 123, "status": "DOING_RAG"})
    client = _create_client(monkeypatch, cc)

    response = client.get(f"/api/v1/corrections/{CORRECTION_ID}/status")

    assert response.status_code == 200
    assert response.json()["status"] == "doing_rag"


def test_get_correction_status_returns_404(monkeypatch):
    """상태 조회 시 첨삭이 없으면 404를 반환한다."""
    cc = DummyCorrectionClient(correction=None)
    client = _create_client(monkeypatch, cc)

    response = client.get(f"/api/v1/corrections/{CORRECTION_ID}/status")

    assert response.status_code == 404


def test_invalid_correction_id_returns_404(monkeypatch):
    """유효하지 않은 correction_id는 404를 반환한다."""
    cc = DummyCorrectionClient()
    client = _create_client(monkeypatch, cc)

    response = client.get("/api/v1/corrections/not-a-number/status")

    assert response.status_code == 404


# ------------------------------------------------------------------
# RAG 시작
# ------------------------------------------------------------------


def test_start_rag_returns_202(monkeypatch):
    """RAG 시작이 성공하면 202를 반환한다."""
    cc = DummyCorrectionClient()
    service = DummyCorrectionService()
    client = _create_client(monkeypatch, cc, service)

    response = client.post(f"/api/v1/corrections/{CORRECTION_ID}/rag")

    assert response.status_code == 202
    assert service.rag_started is True


def test_start_rag_returns_409_on_invalid_transition(monkeypatch):
    """RAG 시작 시 상태 전이 규칙 위반이면 409를 반환한다."""
    cc = DummyCorrectionClient()
    service = DummyCorrectionService(
        rag_error=base_client_module.MainServerError(
            status_code=422,
            detail="유효하지 않은 상태 전이",
            error_code="CORRECTION4221",
        )
    )
    client = _create_client(monkeypatch, cc, service)

    response = client.post(f"/api/v1/corrections/{CORRECTION_ID}/rag")

    assert response.status_code == 409


# ------------------------------------------------------------------
# 기업 분석
# ------------------------------------------------------------------


def test_get_company_insight_returns_200(monkeypatch):
    """기업 분석 조회가 성공하면 200을 반환한다."""
    cc = DummyCorrectionClient(
        correction={"id": 123, "status": "COMPANY_INSIGHT", "companyInsight": "분석 내용"}
    )
    client = _create_client(monkeypatch, cc)

    response = client.get(f"/api/v1/corrections/{CORRECTION_ID}/company-insight")

    assert response.status_code == 200
    assert response.json()["company_insight"] == "분석 내용"


def test_get_company_insight_returns_409_when_none(monkeypatch):
    """companyInsight가 None이면 409를 반환한다."""
    cc = DummyCorrectionClient(
        correction={"id": 123, "status": "NOT_STARTED", "companyInsight": None}
    )
    client = _create_client(monkeypatch, cc)

    response = client.get(f"/api/v1/corrections/{CORRECTION_ID}/company-insight")

    assert response.status_code == 409


def test_get_company_insight_dict_serialized_to_json(monkeypatch):
    """companyInsight가 dict이면 JSON 문자열로 변환하여 반환한다."""
    cc = DummyCorrectionClient(
        correction={
            "id": 123,
            "status": "COMPANY_INSIGHT",
            "companyInsight": {"summary": "요약"},
        }
    )
    client = _create_client(monkeypatch, cc)

    response = client.get(f"/api/v1/corrections/{CORRECTION_ID}/company-insight")

    assert response.status_code == 200
    assert '"summary"' in response.json()["company_insight"]


def test_update_company_insight_returns_200(monkeypatch):
    """기업 분석 수정이 성공하면 200을 반환한다."""
    cc = DummyCorrectionClient(correction={"id": 123, "status": "COMPANY_INSIGHT"})
    client = _create_client(monkeypatch, cc)

    response = client.patch(
        f"/api/v1/corrections/{CORRECTION_ID}/company-insight",
        json={"company_insight": "수정 내용"},
    )

    assert response.status_code == 200
    assert cc.updated_company_insight == (123, "수정 내용")


# ------------------------------------------------------------------
# 강조 포인트
# ------------------------------------------------------------------


def test_update_emphasis_points_returns_200(monkeypatch):
    """강조 포인트 수정이 성공하면 200을 반환한다."""
    cc = DummyCorrectionClient(correction={"id": 123, "status": "COMPANY_INSIGHT"})
    client = _create_client(monkeypatch, cc)

    response = client.patch(
        f"/api/v1/corrections/{CORRECTION_ID}/emphasis-points",
        json={"emphasis_points": "새 포인트"},
    )

    assert response.status_code == 200
    assert cc.updated_emphasis_points == (123, "새 포인트")


# ------------------------------------------------------------------
# 첨삭 생성
# ------------------------------------------------------------------


def test_start_generation_returns_202(monkeypatch):
    """첨삭 생성 시작이 성공하면 202를 반환한다."""
    cc = DummyCorrectionClient()
    service = DummyCorrectionService()
    client = _create_client(monkeypatch, cc, service)

    response = client.post(f"/api/v1/corrections/{CORRECTION_ID}/generate")

    assert response.status_code == 202
    assert service.generation_started is True


def test_start_generation_returns_409_on_invalid_transition(monkeypatch):
    """생성 시작 시 상태 전이 규칙 위반이면 409를 반환한다."""
    cc = DummyCorrectionClient()
    service = DummyCorrectionService(
        gen_error=base_client_module.MainServerError(
            status_code=422,
            detail="유효하지 않은 상태 전이",
            error_code="CORRECTION4221",
        )
    )
    client = _create_client(monkeypatch, cc, service)

    response = client.post(f"/api/v1/corrections/{CORRECTION_ID}/generate")

    assert response.status_code == 409


# ------------------------------------------------------------------
# 재시도
# ------------------------------------------------------------------


def test_retry_returns_202(monkeypatch):
    """재시도가 성공하면 202를 반환한다."""
    cc = DummyCorrectionClient()
    service = DummyCorrectionService()
    client = _create_client(monkeypatch, cc, service)

    response = client.post(f"/api/v1/corrections/{CORRECTION_ID}/retry")

    assert response.status_code == 202
    assert service.retry_started is True


def test_retry_returns_409_when_not_failed(monkeypatch):
    """실패 상태가 아니면 재시도 시 409를 반환한다."""
    cc = DummyCorrectionClient()
    service = DummyCorrectionService(
        retry_error=ValueError("실패 상태가 아닌 첨삭은 재시도할 수 없습니다")
    )
    client = _create_client(monkeypatch, cc, service)

    response = client.post(f"/api/v1/corrections/{CORRECTION_ID}/retry")

    assert response.status_code == 409


# ------------------------------------------------------------------
# 삭제
# ------------------------------------------------------------------


def test_delete_correction_returns_204(monkeypatch):
    """첨삭 삭제가 성공하면 204를 반환한다."""
    cc = DummyCorrectionClient()
    client = _create_client(monkeypatch, cc)

    response = client.delete(f"/api/v1/corrections/{CORRECTION_ID}")

    assert response.status_code == 204
    assert cc.deleted_id == 123
