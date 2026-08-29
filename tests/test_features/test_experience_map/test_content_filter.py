"""반영 내용 필터링 노드 테스트 (에이전트 문서 5-3)"""

import pytest
from langchain_core.runnables import RunnableLambda

from features.experience_map.errors import LlmError
from features.experience_map.nodes import content_filter as filter_node
from features.experience_map.nodes.content_filter import filter_content, next_node
from features.experience_map.prompts.content_filter import build_gap_section
from features.experience_map.schemas import ContentFilterOutput
from features.experience_map.state import start_turn

SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"

MESSAGE = (
    "결제 모듈 타임아웃으로 주문 실패율이 12%까지 올랐다. "
    "APM 으로 병목을 찾아 쿼리 캐싱을 넣었다. "
    "이 내용을 정리해줘."
)


def make_state(**overrides):
    state = start_turn(
        {"user_id": "1", "session_id": SESSION_ID},
        request_id=REQUEST_ID,
        request_hash="a" * 64,
        user_message=overrides.pop("user_message", MESSAGE),
    )
    state.update(overrides)
    return state


def item(item_id: str, text: str, source: str = "message") -> dict:
    return {"item_id": item_id, "text": text, "source": source}


@pytest.fixture
def fake_llm(monkeypatch):
    """LLM 대역. 렌더된 프롬프트를 수집한다."""

    def _set(result: ContentFilterOutput | Exception) -> list[str]:
        prompts: list[str] = []

        async def _handle(prompt_value):
            prompts.append(prompt_value.to_string())
            if isinstance(result, Exception):
                raise result
            return result

        class _FakeLlm:
            def with_structured_output(self, schema):
                return RunnableLambda(_handle)

        monkeypatch.setattr(filter_node, "get_experience_map_llm", lambda **kw: _FakeLlm())
        return prompts

    return _set


ACTIVE_GAP = {
    "gap_id": REQUEST_ID,
    "gap_type": "extend_block",
    "anchor_block_id": "3055",
    "message": "그 해결 방법을 고른 기준이 무엇이었나요?",
    "created_request_id": REQUEST_ID,
}


# ===== 분류 =====


@pytest.mark.asyncio
async def test_classifies_three_buckets(fake_llm):
    fake_llm(
        ContentFilterOutput(
            gap_answer_items=[],
            new_items=[
                item("it_1", "결제 모듈 타임아웃으로 주문 실패율이 12%까지 올랐다."),
                item("it_2", "APM 으로 병목을 찾아 쿼리 캐싱을 넣었다."),
            ],
            excluded_reasons=["요구사항 텍스트"],
        )
    )

    result = await filter_content(make_state())

    assert len(result["new_items"]) == 2
    assert result["gap_answer_items"] == []
    assert result["excluded_reasons"] == ["요구사항 텍스트"]
    assert result["current_node"] == "content_filter"


@pytest.mark.asyncio
async def test_gap_answer_is_kept_when_gap_active(fake_llm):
    fake_llm(
        ContentFilterOutput(
            gap_answer_items=[item("it_1", "APM 으로 병목을 찾아 쿼리 캐싱을 넣었다.")],
            new_items=[],
        )
    )

    result = await filter_content(make_state(active_gap=ACTIVE_GAP))

    assert len(result["gap_answer_items"]) == 1
    assert result["new_items"] == []


@pytest.mark.asyncio
async def test_gap_answer_moves_to_new_when_no_active_gap(fake_llm):
    """활성 gap 이 없으면 gap 답변으로 분류되지 않는다.

    사용자 입력인 것은 맞으므로 버리지 않고 새 내용으로 옮긴다.
    """
    fake_llm(
        ContentFilterOutput(
            gap_answer_items=[item("it_1", "APM 으로 병목을 찾아 쿼리 캐싱을 넣었다.")],
            new_items=[],
        )
    )

    result = await filter_content(make_state())

    assert result["gap_answer_items"] == []
    assert len(result["new_items"]) == 1


@pytest.mark.asyncio
async def test_empty_gap_message_counts_as_no_gap(fake_llm):
    fake_llm(
        ContentFilterOutput(
            gap_answer_items=[item("it_1", "APM 으로 병목을 찾아 쿼리 캐싱을 넣었다.")],
            new_items=[],
        )
    )

    result = await filter_content(make_state(active_gap={**ACTIVE_GAP, "message": "  "}))

    assert result["gap_answer_items"] == []
    assert len(result["new_items"]) == 1


# ===== 원문 역추적 (hallucination 방어) =====


@pytest.mark.asyncio
async def test_untraceable_item_is_dropped(fake_llm):
    """입력에 없는 문장은 버린다. 지어낸 수치가 통과하면 이후 노드가 사실로 다룬다."""
    fake_llm(
        ContentFilterOutput(
            new_items=[
                item("it_1", "결제 모듈 타임아웃으로 주문 실패율이 12%까지 올랐다."),
                item("it_2", "전환율을 45% 개선했고 팀을 이끌었다."),  # 입력에 없음
            ]
        )
    )

    result = await filter_content(make_state())

    texts = [i["text"] for i in result["new_items"]]
    assert len(texts) == 1
    assert "45%" not in texts[0]


@pytest.mark.asyncio
async def test_whitespace_differences_are_tolerated(fake_llm):
    """줄바꿈·들여쓰기 차이로 멀쩡한 조각을 버리면 안 된다."""
    fake_llm(
        ContentFilterOutput(
            new_items=[item("it_1", "결제 모듈  타임아웃으로\n주문 실패율이 12%까지 올랐다.")]
        )
    )

    result = await filter_content(make_state())

    assert len(result["new_items"]) == 1


@pytest.mark.asyncio
async def test_file_text_is_traceable(fake_llm):
    """파일에서 추출한 텍스트도 원문으로 인정한다."""
    fake_llm(
        ContentFilterOutput(
            new_items=[item("it_1", "DAU 150% 증가", source="file")],
        )
    )

    result = await filter_content(
        make_state(user_message=None, extracted_text="성과: DAU 150% 증가")
    )

    assert len(result["new_items"]) == 1
    assert result["new_items"][0]["source"] == "file"


@pytest.mark.asyncio
async def test_long_file_item_is_split_without_rewriting(fake_llm, monkeypatch):
    """PDF 한 페이지가 한 item으로 와도 구조화에는 작은 원문 조각으로 넘긴다."""
    monkeypatch.setattr(filter_node, "MAX_SOURCE_ITEM_CHARS", 30)
    text = "첫 번째 문장에서 프로젝트 배경을 설명했다. 두 번째 문장에서 맡은 역할을 설명했다."
    fake_llm(ContentFilterOutput(new_items=[item("it_1", text, source="file")]))

    result = await filter_content(make_state(user_message=None, extracted_text=text))

    chunks = result["new_items"]
    assert len(chunks) > 1
    assert all(len(chunk["text"]) <= 30 for chunk in chunks)
    assert all(chunk["source"] == "file" for chunk in chunks)
    assert len({chunk["item_id"] for chunk in chunks}) == len(chunks)
    assert " ".join(chunk["text"] for chunk in chunks) == text


@pytest.mark.asyncio
async def test_long_unbroken_file_item_uses_hard_limit(fake_llm, monkeypatch):
    """OCR 결과에 경계가 없어도 구조화 한도를 넘기지 않는다."""
    monkeypatch.setattr(filter_node, "MAX_SOURCE_ITEM_CHARS", 10)
    text = "가나다라마바사아자차카타파하가나다라마바사"
    fake_llm(ContentFilterOutput(new_items=[item("it_1", text, source="file")]))

    result = await filter_content(make_state(user_message=None, extracted_text=text))

    chunks = result["new_items"]
    assert all(len(chunk["text"]) <= 10 for chunk in chunks)
    assert "".join(chunk["text"] for chunk in chunks) == text


@pytest.mark.asyncio
async def test_duplicate_item_is_dropped(fake_llm):
    """같은 문장이 두 목록에 들어오면 하나만 남긴다."""
    text = "APM 으로 병목을 찾아 쿼리 캐싱을 넣었다."
    fake_llm(
        ContentFilterOutput(
            gap_answer_items=[item("it_1", text)],
            new_items=[item("it_2", text)],
        )
    )

    result = await filter_content(make_state(active_gap=ACTIVE_GAP))

    assert len(result["gap_answer_items"]) == 1
    assert result["new_items"] == []


@pytest.mark.asyncio
async def test_empty_text_is_dropped(fake_llm):
    fake_llm(ContentFilterOutput(new_items=[item("it_1", "   ")]))

    result = await filter_content(make_state())

    assert result["new_items"] == []


# ===== 반영할 것이 없을 때 =====


@pytest.mark.asyncio
async def test_all_excluded_sets_fallback_reason(fake_llm):
    fake_llm(ContentFilterOutput(excluded_reasons=["일반 지식 나열", "무관한 내용"]))

    result = await filter_content(make_state())

    assert result["fallback_reason"] == "nothing_to_apply"
    assert next_node(result) == "fallback"


# ===== 프롬프트 구성 =====


@pytest.mark.asyncio
async def test_prompt_states_when_gap_is_absent(fake_llm):
    """gap 이 없으면 없다고 명시한다. 없는데 있는 척하면 아무 문장이나 분류된다."""
    prompts = fake_llm(ContentFilterOutput(new_items=[]))

    await filter_content(make_state())

    assert "활성 gap이 없습니다" in prompts[0]


@pytest.mark.asyncio
async def test_prompt_carries_gap_and_inputs(fake_llm):
    prompts = fake_llm(ContentFilterOutput(new_items=[]))

    await filter_content(make_state(active_gap=ACTIVE_GAP, extracted_text="첨부 내용입니다"))

    assert "그 해결 방법을 고른 기준" in prompts[0]
    assert "결제 모듈 타임아웃" in prompts[0]
    assert "첨부 내용입니다" in prompts[0]


@pytest.mark.asyncio
async def test_prompt_carries_existing_activity_when_comparison_is_requested(fake_llm):
    """기존 내용 제외 요청에는 현재 활동 블록을 비교 근거로 제공한다."""
    prompts = fake_llm(ContentFilterOutput(new_items=[]))

    await filter_content(
        make_state(
            user_message="이미 정리한 내용은 제외하고 새 내용만 반영해줘",
            activity_tree_text="[exp_1] 커머스 개선\n  [b_1] 결제 오류 분석",
        )
    )

    assert "현재 활동에 이미 저장된 경험정리 블록" in prompts[0]
    assert "결제 오류 분석" in prompts[0]


@pytest.mark.asyncio
async def test_regular_input_does_not_include_existing_map(fake_llm):
    """일반 입력에는 큰 기존 맵을 불필요하게 싣지 않는다."""
    prompts = fake_llm(ContentFilterOutput(new_items=[]))

    await filter_content(
        make_state(activity_tree_text="[exp_1] 커머스 개선\n  [b_1] 기존 비밀 내용")
    )

    assert "기존 비밀 내용" not in prompts[0]


def test_gap_section_without_gap():
    assert "활성 gap이 없습니다" in build_gap_section(None)
    assert "활성 gap이 없습니다" in build_gap_section({})


# ===== 실패 =====


@pytest.mark.asyncio
async def test_llm_failure_raises(fake_llm):
    fake_llm(RuntimeError("upstream 500"))

    with pytest.raises(LlmError) as exc_info:
        await filter_content(make_state())

    assert exc_info.value.failed_node == "content_filter"


@pytest.mark.asyncio
async def test_input_text_not_in_exception(fake_llm):
    fake_llm(RuntimeError("boom"))
    secret = "주민등록번호 900101-1234567"

    with pytest.raises(LlmError) as exc_info:
        await filter_content(make_state(user_message=secret))

    assert secret not in str(exc_info.value)


# ===== 후속 노드 분기 5가지 (5-3) =====


def test_next_node_new_items_only():
    assert next_node({"new_items": [item("it_1", "x")], "gap_answer_items": []}) == "structure"


def test_next_node_gap_only_extend_block():
    """`extend_block` 은 기존 블록에 합치므로 정제로 간다."""
    state = {
        "gap_answer_items": [item("it_1", "x")],
        "new_items": [],
        "active_gap": {"gap_type": "extend_block"},
    }
    assert next_node(state) == "refine"


def test_next_node_gap_only_new_child_block():
    """`new_child_block` 은 하위 블록을 만들어야 하므로 구조화로 간다."""
    state = {
        "gap_answer_items": [item("it_1", "x")],
        "new_items": [],
        "active_gap": {"gap_type": "new_child_block"},
    }
    assert next_node(state) == "structure"


def test_next_node_structure_gap_plus_new():
    """구조화가 필요한 gap 답변 + 새 내용 → 둘 다 구조화."""
    state = {
        "gap_answer_items": [item("it_1", "x")],
        "new_items": [item("it_2", "y")],
        "active_gap": {"gap_type": "new_child_block"},
    }
    assert next_node(state) == "structure"


def test_next_node_refine_gap_plus_new():
    """정제가 필요한 gap 답변 + 새 내용 → 새 내용을 먼저 구조화한다.

    구조화 결과와 gap 답변은 그 뒤 정제 노드가 함께 다룬다.
    """
    state = {
        "gap_answer_items": [item("it_1", "x")],
        "new_items": [item("it_2", "y")],
        "active_gap": {"gap_type": "extend_block"},
    }
    assert next_node(state) == "structure"


def test_next_node_nothing_to_apply():
    assert next_node({"gap_answer_items": [], "new_items": []}) == "fallback"
