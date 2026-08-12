"""경험정리 API·SSE 계약 테스트 (mock graph)

실제 그래프 없이 **API 계약과 이벤트 순서**만 검증합니다. 노드는 3.11~3.17 에서
붙습니다.

세션·요청 상태는 실제 PostgreSQL 을 씁니다. 멱등성 5분기는 DB 제약이 만드는
동작이라 mock 으로는 검증되지 않습니다. DB 가 없으면 skip 합니다.
"""

import json
import time
import uuid

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.middleware.experience_map_ticket import ExperienceMapTicketMiddleware
from features.experience_map import service as service_module
from features.experience_map.graph_runner import MockGraphRunner
from features.experience_map.repository import ExperienceMapRepository
from features.experience_map.service import ExperienceMapService

SECRET = "experience-map-api-test-secret-32bytes"
USER_ID_BASE = 9_600_000


def make_ticket(user_id: str, session_id: str, *, expires_in: int = 300) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "sid": session_id, "iat": now, "exp": now + expires_in},
        SECRET,
        algorithm="HS256",
    )


@pytest.fixture(autouse=True)
def reset_sse_app_status():
    """`sse_starlette` 의 모듈 레벨 이벤트를 테스트마다 초기화한다.

    `AppStatus.should_exit_event` 은 클래스 속성이라 첫 스트림에서 만들어진 뒤
    계속 재사용된다. 테스트마다 이벤트 루프가 새로 뜨므로 두 번째 스트림부터
    "bound to a different event loop" 가 난다.
    """
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


@pytest.fixture
def api_user_id(request) -> str:
    return str(USER_ID_BASE + (abs(hash(request.node.nodeid)) % 300_000))


@pytest_asyncio.fixture
async def service(clean_db) -> ExperienceMapService:
    """실제 DB + mock graph 로 구성한 서비스"""
    return ExperienceMapService(
        repository=ExperienceMapRepository(clean_db, lease_seconds=300),
        runner=MockGraphRunner(),
    )


@pytest_asyncio.fixture
async def client(service, monkeypatch):
    """경험정리 라우터만 얹은 앱. 티켓 미들웨어를 포함한다.

    `TestClient` 대신 `httpx.AsyncClient` 를 쓴다. `TestClient` 는 앱을 별도
    이벤트 루프에서 돌려 asyncpg 풀이 다른 루프에 묶인다.
    """
    from app.api.v1.experience_map import router

    monkeypatch.setattr(service_module, "_service", service)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.add_middleware(ExperienceMapTicketMiddleware, secret_provider=lambda: SECRET)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


def read_events(response) -> list[dict]:
    """SSE 응답 본문을 이벤트 목록으로 파싱한다."""
    events = []
    for block in response.text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:") :].strip()))
    return events


def chat_form(request_id: str, message: str = "결제 실패 문제를 해결한 내용을 정리해줘") -> dict:
    return {"request": json.dumps({"request_id": request_id, "user_message": message})}


# ===== 세션 =====


@pytest.mark.asyncio
async def test_create_session(client, api_user_id):
    """메인 서버가 X-API-Key 로 호출한다. 티켓 경로가 아니다."""
    response = await client.post("/api/v1/experience-map/sessions", json={"user_id": api_user_id})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    uuid.UUID(body["session_id"])


@pytest.mark.asyncio
async def test_create_session_is_idempotent(client, api_user_id):
    first = await client.post("/api/v1/experience-map/sessions", json={"user_id": api_user_id})
    second = await client.post("/api/v1/experience-map/sessions", json={"user_id": api_user_id})

    assert first.json()["session_id"] == second.json()["session_id"]


@pytest.mark.asyncio
async def test_create_session_rejects_bad_user_id(client):
    response = await client.post("/api/v1/experience-map/sessions", json={"user_id": "user-abc"})

    assert response.status_code == 422


@pytest_asyncio.fixture
async def session(client, api_user_id) -> tuple[str, str]:
    """(session_id, Authorization 헤더값)"""
    response = await client.post("/api/v1/experience-map/sessions", json={"user_id": api_user_id})
    session_id = response.json()["session_id"]
    return session_id, f"Bearer {make_ticket(api_user_id, session_id)}"


# ===== 인증 =====


@pytest.mark.asyncio
async def test_stream_requires_ticket(client, session):
    session_id, _ = session

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(str(uuid.uuid4())),
    )

    assert response.status_code == 401
    assert response.json()["code"] == "ticket_invalid"


@pytest.mark.asyncio
async def test_stream_rejects_other_session_ticket(client, session, api_user_id):
    """서명이 유효해도 다른 세션 경로에는 쓸 수 없다."""
    session_id, _ = session
    other = f"Bearer {make_ticket(api_user_id, str(uuid.uuid4()))}"

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(str(uuid.uuid4())),
        headers={"Authorization": other},
    )

    assert response.status_code == 403


# ===== SSE 이벤트 순서 =====


@pytest.mark.asyncio
async def test_chat_stream_event_order(client, session):
    """API 명세 6절의 정상 커밋 이벤트 순서를 따른다."""
    session_id, auth = session

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(str(uuid.uuid4())),
        headers={"Authorization": auth},
    )

    assert response.status_code == 200
    types = [e["type"] for e in read_events(response)]

    assert types[0] == "processing_started"
    assert types[-1] == "processing_complete"

    # node_status 를 걷어내면 명세의 골격만 남는다.
    skeleton = [t for t in types if t != "node_status"]
    assert skeleton == [
        "processing_started",
        "commit_result",
        "message_complete",
        "suggestion_ready",
        "message_complete",
        "processing_complete",
    ]


@pytest.mark.asyncio
async def test_message_complete_kinds(client, session):
    """결과 메시지가 먼저, 제안 메시지가 뒤따른다."""
    session_id, auth = session

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(str(uuid.uuid4())),
        headers={"Authorization": auth},
    )

    kinds = [
        e["message"]["response_kind"]
        for e in read_events(response)
        if e["type"] == "message_complete"
    ]
    assert kinds == ["result", "suggestion"]


@pytest.mark.asyncio
async def test_sse_headers(client, session):
    session_id, auth = session

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(str(uuid.uuid4())),
        headers={"Authorization": auth},
    )

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


# ===== 스트림 시작 전 오류는 JSON =====


@pytest.mark.asyncio
async def test_missing_message_and_files_is_json_error(client, session):
    """스트림을 열기 전에 거부하므로 SSE 가 아니라 JSON 이다."""
    session_id, auth = session

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data={"request": json.dumps({"request_id": str(uuid.uuid4())})},
        headers={"Authorization": auth},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_bad_request_id_is_json_error(client, session):
    session_id, auth = session

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data={"request": json.dumps({"request_id": "not-a-uuid", "user_message": "안녕"})},
        headers={"Authorization": auth},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_error_response_shape(client, session):
    """API 명세 2-3 의 오류 JSON 형식"""
    session_id, auth = session

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data={"request": json.dumps({"request_id": str(uuid.uuid4())})},
        headers={"Authorization": auth},
    )

    assert set(response.json()) == {"statusCode", "code", "message"}


# ===== 멱등성 (API 명세 2-5) =====


@pytest.mark.asyncio
async def test_completed_request_replays_stored_events(client, session):
    """완료된 요청에 다시 붙으면 저장 결과를 재전송한다."""
    session_id, auth = session
    request_id = str(uuid.uuid4())
    form = chat_form(request_id)

    first = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=form,
        headers={"Authorization": auth},
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=form,
        headers={"Authorization": auth},
    )

    assert second.status_code == 200
    types = [e["type"] for e in read_events(second)]
    assert "node_status" not in types  # 그래프를 다시 돌리지 않는다
    assert types == [
        "processing_started",
        "commit_result",
        "message_complete",
        "suggestion_ready",
        "message_complete",
        "processing_complete",
    ]


@pytest.mark.asyncio
async def test_same_request_id_different_input_conflicts(client, session):
    session_id, auth = session
    request_id = str(uuid.uuid4())

    await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(request_id, "첫 번째 내용"),
        headers={"Authorization": auth},
    )
    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(request_id, "다른 내용"),
        headers={"Authorization": auth},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"


# ===== 상태 조회 =====


@pytest.mark.asyncio
async def test_session_state_after_completion(client, session):
    session_id, auth = session
    request_id = str(uuid.uuid4())
    await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(request_id),
        headers={"Authorization": auth},
    )

    response = await client.get(
        f"/api/v1/experience-map/sessions/{session_id}/state", headers={"Authorization": auth}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["active_request_id"] == request_id
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_request_state_recovers_stored_result(client, session):
    """SSE 가 끊겨도 결과를 다시 가져올 수 있다."""
    session_id, auth = session
    request_id = str(uuid.uuid4())
    await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(request_id),
        headers={"Authorization": auth},
    )

    response = await client.get(
        f"/api/v1/experience-map/sessions/{session_id}/requests/{request_id}",
        headers={"Authorization": auth},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["map_version"] == 43
    assert body["suggestion"]["message"]


@pytest.mark.asyncio
async def test_unknown_request_is_404(client, session):
    session_id, auth = session

    response = await client.get(
        f"/api/v1/experience-map/sessions/{session_id}/requests/{uuid.uuid4()}",
        headers={"Authorization": auth},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "request_not_found"


# ===== 재시도 =====


@pytest.mark.asyncio
async def test_retry_rejects_completed_request_as_replay(client, session):
    """완료된 요청을 재시도하면 저장 결과를 돌려준다."""
    session_id, auth = session
    request_id = str(uuid.uuid4())
    await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/chat/stream",
        data=chat_form(request_id),
        headers={"Authorization": auth},
    )

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/retry/stream",
        json={"request_id": request_id},
        headers={"Authorization": auth},
    )

    assert response.status_code == 200
    assert "node_status" not in [e["type"] for e in read_events(response)]


@pytest.mark.asyncio
async def test_retry_unknown_request_is_404(client, session):
    session_id, auth = session

    response = await client.post(
        f"/api/v1/experience-map/sessions/{session_id}/retry/stream",
        json={"request_id": str(uuid.uuid4())},
        headers={"Authorization": auth},
    )

    assert response.status_code == 404


# ===== feature flag =====


def test_router_not_registered_when_flag_disabled(monkeypatch):
    """flag 가 꺼져 있으면 경로 자체가 없다."""
    import importlib

    from features.experience_map import config

    monkeypatch.delenv("EXPERIENCE_MAP_ENABLED", raising=False)
    config.reset_settings()

    import app.api.v1 as api_v1

    reloaded = importlib.reload(api_v1)
    paths = {route.path for route in reloaded.router.routes}

    assert not any("experience-map" in path for path in paths)

    # 다른 테스트에 영향을 주지 않도록 되돌린다.
    config.reset_settings()
    importlib.reload(api_v1)
