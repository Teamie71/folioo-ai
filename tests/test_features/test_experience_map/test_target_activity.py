"""대상 활동 선택 노드 테스트 (에이전트 문서 5-4, 6-2)."""

import pytest
from langchain_core.runnables import RunnableLambda

from features.experience_map.errors import LlmError
from features.experience_map.nodes import target_activity as target_node
from features.experience_map.nodes.target_activity import next_node, select_target_activity
from features.experience_map.schemas import TargetActivityOutput
from features.experience_map.state import start_turn

SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"

OUTLINE = [
    {
        "alias": "group_1",
        "level": 1,
        "title": "프로젝트",
        "children": [
            {"alias": "exp_1", "level": 2, "title": "교내 커머스 리뉴얼"},
            {"alias": "exp_2", "level": 2, "title": "추천 시스템 개선"},
        ],
    }
]


def make_state(**overrides):
    """기본 맵과 alias 소유권을 갖춘 테스트 state."""
    state = start_turn(
        {"user_id": "1", "session_id": SESSION_ID},
        request_id=REQUEST_ID,
        request_hash="a" * 64,
        user_message=overrides.pop("user_message", "커머스 결제 실패율을 낮췄어요"),
    )
    state.update(
        outline=OUTLINE,
        alias_to_block_id={"exp_1": "101", "exp_2": "102"},
        block_id_to_experience_alias={"101": "exp_1", "102": "exp_2", "305": "exp_1"},
    )
    state.update(overrides)
    return state


@pytest.fixture
def fake_llm(monkeypatch):
    """대역 LLM과 렌더된 프롬프트 수집기를 제공한다."""

    def _set(result: TargetActivityOutput | Exception) -> list[str]:
        prompts: list[str] = []

        def _handle(prompt_value) -> TargetActivityOutput:
            prompts.append(prompt_value.to_string())
            if isinstance(result, Exception):
                raise result
            return result

        class _FakeLlm:
            def with_structured_output(self, schema):
                return RunnableLambda(_handle)

        monkeypatch.setattr(target_node, "get_experience_map_llm", lambda **kw: _FakeLlm())
        return prompts

    return _set


@pytest.mark.asyncio
async def test_valid_context_has_priority_and_skips_llm(fake_llm):
    """화면에서 선택한 유효 활동은 메시지보다 우선한다."""
    prompts = fake_llm(TargetActivityOutput(activity_alias="exp_2", reason="틀린 선택"))

    result = await select_target_activity(
        make_state(context_experience_id="101", user_message="추천 시스템을 개선했어요")
    )

    assert result["target_experience_alias"] == "exp_1"
    assert prompts == []


@pytest.mark.asyncio
async def test_foreign_or_unknown_context_is_not_accepted(fake_llm):
    """다른 활동·다른 사용자의 ID는 context로 승인되지 않는다."""
    fake_llm(TargetActivityOutput(activity_alias="exp_2", reason="메시지가 명확함"))

    result = await select_target_activity(make_state(context_experience_id="999"))

    assert result["target_experience_alias"] == "exp_2"


@pytest.mark.asyncio
async def test_gap_answer_uses_owner_of_anchor_and_skips_llm(fake_llm):
    """짧은 gap 답변도 anchor가 속한 활동으로 정확히 돌아간다."""
    prompts = fake_llm(TargetActivityOutput(activity_alias="exp_2", reason="틀린 선택"))
    result = await select_target_activity(
        make_state(
            user_message="재시도 로직을 추가했어요",
            active_gap={"anchor_block_id": "305", "message": "어떻게 해결했나요?"},
            gap_answer_items=[{"item_id": "g_1", "text": "재시도 로직", "source": "message"}],
        )
    )

    assert result["target_experience_alias"] == "exp_1"
    assert prompts == []


@pytest.mark.asyncio
async def test_unverified_gap_anchor_falls_back_without_llm(fake_llm):
    """gap anchor의 소유 활동을 모르면 임의 배정하지 않는다."""
    prompts = fake_llm(TargetActivityOutput(activity_alias="exp_1", reason="추측"))
    result = await select_target_activity(
        make_state(
            active_gap={"anchor_block_id": "999", "message": "어떻게 해결했나요?"},
            gap_answer_items=[{"item_id": "g_1", "text": "재시도 로직", "source": "message"}],
        )
    )

    assert result["fallback_reason"] == "ambiguous_target"
    assert result["target_experience_alias"] is None
    assert prompts == []


@pytest.mark.asyncio
async def test_message_selection_receives_only_outline_aliases(fake_llm):
    """LLM에는 상세 block ID를 주지 않고 활동 outline만 준다."""
    prompts = fake_llm(TargetActivityOutput(activity_alias="exp_2", reason="추천 시스템 언급"))

    result = await select_target_activity(
        make_state(user_message="추천 시스템 정확도를 개선했어요")
    )

    assert result["target_experience_alias"] == "exp_2"
    assert "[exp_1] 교내 커머스 리뉴얼" in prompts[0]
    assert "[exp_2] 추천 시스템 개선" in prompts[0]
    assert "305" not in prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", [None, "exp_999", "b_305"])
async def test_ambiguous_or_unapproved_llm_result_falls_back(fake_llm, alias):
    """불명확하거나 허용 목록 밖인 결과는 커밋 경로로 보내지 않는다."""
    fake_llm(TargetActivityOutput(activity_alias=alias, reason="판단 불가"))

    result = await select_target_activity(make_state())

    assert result["fallback_reason"] == "ambiguous_target"
    assert next_node(result) == "fallback"


@pytest.mark.asyncio
async def test_llm_failure_raises_retryable_error(fake_llm):
    fake_llm(RuntimeError("upstream 500"))

    with pytest.raises(LlmError) as exc_info:
        await select_target_activity(make_state())

    assert exc_info.value.failed_node == "target_activity"
    assert exc_info.value.retryable is True
