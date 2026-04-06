"""인터뷰 API 테스트"""

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1 import interview as interview_api


class DummyInterviewService:
    def __init__(self, missing_session_ids: set[str] | None = None):
        self.missing_session_ids = missing_session_ids or set()
        self.process_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    async def create_session_stream(self, user_id: str, session_id: str, experience_name: str):
        assert user_id
        assert session_id
        assert experience_name
        yield {"event": "message_complete", "data": "{}"}

    async def process_message(
        self,
        session_id: str,
        message: str,
        files: list[dict] | None = None,
        mentioned_insight: str | None = None,
    ) -> dict:
        self.process_calls.append(
            {
                "session_id": session_id,
                "message": message,
                "files": files,
                "mentioned_insight": mentioned_insight,
            }
        )

        if session_id in self.missing_session_ids:
            raise ValueError(f"세션을 찾을 수 없습니다: {session_id}")

        return {
            "ai_response": "응답입니다.",
            "current_stage": 1,
            "stage_progress": {
                "fixed_q_used": 1,
                "fixed_q_total": 3,
                "generated_q_used": 0,
                "generated_q_max": 2,
                "force_all_generated_q": False,
                "is_complete": False,
            },
            "overall_completion": 25.0,
            "all_complete": False,
            "is_extended_mode": False,
            "extension_turns_used": None,
            "extension_turns_max": None,
        }

    async def process_message_stream(
        self,
        session_id: str,
        message: str,
        files: list[dict] | None = None,
        mentioned_insight: str | None = None,
    ):
        self.stream_calls.append(
            {
                "session_id": session_id,
                "message": message,
                "files": files,
                "mentioned_insight": mentioned_insight,
            }
        )

        if session_id in self.missing_session_ids:
            raise ValueError(f"세션을 찾을 수 없습니다: {session_id}")

        yield {
            "event": "message_complete",
            "data": json.dumps({"type": "message_complete", "message": {}}),
        }

    async def get_session_state(self, session_id: str):
        if session_id in self.missing_session_ids:
            return None

        return {
            "session_id": session_id,
            "user_id": "user-1",
            "experience_name": "프로젝트",
            "status": "completed",
            "turn_number": 2,
            "current_stage": 1,
            "stage_progress": {
                "fixed_q_used": 1,
                "fixed_q_total": 3,
                "generated_q_used": 0,
                "generated_q_max": 2,
                "force_all_generated_q": False,
                "is_complete": False,
            },
            "overall_completion_percentage": 25.0,
            "all_stages_complete": False,
            "is_extended_mode": False,
            "collected_data": {"stage_1": {}},
            "insight_turn_history": [
                {
                    "turn_number": 1,
                    "user_message": "첫 답변",
                    "mentioned_insight": "insight-1",
                    "insights": [
                        {
                            "id": "insight-1",
                            "title": "문제 해결 경험",
                            "activity_name": "해커톤",
                            "category": "문제해결",
                            "content": "문제를 해결했습니다.",
                            "similarity_score": 0.91,
                            "source": "search",
                        }
                    ],
                }
            ],
            "file_turn_history": [
                {
                    "turn_number": 1,
                    "files": [
                        {
                            "filename": "portfolio.pdf",
                            "content_type": "application/pdf",
                            "file_size": 524288,
                        }
                    ],
                }
            ],
            "messages": [],
            "mentioned_insight": None,
            "retrieved_insights": [],
        }

    async def get_session_status(self, session_id: str):
        if session_id in self.missing_session_ids:
            return None

        return {
            "session_id": session_id,
            "status": "completed",
            "current_stage": 1,
            "all_complete": False,
        }


def _create_client(monkeypatch, service: DummyInterviewService | None = None) -> TestClient:
    monkeypatch.setattr(
        interview_api, "get_interview_service", lambda: service or DummyInterviewService()
    )
    monkeypatch.setattr(interview_api, "uuid4", lambda: "test-session-id")
    app = FastAPI()
    app.include_router(interview_api.router, prefix="/api/v1")
    return TestClient(app)


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


def test_create_session_stream_includes_session_id_header(monkeypatch):
    """세션 생성 SSE 응답 헤더에 session_id를 포함한다."""
    client = _create_client(monkeypatch)

    response = client.post(
        "/api/v1/interview/sessions/stream",
        json={"user_id": "user-1", "experience_name": "프로젝트"},
    )

    assert response.status_code == 201
    assert response.headers["x-session-id"] == "test-session-id"


def test_extend_routes_exist():
    """연장 API 라우트가 등록되어 있는지 확인"""
    extend_methods = None
    extend_stream_methods = None

    for route in interview_api.router.routes:
        if route.path == "/interview/sessions/{session_id}/extend":
            extend_methods = getattr(route, "methods", set())
        elif route.path == "/interview/sessions/{session_id}/extend/stream":
            extend_stream_methods = getattr(route, "methods", set())

    assert extend_methods is not None
    assert "POST" in extend_methods
    assert extend_stream_methods is not None
    assert "POST" in extend_stream_methods


def test_get_session_state_includes_turn_history(monkeypatch):
    """세션 상태 응답에 turn_number와 인사이트 턴 히스토리를 포함한다."""
    client = _create_client(monkeypatch)

    response = client.get("/api/v1/interview/sessions/session-1/state")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["turn_number"] == 2
    assert body["insight_turn_history"][0]["turn_number"] == 1
    assert body["insight_turn_history"][0]["user_message"] == "첫 답변"
    assert body["insight_turn_history"][0]["insights"][0]["activity_name"] == "해커톤"
    assert body["file_turn_history"] == [
        {
            "turn_number": 1,
            "files": [
                {
                    "filename": "portfolio.pdf",
                    "content_type": "application/pdf",
                    "file_size": 524288,
                }
            ],
        }
    ]


def test_get_session_status_returns_compact_payload(monkeypatch):
    """세션 경량 상태 조회 응답에 status 핵심 필드를 포함한다."""
    client = _create_client(monkeypatch)

    response = client.get("/api/v1/interview/sessions/session-1/status")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": "session-1",
        "status": "completed",
        "current_stage": 1,
        "all_complete": False,
    }


def test_get_session_status_returns_404_when_missing(monkeypatch):
    """존재하지 않는 세션의 경량 상태 조회는 404를 반환한다."""
    client = _create_client(monkeypatch, DummyInterviewService({"missing-session"}))

    response = client.get("/api/v1/interview/sessions/missing-session/status")

    assert response.status_code == 404
    assert response.json()["detail"] == "세션을 찾을 수 없습니다: missing-session"


def test_chat_accepts_multipart_form_and_passes_file_payloads(monkeypatch, tmp_path):
    """chat 엔드포인트는 multipart 업로드를 FilePayload 리스트로 서비스에 전달한다."""
    service = DummyInterviewService()
    client = _create_client(monkeypatch, service)
    temp_paths = iter([tmp_path / "interview-upload-1.pdf", tmp_path / "interview-upload-2.jpg"])
    monkeypatch.setattr(interview_api, "_create_temp_upload_file", lambda _suffix: next(temp_paths))

    response = client.post(
        "/api/v1/interview/sessions/session-1/chat",
        data={"message": "안녕하세요", "mentioned_insight": "insight-1"},
        files=[
            ("files", ("portfolio.pdf", b"%PDF-1.4", "application/pdf")),
            ("files", ("image.jpg", b"jpeg-bytes", "image/jpeg")),
        ],
    )

    assert response.status_code == 200
    assert service.process_calls[0]["message"] == "안녕하세요"
    assert service.process_calls[0]["mentioned_insight"] == "insight-1"
    assert service.process_calls[0]["files"] == [
        {
            "filename": "portfolio.pdf",
            "content_type": "application/pdf",
            "temp_path": str(tmp_path / "interview-upload-1.pdf"),
            "file_size": len(b"%PDF-1.4"),
        },
        {
            "filename": "image.jpg",
            "content_type": "image/jpeg",
            "temp_path": str(tmp_path / "interview-upload-2.jpg"),
            "file_size": len(b"jpeg-bytes"),
        },
    ]
    assert (tmp_path / "interview-upload-1.pdf").exists() is False
    assert (tmp_path / "interview-upload-2.jpg").exists() is False
    assert "file_turn_history" not in response.json()


def test_chat_cleans_up_temp_files_when_service_raises(monkeypatch, tmp_path):
    """서비스 오류가 나도 임시 업로드 파일은 정리한다."""
    client = _create_client(monkeypatch, DummyInterviewService({"missing-session"}))
    temp_file_path = tmp_path / "interview-upload-1.pdf"
    monkeypatch.setattr(interview_api, "_create_temp_upload_file", lambda _suffix: temp_file_path)

    response = client.post(
        "/api/v1/interview/sessions/missing-session/chat",
        data={"message": "안녕하세요"},
        files=[("files", ("portfolio.pdf", b"%PDF-1.4", "application/pdf"))],
    )

    assert response.status_code == 404
    assert temp_file_path.exists() is False


@pytest.mark.asyncio
async def test_chat_stream_cleans_up_temp_files_after_response(monkeypatch, tmp_path):
    """chat_stream 제너레이터 소비가 끝나면 임시 업로드 파일을 정리한다."""
    service = DummyInterviewService()
    temp_file_path = tmp_path / "interview-upload-1.pdf"
    monkeypatch.setattr(interview_api, "_create_temp_upload_file", lambda _suffix: temp_file_path)
    monkeypatch.setattr(interview_api, "get_interview_service", lambda: service)

    class _FakeEventSourceResponse:
        def __init__(self, content, headers):
            self.content = content
            self.headers = headers

    monkeypatch.setattr(interview_api, "EventSourceResponse", _FakeEventSourceResponse)

    upload = ChunkedUploadFile(
        filename="portfolio.pdf",
        content_type="application/pdf",
        chunks=[b"%PDF-1.4"],
    )

    response = await interview_api.chat_stream(
        session_id="session-1",
        message="안녕하세요",
        files=[upload],
    )

    events = [event async for event in response.content]

    assert events
    assert service.stream_calls[0]["files"] == [
        {
            "filename": "portfolio.pdf",
            "content_type": "application/pdf",
            "temp_path": str(temp_file_path),
            "file_size": len(b"%PDF-1.4"),
        }
    ]
    assert temp_file_path.exists() is False


@pytest.mark.asyncio
async def test_read_and_validate_files_stores_temp_file_and_closes_upload(tmp_path, monkeypatch):
    """업로드 파일은 chunk 단위로 읽고 모두 close한다."""
    upload = ChunkedUploadFile(
        filename="portfolio.pdf",
        content_type="application/pdf",
        chunks=[b"%PDF", b"-1.4"],
    )
    temp_file_path = tmp_path / "portfolio.pdf"
    monkeypatch.setattr(
        interview_api,
        "_create_temp_upload_file",
        lambda _suffix: temp_file_path,
    )

    payloads = await interview_api._read_and_validate_files([upload])

    assert payloads == [
        {
            "filename": "portfolio.pdf",
            "content_type": "application/pdf",
            "temp_path": str(temp_file_path),
            "file_size": len(b"%PDF-1.4"),
        }
    ]
    assert upload.read_sizes == [1024 * 1024, 1024 * 1024, 1024 * 1024]
    assert upload.closed is True
    assert temp_file_path.read_bytes() == b"%PDF-1.4"


@pytest.mark.asyncio
async def test_read_and_validate_files_rejects_too_many_files():
    """한 턴 최대 파일 개수를 초과하면 400 에러를 반환한다."""
    uploads = [
        ChunkedUploadFile(
            filename=f"file-{index}.pdf", content_type="application/pdf", chunks=[b"a"]
        )
        for index in range(4)
    ]

    with pytest.raises(HTTPException) as exc_info:
        await interview_api._read_and_validate_files(uploads)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "파일은 한 번에 최대 3개까지 업로드할 수 있습니다."
    assert all(upload.closed for upload in uploads)


@pytest.mark.asyncio
async def test_read_and_validate_files_rejects_invalid_content_type():
    """허용되지 않은 content-type은 400 에러를 반환한다."""
    upload = ChunkedUploadFile(
        filename="portfolio.gif",
        content_type="image/gif",
        chunks=[b"gif"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await interview_api._read_and_validate_files([upload])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "PDF, PNG, JPG(JPEG) 파일만 업로드할 수 있습니다."
    assert upload.closed is True


@pytest.mark.asyncio
async def test_read_and_validate_files_rejects_invalid_extension():
    """허용되지 않은 확장자는 400 에러를 반환한다."""
    upload = ChunkedUploadFile(
        filename="portfolio.txt",
        content_type="application/pdf",
        chunks=[b"%PDF"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await interview_api._read_and_validate_files([upload])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "PDF, PNG, JPG(JPEG) 파일만 업로드할 수 있습니다."
    assert upload.closed is True


@pytest.mark.asyncio
async def test_read_and_validate_files_rejects_file_too_large(tmp_path, monkeypatch):
    """10MB를 초과하면 읽기 중 즉시 400 에러를 반환한다."""
    upload = ChunkedUploadFile(
        filename="portfolio.pdf",
        content_type="application/pdf",
        chunks=[b"a" * (10 * 1024 * 1024), b"b"],
    )
    temp_file_path = tmp_path / "portfolio.pdf"
    monkeypatch.setattr(
        interview_api,
        "_create_temp_upload_file",
        lambda _suffix: temp_file_path,
    )

    with pytest.raises(HTTPException) as exc_info:
        await interview_api._read_and_validate_files([upload])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "portfolio.pdf 크기는 10MB를 초과할 수 없습니다."
    assert upload.closed is True
    assert temp_file_path.exists() is False


@pytest.mark.asyncio
async def test_read_and_validate_files_cleans_up_previous_temp_files_on_partial_failure(
    tmp_path, monkeypatch
):
    """여러 파일 중 하나가 실패하면 앞서 저장한 임시 파일도 함께 정리한다."""
    uploads = [
        ChunkedUploadFile(
            filename="portfolio.pdf",
            content_type="application/pdf",
            chunks=[b"%PDF-1.4"],
        ),
        ChunkedUploadFile(
            filename="image.jpg",
            content_type="image/jpeg",
            chunks=[b"a" * (10 * 1024 * 1024), b"b"],
        ),
    ]
    temp_paths = iter([tmp_path / "portfolio.pdf", tmp_path / "image.jpg"])
    monkeypatch.setattr(interview_api, "_create_temp_upload_file", lambda _suffix: next(temp_paths))

    with pytest.raises(HTTPException) as exc_info:
        await interview_api._read_and_validate_files(uploads)

    assert exc_info.value.status_code == 400
    assert (tmp_path / "portfolio.pdf").exists() is False
    assert (tmp_path / "image.jpg").exists() is False
