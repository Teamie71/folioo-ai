"""이번 턴 커밋 기준 gap 분석과 제안 변환 테스트."""

import pytest
from langchain_core.runnables import RunnableLambda

from features.experience_map.errors import LlmError
from features.experience_map.nodes import gap_analysis as gap_node
from features.experience_map.nodes.gap_analysis import NO_GAP_MESSAGE, analyze_gap
from features.experience_map.nodes.suggestion_response import build_suggestion
from features.experience_map.schemas import GapCandidate, GapOutput
from features.experience_map.state import start_turn

REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"


def make_state(**overrides):
    """커밋이 확정된 한 활동의 기본 state."""
    state = start_turn(
        {"user_id": "1", "session_id": "d9428888-122b-11e1-b85c-61cd3cbb3210"},
        request_id=REQUEST_ID,
        request_hash="a" * 64,
    )
    state.update(
        alias_to_block_id={"exp_1": "101", "b_1": "305", "b_2": "306"},
        activity_tree_text="[exp_1] 교내 커머스 리뉴얼\n  [b_1] 담당업무\n    [b_2] 결제 개선",
        commit_items=[
            {
                "item_id": "it_1",
                "action": "add",
                "parent_ref": "b_1",
                "text": "결제 오류 원인을 분석해 개선안을 적용했다.",
            }
        ],
    )
    state.update(overrides)
    return state


@pytest.fixture
def fake_llm(monkeypatch):
    """structured output과 프롬프트를 제어하는 LLM 대역."""

    def _set(result: GapOutput | Exception) -> list[str]:
        prompts: list[str] = []

        async def _handle(prompt_value) -> GapOutput:
            prompts.append(prompt_value.to_string())
            if isinstance(result, Exception):
                raise result
            return result

        class _FakeLlm:
            def with_structured_output(self, schema):
                assert schema is GapOutput
                return RunnableLambda(_handle)

        monkeypatch.setattr(gap_node, "get_experience_map_llm", lambda **kwargs: _FakeLlm())
        return prompts

    return _set


@pytest.mark.asyncio
async def test_analyzes_only_committed_items_and_direct_anchor_candidates(fake_llm):
    """현재 맵 전체 대신 방금 커밋될 text와 연결된 별칭만 LLM에 준다."""
    prompts = fake_llm(
        GapOutput(
            gap=GapCandidate(
                gap_type="extend_block", anchor_ref="b_1", reason="판단 기준이 부족함"
            ),
            message="개선안을 선택한 판단 기준은 무엇이었나요?",
        )
    )

    result = await analyze_gap(make_state())

    assert result["gap_candidate"] == {
        "gap_type": "extend_block",
        "anchor_ref": "b_1",
        "reason": "판단 기준이 부족함",
    }
    assert result["gap_message"] == "개선안을 선택한 판단 기준은 무엇이었나요?"
    assert "결제 오류 원인을 분석해 개선안을 적용했다." in prompts[0]
    assert "[b_1]" in prompts[0]
    assert "[b_2]" not in prompts[0]
    assert "교내 커머스 리뉴얼" not in prompts[0]


@pytest.mark.asyncio
async def test_no_gap_uses_fixed_message_and_does_not_keep_previous_gap(fake_llm):
    """정상적인 gap 없음은 고정 문구와 active_gap 초기화로 표현한다."""
    fake_llm(GapOutput(gap=None, message="임의 문구"))

    analyzed = await analyze_gap(make_state(active_gap={"gap_type": "extend_block"}))
    result = build_suggestion(analyzed)

    assert result["gap_message"] == NO_GAP_MESSAGE
    assert result["active_gap"] is None
    assert result["suggestion"] == {"gap": None, "message": NO_GAP_MESSAGE}


@pytest.mark.asyncio
async def test_invalid_indirect_anchor_is_gap_analysis_failure(fake_llm):
    """이번 커밋과 직접 연결되지 않은 블록을 질문 기준으로 쓰지 못한다."""
    fake_llm(
        GapOutput(
            gap=GapCandidate(gap_type="extend_block", anchor_ref="b_2"),
            message="무엇을 더 했나요?",
        )
    )

    with pytest.raises(LlmError) as exc_info:
        await analyze_gap(make_state())

    assert exc_info.value.failed_node == "gap_analysis"


@pytest.mark.asyncio
async def test_gap_analysis_failure_is_distinct_from_no_gap(fake_llm):
    """LLM 실패는 no-gap suggestion을 만들지 않고 coordinator로 전파한다."""
    fake_llm(RuntimeError("upstream failure"))

    with pytest.raises(LlmError):
        await analyze_gap(make_state())


@pytest.mark.asyncio
async def test_no_existing_anchor_skips_llm_and_returns_no_gap(fake_llm):
    """새 블록끼리만 연결된 커밋은 근거 없는 질문을 만들지 않는다."""
    prompts = fake_llm(GapOutput(gap=None, message="unused"))
    state = make_state(
        commit_items=[
            {
                "item_id": "section_1",
                "action": "add",
                "parent_item_id": "new_activity",
                "text": "새 내용",
            }
        ]
    )

    result = await analyze_gap(state)

    assert prompts == []
    assert result["gap_candidate"] is None
    assert result["gap_message"] == NO_GAP_MESSAGE


def test_suggestion_converts_alias_to_active_gap_and_path():
    """LLM 별칭은 state에서만 실제 ID로 바꾸고 화면에는 경로를 제공한다."""
    result = build_suggestion(
        make_state(
            gap_candidate={"gap_type": "new_child_block", "anchor_ref": "b_2"},
            gap_message="결제 개선 뒤 어떤 변화가 있었나요?",
        )
    )

    assert result["active_gap"]["anchor_block_id"] == "306"
    assert result["active_gap"]["gap_type"] == "new_child_block"
    assert result["active_gap"]["created_request_id"] == REQUEST_ID
    assert result["suggestion"]["gap"]["path"] == "교내 커머스 리뉴얼 > 담당업무 > 결제 개선"
