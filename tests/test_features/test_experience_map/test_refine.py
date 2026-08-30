"""문장 정제 노드 테스트 (에이전트 문서 2-1, 5-6)."""

import pytest
from langchain_core.runnables import RunnableLambda

from features.experience_map.errors import LlmError
from features.experience_map.nodes import refine as refine_node
from features.experience_map.nodes.refine import next_node, refine_text
from features.experience_map.schemas import RefinedItem, RefinementOutput
from features.experience_map.state import start_turn

SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"


def make_state(**overrides):
    """구조화 결과가 있는 기본 state."""
    state = start_turn(
        {"user_id": "1", "session_id": SESSION_ID},
        request_id=REQUEST_ID,
        request_hash="a" * 64,
        user_message="결제 오류를 해결했다",
    )
    state.update(
        activity_tree_text="[exp_1] 교내 커머스 리뉴얼\n  [b_1] 문제해결",
        alias_to_block_id={"exp_1": "101", "b_1": "305"},
        structured_items=[
            {
                "item_id": "it_1",
                "action": "add",
                "parent_ref": "b_1",
                "text": "APM으로 병목을 확인해 결제 오류를 해결했다.",
            },
            {
                "item_id": "empty_1",
                "action": "add",
                "parent_ref": "b_1",
                "slot_id": "TASK.BASIC.RESULT",
                "text": None,
            },
        ],
    )
    state.update(overrides)
    return state


@pytest.fixture
def fake_llm(monkeypatch):
    """LLM 대역과 렌더된 활동 단위 프롬프트를 제공한다."""

    def _set(result: RefinementOutput | Exception) -> list[str]:
        prompts: list[str] = []

        async def _handle(prompt_value) -> RefinementOutput:
            prompts.append(prompt_value.to_string())
            if isinstance(result, Exception):
                raise result
            return result

        class _FakeLlm:
            def with_structured_output(self, schema):
                assert schema is RefinementOutput
                return RunnableLambda(_handle)

        monkeypatch.setattr(refine_node, "get_experience_map_llm", lambda **kw: _FakeLlm())
        return prompts

    return _set


@pytest.mark.asyncio
async def test_refines_content_only_and_restores_empty_slot(fake_llm):
    """빈 슬롯은 LLM에 보내지 않고 코드가 null 결과를 복원한다."""
    prompts = fake_llm(
        RefinementOutput(
            items=[
                RefinedItem(item_id="it_1", refined_text="APM으로 병목 확인 → 결제 오류 해결"),
                RefinedItem(item_id="empty_1", refined_text=None),
            ]
        )
    )

    result = await refine_text(make_state())

    assert result["refined_items"] == [
        {"item_id": "it_1", "refined_text": "APM으로 병목 확인 → 결제 오류 해결"},
        {"item_id": "empty_1", "refined_text": None},
    ]
    assert "[it_1]" in prompts[0]
    assert "[empty_1]" not in prompts[0]
    assert "[exp_1] 교내 커머스 리뉴얼" in prompts[0]


@pytest.mark.asyncio
async def test_extend_gap_combines_existing_content_and_answer(fake_llm):
    """extend gap은 기존 anchor 문장을 잃지 않고 update metadata를 남긴다."""
    fake_llm(
        RefinementOutput(
            items=[
                RefinedItem(item_id="it_1", refined_text="APM으로 병목 확인 → 결제 오류 해결"),
                RefinedItem(item_id="empty_1", refined_text=None),
                RefinedItem(
                    item_id="gap_update:305",
                    refined_text="기존 문장 / 재시도 로직을 추가해 재발 방지",
                ),
            ]
        )
    )
    state = make_state(
        active_gap={"gap_type": "extend_block", "anchor_block_id": "305"},
        gap_answer_items=[
            {"item_id": "gap_1", "text": "재시도 로직을 추가해 재발을 막았다.", "source": "message"}
        ],
        block_id_to_content={"305": "기존 문장"},
    )

    result = await refine_text(state)

    assert result["gap_update_item"] == {
        "item_id": "gap_update:305",
        "action": "update",
        "target_ref": "b_1",
        "text": "기존 문장\n재시도 로직을 추가해 재발을 막았다.",
    }
    assert result["refined_items"][-1]["item_id"] == "gap_update:305"


@pytest.mark.asyncio
async def test_missing_or_extra_item_ids_fall_back_to_source(fake_llm):
    fake_llm(RefinementOutput(items=[RefinedItem(item_id="invented", refined_text="임의 결과")]))

    result = await refine_text(make_state())

    assert result["refined_items"][0] == {
        "item_id": "it_1",
        "refined_text": "APM으로 병목을 확인해 결제 오류를 해결했다.",
    }


@pytest.mark.asyncio
async def test_number_not_grounded_in_source_falls_back_to_source(fake_llm):
    fake_llm(
        RefinementOutput(
            items=[
                RefinedItem(item_id="it_1", refined_text="전환율 50% 개선"),
                RefinedItem(item_id="empty_1", refined_text=None),
            ]
        )
    )

    result = await refine_text(make_state())

    assert result["refined_items"][0]["refined_text"] == (
        "APM으로 병목을 확인해 결제 오류를 해결했다."
    )


@pytest.mark.asyncio
async def test_new_proper_noun_not_grounded_in_source_falls_back_to_source(fake_llm):
    fake_llm(
        RefinementOutput(
            items=[
                RefinedItem(item_id="it_1", refined_text="APM으로 Redis 병목 해결"),
                RefinedItem(item_id="empty_1", refined_text=None),
            ]
        )
    )

    result = await refine_text(make_state())

    assert result["refined_items"][0]["refined_text"] == (
        "APM으로 병목을 확인해 결제 오류를 해결했다."
    )


@pytest.mark.asyncio
async def test_allows_case_normalization_of_english_token(fake_llm):
    """영문 약어의 대소문자 정규화는 새로운 고유명사가 아니다."""
    fake_llm(
        RefinementOutput(
            items=[
                RefinedItem(item_id="it_1", refined_text="신청 페이지 UI를 친숙하게 개선"),
                RefinedItem(item_id="empty_1", refined_text=None),
            ]
        )
    )
    state = make_state(
        structured_items=[
            {
                "item_id": "it_1",
                "action": "add",
                "parent_ref": "b_1",
                "text": "신청 페이지의 ui를 더 친숙하게 바꿨다.",
            },
            {
                "item_id": "empty_1",
                "action": "add",
                "parent_ref": "b_1",
                "slot_id": "TASK.BASIC.RESULT",
                "text": None,
            },
        ]
    )

    result = await refine_text(state)

    assert result["refined_items"][0]["refined_text"] == "신청 페이지 UI를 친숙하게 개선"


@pytest.mark.asyncio
async def test_extend_gap_without_existing_content_is_rejected_before_llm(fake_llm):
    """anchor 원문을 못 읽으면 답변만으로 update하지 않는다."""
    prompts = fake_llm(RefinementOutput(items=[]))
    state = make_state(
        active_gap={"gap_type": "extend_block", "anchor_block_id": "305"},
        gap_answer_items=[{"item_id": "gap_1", "text": "재시도 로직", "source": "message"}],
    )

    with pytest.raises(LlmError):
        await refine_text(state)

    assert prompts == []


@pytest.mark.asyncio
async def test_llm_failure_is_retryable(fake_llm):
    fake_llm(RuntimeError("upstream 500"))

    with pytest.raises(LlmError) as exc_info:
        await refine_text(make_state())

    assert exc_info.value.failed_node == "refine"
    assert exc_info.value.retryable is True


def test_next_node_uses_validate_only_with_result():
    assert next_node({"refined_items": [{"item_id": "it_1"}]}) == "validate"
    assert next_node({"refined_items": []}) == "fallback"
