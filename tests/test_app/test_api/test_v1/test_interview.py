"""인터뷰 API 테스트"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import interview as interview_api


class DummyInterviewService:
    async def create_session_stream(self, user_id: str, session_id: str, experience_name: str):
        assert user_id
        assert session_id
        assert experience_name
        yield {"event": "message_complete", "data": "{}"}


def _create_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(interview_api, "get_interview_service", lambda: DummyInterviewService())
    monkeypatch.setattr(interview_api, "uuid4", lambda: "test-session-id")
    app = FastAPI()
    app.include_router(interview_api.router, prefix="/api/v1")
    return TestClient(app)


def test_create_session_stream_includes_session_id_header(monkeypatch):
    """세션 생성 SSE 응답 헤더에 session_id를 포함한다."""
    client = _create_client(monkeypatch)

    response = client.post(
        "/api/v1/interview/sessions/stream",
        json={"user_id": "user-1", "experience_name": "프로젝트"},
    )

    assert response.status_code == 201
    assert response.headers["x-session-id"] == "test-session-id"
