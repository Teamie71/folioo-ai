"""경험정리 수동 테스트 UI 전용 맵 런타임 테스트"""

import pytest

from features.experience_map.nodes.result_response import _path_parts
from features.experience_map.test_runtime import (
    InMemoryTestMapStore,
    create_test_template_catalog_client,
)


@pytest.mark.asyncio
async def test_test_template_catalog_is_available_without_main_server():
    """테스트 UI는 메인 서버 없이도 구조화 카탈로그를 읽을 수 있어야 한다."""
    catalog = await create_test_template_catalog_client().get_catalog()

    assert catalog.version == "agent-doc-3-0"
    assert catalog.get_slot("PROBLEM_SOLVING.SUMMARY") is not None


@pytest.mark.asyncio
async def test_test_template_catalog_matches_agent_doc_3_0():
    """전체 38개 슬롯(level 4 10개 + level 5 28개)이 실려 있어야 한다.

    예전엔 슬롯 2개짜리 가짜 카탈로그를 썼다. 그러면 담당업무·문제해결의 실제
    하위 템플릿이 하나도 없어서, 테스트 콘솔에서 어떤 입력을 넣어도 세부 템플릿
    슬롯이 적용되는 걸 볼 수 없었다.
    """
    catalog = await create_test_template_catalog_client().get_catalog()
    slots = list(catalog.iter_slots())

    assert len(slots) == 38
    assert len([s for s in slots if s.level == 4]) == 10
    assert len([s for s in slots if s.level == 5]) == 28
    assert {s.slot_id for s in slots if s.is_anchor} == {
        "TASK.SUMMARY",
        "PROBLEM_SOLVING.SUMMARY",
    }

    for slot_id in (
        "DETAIL.MOTIVATION",
        "ACHIEVEMENT.QUANTITATIVE",
        "LEARNING.GROWTH",
        "TASK.BASIC.RESULT",
        "PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE",
        "PROBLEM_SOLVING.TROUBLESHOOTING.SOLUTION",
        "PROBLEM_SOLVING.TROUBLESHOOTING.VERIFICATION",
        "PROBLEM_SOLVING.RECOVERY.CHANGE",
    ):
        assert catalog.get_slot(slot_id) is not None, f"{slot_id} 이 카탈로그에 없습니다."


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


@pytest.mark.asyncio
async def test_applied_path_points_at_the_parent_category():
    """`path`는 블록이 **놓인 자리**다. 블록 자신도, 최상위 루트도 넣지 않는다.

    자신을 넣으면 방금 만든 문장이 카테고리 자리에 들어가
    `"교내 커머스 리뉴얼 > 이탈률이 90% 감소함.에 1개를 정리했어요."` 처럼 읽힌다.
    루트를 넣으면 활동명 자리가 `"프로젝트 경험"`으로 밀린다.
    """
    store = InMemoryTestMapStore()

    updated = await store.commit(
        {
            "user_id": "9000002",
            "request_id": "550e8400-e29b-41d4-a716-446655440001",
            "alias_to_block_id": {"b_5": "400"},
            "commit_items": [
                {
                    "item_id": "add_1",
                    "action": "add",
                    "parent_ref": "b_5",
                    "text": "이탈률이 90% 감소함.",
                }
            ],
        }
    )

    applied = updated["commit_result"]["applied"][0]
    assert applied["path"] == "교내 커머스 리뉴얼 > 성과"

    # 결과 문구까지 확인한다. 경로가 어긋나면 여기서 어색하게 읽힌다.
    experience_name, category = _path_parts(applied["path"])
    assert f"{experience_name} > {category}" == "교내 커머스 리뉴얼 > 성과"


@pytest.mark.asyncio
async def test_applied_path_on_update_uses_the_parent_too():
    """수정도 같은 규칙이다. 대상 블록 자신이 경로에 들어가면 안 된다."""
    store = InMemoryTestMapStore()

    updated = await store.commit(
        {
            "user_id": "9000003",
            "request_id": "550e8400-e29b-41d4-a716-446655440002",
            "alias_to_block_id": {"b_2": "301"},
            "commit_items": [
                {
                    "item_id": "update_1",
                    "action": "update",
                    "target_ref": "b_2",
                    "text": "행사 신청 페이지의 이탈률이 높았다.",
                }
            ],
        }
    )

    assert updated["commit_result"]["applied"][0]["path"] == "교내 커머스 리뉴얼 > 문제 해결"
