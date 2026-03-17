"""인터뷰 API 테스트"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import interview as interview_api


class DummyInterviewService:
    def __init__(self, missing_session_ids: set[str] | None = None):
        self.missing_session_ids = missing_session_ids or set()

    async def create_session_stream(self, user_id: str, session_id: str, experience_name: str):
        assert user_id
        assert session_id
        assert experience_name
        yield {"event": "message_complete", "data": "{}"}

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
