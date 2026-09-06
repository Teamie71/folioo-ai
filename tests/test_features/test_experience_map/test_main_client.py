"""경험정리 메인 서버 커밋 API 클라이언트 테스트"""

import httpx
import pytest

from common.http_client import MainServerError
from features.experience_map.errors import (
    CommitRequestIdReusedError,
    MapNotInitializedError,
    MapVersionConflictError,
)
from features.experience_map.main_client import (
    COMMIT_PATH,
    COMMIT_RECOVERY_PATH,
    ExperienceMapMainClient,
)
from features.experience_map.schemas import CommitAddItem


def response(status_code: int, payload: dict) -> httpx.Response:
    """mock transport용 JSON 응답"""
    return httpx.Response(status_code, json=payload, request=httpx.Request("POST", "https://main"))


def commit_payload() -> dict:
    return {
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "previous_version": 42,
        "map_version": 43,
        "applied": [{"item_id": "it_1", "block_id": "3701", "path": "경험 > 문제해결"}],
    }


def commit_item() -> CommitAddItem:
    return CommitAddItem(item_id="it_1", parent_id="3021", content="원인을 분석했습니다.")


class CatalogStub:
    def __init__(self) -> None:
        self.refresh_calls = 0

    async def refresh(self):
        self.refresh_calls += 1


@pytest.mark.asyncio
async def test_commit_posts_contract_payload_without_generic_retry():
    """명세의 request body를 한 번만 보내고 성공 응답을 SSE 모델로 보완한다."""
    calls: list[tuple[str, str, dict]] = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs["json"]))
        return response(200, commit_payload())

    client = ExperienceMapMainClient(request=request, catalog_client=CatalogStub())

    result = await client.commit(
        user_id="123",
        request_id="550e8400-e29b-41d4-a716-446655440000",
        base_map_version=42,
        items=[commit_item()],
    )

    assert result.map_version == 43
    assert result.revert_to_version == 42
    assert result.can_revert is True
    assert calls == [
        (
            "POST",
            COMMIT_PATH,
            {
                "user_id": "123",
                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                "base_map_version": 42,
                "items": [
                    {
                        "item_id": "it_1",
                        "action": "add",
                        "parent_id": "3021",
                        "content": "원인을 분석했습니다.",
                    }
                ],
            },
        )
    ]


@pytest.mark.asyncio
async def test_unknown_slot_refreshes_catalog_and_retries_exactly_once():
    """unknown_slot_id만 카탈로그를 갱신하고 한 번 더 시도한다."""
    calls = 0
    catalog = CatalogStub()

    async def request(method, path, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response(422, {"code": "unknown_slot_id", "message": "알 수 없는 슬롯"})
        return response(200, commit_payload())

    client = ExperienceMapMainClient(request=request, catalog_client=catalog)

    assert (
        await client.commit(
            user_id="123",
            request_id="550e8400-e29b-41d4-a716-446655440000",
            base_map_version=42,
            items=[],
        )
    ).map_version == 43
    assert calls == 2
    assert catalog.refresh_calls == 1


@pytest.mark.asyncio
async def test_unknown_slot_second_failure_is_not_retried_again():
    """강제 갱신 뒤에도 unknown_slot_id면 세 번째 POST는 보내지 않는다."""
    calls = 0
    catalog = CatalogStub()

    async def request(method, path, **kwargs):
        nonlocal calls
        calls += 1
        return response(422, {"code": "unknown_slot_id"})

    client = ExperienceMapMainClient(request=request, catalog_client=catalog)

    with pytest.raises(Exception, match="카탈로그"):
        await client.commit(
            user_id="123",
            request_id="550e8400-e29b-41d4-a716-446655440000",
            base_map_version=42,
            items=[],
        )

    assert calls == 2
    assert catalog.refresh_calls == 1


@pytest.mark.asyncio
async def test_maps_version_conflict_with_current_version():
    async def request(method, path, **kwargs):
        return response(409, {"code": "map_version_conflict", "current_map_version": 45})

    client = ExperienceMapMainClient(request=request, catalog_client=CatalogStub())

    with pytest.raises(MapVersionConflictError) as exc_info:
        await client.commit(
            user_id="123",
            request_id="550e8400-e29b-41d4-a716-446655440000",
            base_map_version=42,
            items=[],
        )

    assert exc_info.value.current_map_version == 45


@pytest.mark.asyncio
async def test_maps_main_server_version_conflict_envelope():
    """메인 서버의 errorCode/reason/details camelCase 오류 응답을 해석한다."""

    async def request(method, path, **kwargs):
        return response(
            409,
            {
                "isSuccess": False,
                "error": {
                    "errorCode": "EXPERIENCE_MAP4091",
                    "reason": "경험 맵 버전이 충돌했습니다.",
                    "details": {"currentMapVersion": "45"},
                    "path": "/api/v1/experience-map/commit",
                },
                "result": None,
            },
        )

    client = ExperienceMapMainClient(request=request, catalog_client=CatalogStub())

    with pytest.raises(MapVersionConflictError) as exc_info:
        await client.commit(
            user_id="123",
            request_id="550e8400-e29b-41d4-a716-446655440000",
            base_map_version=42,
            items=[],
        )

    assert exc_info.value.current_map_version == 45


@pytest.mark.asyncio
async def test_main_server_unknown_slot_envelope_refreshes_catalog():
    """메인 서버의 unknown slot 오류도 카탈로그 갱신 분기로 연결한다."""
    calls = 0
    catalog = CatalogStub()

    async def request(method, path, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response(
                422,
                {
                    "isSuccess": False,
                    "error": {
                        "errorCode": "EXPERIENCE_MAP4223",
                        "reason": "알 수 없는 슬롯입니다.",
                    },
                    "result": None,
                },
            )
        return response(200, commit_payload())

    client = ExperienceMapMainClient(request=request, catalog_client=catalog)

    result = await client.commit(
        user_id="123",
        request_id="550e8400-e29b-41d4-a716-446655440000",
        base_map_version=42,
        items=[],
    )

    assert result.map_version == 43
    assert calls == 2
    assert catalog.refresh_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        ("request_id_reused", CommitRequestIdReusedError),
        ("map_not_initialized", MapNotInitializedError),
    ],
)
async def test_maps_typed_commit_errors(code, error_type):
    async def request(method, path, **kwargs):
        return response(409, {"code": code})

    client = ExperienceMapMainClient(request=request, catalog_client=CatalogStub())

    with pytest.raises(error_type):
        await client.commit(
            user_id="123",
            request_id="550e8400-e29b-41d4-a716-446655440000",
            base_map_version=42,
            items=[],
        )


@pytest.mark.asyncio
async def test_get_commit_recovers_completed_result():
    async def request(method, path, **kwargs):
        assert method == "GET"
        assert path == COMMIT_RECOVERY_PATH.format(
            request_id="550e8400-e29b-41d4-a716-446655440000"
        )
        return response(200, {"committed": True, "result": commit_payload()})

    result = await ExperienceMapMainClient(
        request=request, catalog_client=CatalogStub()
    ).get_commit("550e8400-e29b-41d4-a716-446655440000")

    assert result.committed is True
    assert result.result.map_version == 43


@pytest.mark.asyncio
async def test_get_commit_reports_not_committed():
    async def request(method, path, **kwargs):
        return response(200, {"committed": False})

    result = await ExperienceMapMainClient(
        request=request, catalog_client=CatalogStub()
    ).get_commit("request")

    assert result.committed is False
    assert result.result is None


@pytest.mark.asyncio
async def test_invalid_success_response_raises_main_server_error():
    async def request(method, path, **kwargs):
        return response(200, {"unexpected": True})

    client = ExperienceMapMainClient(request=request, catalog_client=CatalogStub())

    with pytest.raises(MainServerError) as exc_info:
        await client.commit(
            user_id="123",
            request_id="550e8400-e29b-41d4-a716-446655440000",
            base_map_version=42,
            items=[],
        )

    assert exc_info.value.status_code == 502
