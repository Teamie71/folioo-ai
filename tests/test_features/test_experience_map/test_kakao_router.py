"""카톡 턴 API 라우터 테스트 (서비스 대역)"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.kakao import kakao_router
from features.experience_map.errors import IdempotencyKeyReusedError, InvalidRequestError
from features.experience_map.kakao import set_kakao_service
from features.experience_map.kakao.schemas import KakaoTurnStatusResponse
from features.experience_map.kakao.store import TurnRow

SESSION_ID = str(uuid.uuid4())


def _row(request_id: str, state: str = "ACCEPTED") -> TurnRow:
    return TurnRow(
        request_id=request_id,
        user_id="123",
        session_id=SESSION_ID,
        block_id="12",
        request_hash="h",
        utterance="u",
        expires_at=datetime.now(UTC),
        state=state,
        delivery_status="NOT_ATTEMPTED",
        usage_reported=False,
        complete_notified=False,
    )


class StubService:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.status: KakaoTurnStatusResponse | None = None
        self.accepted: list[tuple[str, object]] = []

    async def accept(self, session_id, request):
        if self.error:
            raise self.error
        self.accepted.append((session_id, request))
        return _row(request.request_id)

    async def get_status(self, request_id, user_id):
        return self.status


@pytest.fixture
def stub():
    service = StubService()
    set_kakao_service(service)
    yield service
    set_kakao_service(None)


@pytest.fixture
def client(stub):
    app = FastAPI()
    app.include_router(kakao_router)
    return TestClient(app)


def _body(**overrides) -> dict:
    data = {
        "user_id": "123",
        "block_id": "12",
        "request_id": str(uuid.uuid4()),
        "utterance": "동아리에서 축제 부스 운영했어요",
        "callback_url": "https://bot-api.kakao.com/v1/x",
        "expires_at": (datetime.now(UTC) + timedelta(seconds=50)).isoformat(),
    }
    return {**data, **overrides}


def test_accept_returns_202(client, stub):
    body = _body()

    response = client.post(f"/sessions/{SESSION_ID}/kakao/turns", json=body)

    assert response.status_code == 202
    assert response.json() == {"request_id": body["request_id"], "state": "ACCEPTED"}
    assert stub.accepted[0][0] == SESSION_ID


def test_accept_conflict_is_409(client, stub):
    stub.error = IdempotencyKeyReusedError()

    response = client.post(f"/sessions/{SESSION_ID}/kakao/turns", json=_body())

    assert response.status_code == 409


def test_accept_rejects_before_creating_work(client, stub):
    stub.error = InvalidRequestError("callback_url 호스트가 허용 목록에 없습니다.")

    response = client.post(f"/sessions/{SESSION_ID}/kakao/turns", json=_body())

    assert response.status_code == 422
    assert "callback" in response.json()["message"]


@pytest.mark.parametrize("overrides", [{"utterance": "가" * 501}, {"request_id": "x"}])
def test_accept_validates_body(client, stub, overrides):
    response = client.post(f"/sessions/{SESSION_ID}/kakao/turns", json=_body(**overrides))

    assert response.status_code == 422
    assert stub.accepted == []


def test_accept_with_malformed_session_id_is_404(client, stub):
    response = client.post("/sessions/not-a-uuid/kakao/turns", json=_body())

    assert response.status_code == 404


def test_status_returns_top_level_json_without_envelope(client, stub):
    rid = str(uuid.uuid4())
    stub.status = KakaoTurnStatusResponse(
        request_id=rid, state="SUCCEEDED", delivery_status="SUCCESS"
    )

    response = client.get(f"/kakao/turns/{rid}", params={"user_id": "123"})

    assert response.status_code == 200
    assert response.json() == {
        "request_id": rid,
        "state": "SUCCEEDED",
        "delivery_status": "SUCCESS",
    }


def test_status_404_when_missing(client, stub):
    response = client.get(f"/kakao/turns/{uuid.uuid4()}", params={"user_id": "123"})

    assert response.status_code == 404


def test_status_requires_user_id(client, stub):
    assert client.get(f"/kakao/turns/{uuid.uuid4()}").status_code == 422
