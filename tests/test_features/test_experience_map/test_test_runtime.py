"""경험정리 수동 테스트 UI 전용 맵 런타임 테스트"""

import pytest

from features.experience_map.test_runtime import InMemoryTestMapStore


@pytest.mark.asyncio
async def test_test_map_store_applies_update_to_selected_block():
    """수정 operation은 테스트 전용 맵에 반영되고 version을 올린다."""
    store = InMemoryTestMapStore()
    before = await store.snapshot("9000001")

    updated = await store.commit(
        {
            "user_id": "9000001",
            "request_id": "550e8400-e29b-41d4-a716-446655440000",
            "alias_to_block_id": {"b_2": "301"},
            "commit_items": [
                {
                    "item_id": "update_1",
                    "action": "update",
                    "target_ref": "b_2",
                    "text": "GA4 퍼널에서 이탈 구간을 확인해 개선 목표를 설정했다.",
                }
            ],
        }
    )
    after = await store.snapshot("9000001")

    assert before.map_version == 1
    assert after.map_version == 2
    assert after.block_contents()["301"] == "GA4 퍼널에서 이탈 구간을 확인해 개선 목표를 설정했다."
    assert updated["commit_result"]["applied"][0]["block_id"] == "301"
