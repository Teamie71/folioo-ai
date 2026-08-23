"""Swagger용 경험 맵 로컬 데모 API 테스트."""

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.experience_map_demo import router


@pytest.mark.asyncio
async def test_demo_endpoint_returns_events_and_in_memory_map():
    """데모 API는 외부 서비스 없이 SSE 모델과 가상 맵 변경을 반환한다."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/experience-map/demo/run")

    assert response.status_code == 200
    body = response.json()
    non_status_events = [event["type"] for event in body["events"] if event["type"] != "node_status"]
    assert non_status_events == [
        "commit_result",
        "message_complete",
        "suggestion_ready",
        "message_complete",
    ]
    assert body["map"][-1]["content"] == "결제 오류 원인 분석 → 재시도 로직 추가로 장애 감소"
