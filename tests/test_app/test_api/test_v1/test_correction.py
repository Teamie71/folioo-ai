"""첨삭 API 테스트 (httpx 클라이언트 기반)"""

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1 import correction as correction_api
from common.clients import base_client as base_client_module
from common.clients import correction_client as correction_client_module
from features.portfolio.pdf_extraction.schemas import (
    PdfActivity,
    PdfExtractionResult,
    PdfProblemSolvingItem,
)
from features.portfolio.pdf_extraction.service import PdfExtractionService

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


class DummyPdfExtractionClient:
    """PDF 추출 서비스 테스트용 콜백 클라이언트"""

    def __init__(self) -> None:
        self.completed_calls: list[tuple[int, list[dict], str]] = []
        self.failed_calls: list[tuple[int, str]] = []

    async def complete_pdf_extraction(
        self,
        correction_id: int,
        activities: list[dict],
        source_type: str,
    ) -> dict:
        self.completed_calls.append((correction_id, activities, source_type))
        return {"id": correction_id}

    async def fail_pdf_extraction(self, correction_id: int, error_message: str) -> dict:
        self.failed_calls.append((correction_id, error_message))
        return {"id": correction_id}


class DummyPdfExtractionGenerator:
    """PDF 추출 서비스 테스트용 generator"""

    def __init__(
        self,
        result: PdfExtractionResult | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._result = result or PdfExtractionResult(
            activities=[
                PdfActivity(
                    activity_name="프로젝트 A",
                    detail=["상세 설명"],
                    responsibility=["담당 업무"],
                    problem_solving=[
                        PdfProblemSolvingItem(
                            no=3,
                            situation="문제 상황",
                            strategy="대응 전략",
                            reason="선택 이유",
                        )
                    ],
                    learning=["배운 점"],
                )
            ]
        )
        self._exc = exc

    def extract(self, _file_bytes: bytes, _filename: str) -> PdfExtractionResult:
        if self._exc is not None:
            raise self._exc
        return self._result


class RecordingPdfService:
    """라우터 단위 테스트용 PDF 서비스 recorder"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start_extraction(
        self,
        correction_id: int,
        file_bytes: bytes,
        filename: str,
        content_type: str | None,
        background_tasks: BackgroundTasks,
    ) -> None:
        self.calls.append(
            {
                "correction_id": correction_id,
                "file_bytes": file_bytes,
                "filename": filename,
                "content_type": content_type,
                "background_tasks": background_tasks,
            }
        )


class ChunkedUploadFile:
    """chunk read 동작 검증용 UploadFile 대역"""

    def __init__(self, *, filename: str, content_type: str | None, chunks: list[bytes]) -> None:
        self.filename = filename
        self.content_type = content_type
        self._chunks = list(chunks)
        self.read_sizes: list[int] = []
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    async def close(self) -> None:
        self.closed = True


def _create_pdf_extraction_service(
    *,
    generator: DummyPdfExtractionGenerator | None = None,
    correction_client: DummyPdfExtractionClient | None = None,
) -> PdfExtractionService:
    return PdfExtractionService(
        correction_client=correction_client or DummyPdfExtractionClient(),
        generator=generator or DummyPdfExtractionGenerator(),
    )


def _create_client(
    monkeypatch,
    correction_client: DummyCorrectionClient,
    service: DummyCorrectionService | None = None,
    pdf_service: PdfExtractionService | None = None,
) -> TestClient:
    monkeypatch.setattr(
        correction_client_module, "get_correction_client", lambda: correction_client
    )
    monkeypatch.setattr(correction_api, "get_correction_client", lambda: correction_client)
    if service is not None:
        monkeypatch.setattr(correction_api, "get_correction_service", lambda: service)
    if pdf_service is not None:
        monkeypatch.setattr(correction_api, "get_pdf_extraction_service", lambda: pdf_service)
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


def test_get_correction_result_returns_multi_portfolio_result(monkeypatch):
    """첨삭 결과 조회 시 다중 포트폴리오 응답 포맷을 반환한다."""
    cc = DummyCorrectionClient(
        correction={
            "id": 123,
            "status": "DONE",
            "result": {
                "portfolio_corrections": [
                    {
                        "portfolio_id": 10,
                        "fields": [
                            {
                                "field_name": "description",
                                "lines": [
                                    {
                                        "line_number": 1,
                                        "original_text": "원문",
                                        "type": "keep",
                                        "comment": None,
                                    }
                                ],
                            },
                            {
                                "field_name": "contributions",
                                "lines": [
                                    {
                                        "line_number": 1,
                                        "original_text": "원문",
                                        "type": "emphasize",
                                        "comment": "강조",
                                    }
                                ],
                            },
                            {
                                "field_name": "achievements",
                                "lines": [
                                    {
                                        "line_number": 1,
                                        "original_text": "원문",
                                        "type": "reduce",
                                        "comment": "축소",
                                    }
                                ],
                            },
                            {
                                "field_name": "insights",
                                "lines": [
                                    {
                                        "line_number": 1,
                                        "original_text": "원문",
                                        "type": "keep",
                                        "comment": None,
                                    }
                                ],
                            },
                        ],
                    }
                ],
                "overall_summary": "전체 총평",
            },
        }
    )
    client = _create_client(monkeypatch, cc)

    response = client.get(f"/api/v1/corrections/{CORRECTION_ID}")

    assert response.status_code == 200
    assert response.json()["result"]["portfolio_corrections"][0]["portfolio_id"] == 10
    assert response.json()["result"]["overall_summary"] == "전체 총평"


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


def test_update_company_insight_rejects_payload_over_2000_chars(monkeypatch):
    """기업 분석 수정은 2000자를 초과한 payload를 422로 거부한다."""
    cc = DummyCorrectionClient(correction={"id": 123, "status": "COMPANY_INSIGHT"})
    client = _create_client(monkeypatch, cc)

    response = client.patch(
        f"/api/v1/corrections/{CORRECTION_ID}/company-insight",
        json={"company_insight": "가" * 2001},
    )

    assert response.status_code == 422
    assert cc.updated_company_insight is None


def test_update_company_insight_openapi_documents_422_response(monkeypatch):
    """기업 분석 수정 API는 요청 검증 실패 422 응답을 문서화한다."""
    cc = DummyCorrectionClient(correction={"id": 123, "status": "COMPANY_INSIGHT"})
    client = _create_client(monkeypatch, cc)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    responses = response.json()["paths"]["/api/v1/corrections/{correction_id}/company-insight"][
        "patch"
    ]["responses"]
    assert responses["422"]["description"] == "요청 본문 검증 실패"
    assert "400" not in responses


@pytest.mark.asyncio
async def test_start_pdf_extraction_reads_in_chunks_before_service_call(monkeypatch):
    """PDF 업로드는 chunk 단위로 읽고 누적 bytes를 서비스에 전달한다."""
    service = RecordingPdfService()
    monkeypatch.setattr(correction_api, "get_pdf_extraction_service", lambda: service)
    upload = ChunkedUploadFile(
        filename="portfolio.pdf",
        content_type="application/pdf",
        chunks=[b"%PDF", b"-1.4", b"-body"],
    )

    response = await correction_api.start_pdf_extraction(
        correction_id=CORRECTION_ID,
        background_tasks=BackgroundTasks(),
        file=upload,
    )

    assert response.status == "accepted"
    assert upload.read_sizes == [1024 * 1024, 1024 * 1024, 1024 * 1024, 1024 * 1024]
    assert upload.closed is True
    assert service.calls[0]["file_bytes"] == b"%PDF-1.4-body"


@pytest.mark.asyncio
async def test_start_pdf_extraction_stops_when_chunk_limit_exceeded(monkeypatch):
    """PDF 업로드는 제한 초과 청크에서 즉시 중단하고 서비스를 호출하지 않는다."""
    service = RecordingPdfService()
    monkeypatch.setattr(correction_api, "get_pdf_extraction_service", lambda: service)
    upload = ChunkedUploadFile(
        filename="portfolio.pdf",
        content_type="application/pdf",
        chunks=[b"a" * (10 * 1024 * 1024), b"b"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await correction_api.start_pdf_extraction(
            correction_id=CORRECTION_ID,
            background_tasks=BackgroundTasks(),
            file=upload,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "PDF 파일 크기는 10MB를 초과할 수 없습니다."
    assert upload.closed is True
    assert service.calls == []


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


# ------------------------------------------------------------------
# PDF 추출
# ------------------------------------------------------------------


def test_start_pdf_extraction_returns_202(monkeypatch):
    """PDF 추출 요청이 유효하면 202를 반환한다."""
    cc = DummyCorrectionClient()
    pdf_service = _create_pdf_extraction_service()
    client = _create_client(monkeypatch, cc, pdf_service=pdf_service)

    response = client.post(
        f"/api/v1/corrections/{CORRECTION_ID}/pdf-extraction",
        files={"file": ("portfolio.txt", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 202
    assert response.json() == {
        "correction_id": CORRECTION_ID,
        "status": "accepted",
        "message": "PDF 추출 요청이 접수되었습니다.",
    }


def test_start_pdf_extraction_returns_400_for_non_pdf(monkeypatch):
    """PDF가 아닌 파일이면 400을 반환한다."""
    cc = DummyCorrectionClient()
    pdf_service = _create_pdf_extraction_service()
    client = _create_client(monkeypatch, cc, pdf_service=pdf_service)

    response = client.post(
        f"/api/v1/corrections/{CORRECTION_ID}/pdf-extraction",
        files={"file": ("portfolio.txt", b"plain text", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "PDF 파일만 업로드할 수 있습니다."


def test_start_pdf_extraction_returns_400_for_empty_file(monkeypatch):
    """빈 파일이면 400을 반환한다."""
    cc = DummyCorrectionClient()
    pdf_service = _create_pdf_extraction_service()
    client = _create_client(monkeypatch, cc, pdf_service=pdf_service)

    response = client.post(
        f"/api/v1/corrections/{CORRECTION_ID}/pdf-extraction",
        files={"file": ("portfolio.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "빈 PDF 파일은 업로드할 수 없습니다."


def test_start_pdf_extraction_returns_400_for_file_too_large(monkeypatch):
    """10MB를 초과한 파일이면 400을 반환한다."""
    cc = DummyCorrectionClient()
    pdf_service = _create_pdf_extraction_service()
    client = _create_client(monkeypatch, cc, pdf_service=pdf_service)

    response = client.post(
        f"/api/v1/corrections/{CORRECTION_ID}/pdf-extraction",
        files={
            "file": (
                "portfolio.pdf",
                b"a" * (10 * 1024 * 1024 + 1),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "PDF 파일 크기는 10MB를 초과할 수 없습니다."


def test_start_pdf_extraction_allows_extension_fallback(monkeypatch):
    """MIME이 일반 바이너리여도 .pdf 확장자면 허용한다."""
    cc = DummyCorrectionClient()
    pdf_service = _create_pdf_extraction_service()
    client = _create_client(monkeypatch, cc, pdf_service=pdf_service)

    response = client.post(
        f"/api/v1/corrections/{CORRECTION_ID}/pdf-extraction",
        files={"file": ("portfolio.pdf", b"%PDF-1.4", "application/octet-stream")},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_start_pdf_extraction_allows_text_plain_extension_fallback(monkeypatch):
    """MIME이 text/plain이어도 .pdf 확장자면 허용한다."""
    cc = DummyCorrectionClient()
    pdf_service = _create_pdf_extraction_service()
    client = _create_client(monkeypatch, cc, pdf_service=pdf_service)

    response = client.post(
        f"/api/v1/corrections/{CORRECTION_ID}/pdf-extraction",
        files={"file": ("portfolio.pdf", b"%PDF-1.4", "text/plain")},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
