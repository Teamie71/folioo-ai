"""인터뷰 파일 업로드 API 테스트"""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from app.api.v1 import interview as interview_api


class DummyInterviewService:
    def __init__(self):
        self.stream_calls: list[dict] = []

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

        yield {
            "event": "message_complete",
            "data": json.dumps({"type": "message_complete", "message": {}}),
        }


def _create_client(monkeypatch, service: DummyInterviewService) -> TestClient:
    monkeypatch.setattr(interview_api, "get_interview_service", lambda: service)
    app = FastAPI()
    app.include_router(interview_api.router, prefix="/api/v1")
    return TestClient(app)


def _patch_streaming_response(monkeypatch):
    async def _passthrough_events(stream):
        async for event in stream:
            yield event["data"]

    monkeypatch.setattr(interview_api, "interleave_ping_events", _passthrough_events)
    monkeypatch.setattr(interview_api, "EventSourceResponse", StreamingResponse)


def test_chat_stream_accepts_multipart_with_files(monkeypatch, tmp_path):
    """chat_stream 엔드포인트는 multipart 파일 업로드를 서비스에 전달한다."""
    service = DummyInterviewService()
    client = _create_client(monkeypatch, service)
    temp_paths = iter([tmp_path / "interview-upload-1.pdf", tmp_path / "interview-upload-2.jpg"])
    monkeypatch.setattr(interview_api, "_create_temp_upload_file", lambda _suffix: next(temp_paths))
    _patch_streaming_response(monkeypatch)

    response = client.post(
        "/api/v1/interview/sessions/session-1/chat/stream",
        data={"message": "안녕하세요"},
        files=[
            ("files", ("portfolio.pdf", b"%PDF-1.4", "application/pdf")),
            ("files", ("image.jpg", b"jpeg-bytes", "image/jpeg")),
        ],
    )

    assert response.status_code == 200
    body = response.text
    assert "message_complete" in body
    assert service.stream_calls[0]["files"] == [
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


def test_chat_stream_accepts_file_only_multipart(monkeypatch, tmp_path):
    """chat_stream 엔드포인트는 message 없이 파일만 있어도 허용한다."""
    service = DummyInterviewService()
    client = _create_client(monkeypatch, service)
    temp_file_path = tmp_path / "interview-upload-1.pdf"
    monkeypatch.setattr(interview_api, "_create_temp_upload_file", lambda _suffix: temp_file_path)
    _patch_streaming_response(monkeypatch)

    response = client.post(
        "/api/v1/interview/sessions/session-1/chat/stream",
        files=[("files", ("portfolio.pdf", b"%PDF-1.4", "application/pdf"))],
    )

    assert response.status_code == 200
    assert service.stream_calls[0]["message"] == ""
    assert service.stream_calls[0]["files"] == [
        {
            "filename": "portfolio.pdf",
            "content_type": "application/pdf",
            "temp_path": str(temp_file_path),
            "file_size": len(b"%PDF-1.4"),
        }
    ]
    assert temp_file_path.exists() is False


def test_chat_stream_rejects_request_without_message_and_files(monkeypatch):
    """chat_stream 엔드포인트는 message와 files가 모두 없으면 거부한다."""
    service = DummyInterviewService()
    client = _create_client(monkeypatch, service)

    response = client.post("/api/v1/interview/sessions/session-1/chat/stream", data={})

    assert response.status_code == 400
    assert response.json()["detail"] == "메시지 또는 파일 중 하나는 반드시 전송해야 합니다."
    assert service.stream_calls == []


def test_chat_stream_allows_blank_message_when_files_exist(monkeypatch, tmp_path):
    """chat_stream 엔드포인트는 공백-only message와 파일 조합을 file-only로 처리한다."""
    service = DummyInterviewService()
    client = _create_client(monkeypatch, service)
    temp_file_path = tmp_path / "interview-upload-1.pdf"
    monkeypatch.setattr(interview_api, "_create_temp_upload_file", lambda _suffix: temp_file_path)
    _patch_streaming_response(monkeypatch)

    response = client.post(
        "/api/v1/interview/sessions/session-1/chat/stream",
        data={"message": "   "},
        files=[("files", ("portfolio.pdf", b"%PDF-1.4", "application/pdf"))],
    )

    assert response.status_code == 200
    assert service.stream_calls[0]["message"] == ""
    assert temp_file_path.exists() is False


def test_chat_stream_rejects_more_than_3_files(monkeypatch):
    """chat_stream 엔드포인트는 4개 이상 파일 업로드를 거부한다."""
    service = DummyInterviewService()
    client = _create_client(monkeypatch, service)

    response = client.post(
        "/api/v1/interview/sessions/session-1/chat/stream",
        data={"message": "안녕하세요"},
        files=[
            ("files", ("one.pdf", b"a", "application/pdf")),
            ("files", ("two.pdf", b"a", "application/pdf")),
            ("files", ("three.pdf", b"a", "application/pdf")),
            ("files", ("four.pdf", b"a", "application/pdf")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "파일은 한 번에 최대 3개까지 업로드할 수 있습니다."
    assert service.stream_calls == []


def test_chat_stream_rejects_oversized_file(monkeypatch):
    """chat_stream 엔드포인트는 10MB 초과 파일 업로드를 거부한다."""
    service = DummyInterviewService()
    client = _create_client(monkeypatch, service)

    response = client.post(
        "/api/v1/interview/sessions/session-1/chat/stream",
        data={"message": "안녕하세요"},
        files=[("files", ("large.pdf", b"a" * (10 * 1024 * 1024 + 1), "application/pdf"))],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "large.pdf 크기는 10MB를 초과할 수 없습니다."
    assert service.stream_calls == []


def test_chat_stream_rejects_unsupported_file_type(monkeypatch):
    """chat_stream 엔드포인트는 지원하지 않는 형식의 파일 업로드를 거부한다."""
    service = DummyInterviewService()
    client = _create_client(monkeypatch, service)

    response = client.post(
        "/api/v1/interview/sessions/session-1/chat/stream",
        data={"message": "안녕하세요"},
        files=[("files", ("animated.gif", b"gif-data", "image/gif"))],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "PDF, PNG, JPG(JPEG) 파일만 업로드할 수 있습니다."
    assert service.stream_calls == []


def test_chat_stream_works_without_files(monkeypatch):
    """chat_stream 엔드포인트는 파일 없이도 정상 동작한다."""
    service = DummyInterviewService()
    client = _create_client(monkeypatch, service)
    _patch_streaming_response(monkeypatch)

    response = client.post(
        "/api/v1/interview/sessions/session-1/chat/stream",
        data={"message": "안녕하세요"},
    )

    assert response.status_code == 200
    body = response.text
    assert "message_complete" in body
    assert service.stream_calls[0]["files"] == []
    assert service.stream_calls[0]["message"] == "안녕하세요"
