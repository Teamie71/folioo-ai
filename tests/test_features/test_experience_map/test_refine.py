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
async def test_validate_refine_only_retry_reuses_unflagged_items(fake_llm):
    """validate가 refine만 지목한 재시도는 지목된 item만 다시 LLM에 보낸다.

    지목되지 않은 item(it_1)은 지난 회차 정제 결과를 그대로 재사용해야
    한다 — structure는 이번 루프에서 실행되지 않았으므로 structured_items가
    바뀌지 않았다.
    """
    prompts = fake_llm(
        RefinementOutput(
            items=[RefinedItem(item_id="it_2", refined_text="캐싱 도입으로 페이지 로딩 속도 개선")]
        )
    )

    state = make_state(
        structured_items=[
            {
                "item_id": "it_1",
                "action": "add",
                "parent_ref": "b_1",
                "text": "APM으로 병목을 확인해 결제 오류를 해결했다.",
            },
            {
                "item_id": "it_2",
                "action": "add",
                "parent_ref": "b_1",
                "text": "캐싱을 도입해 페이지 로딩 속도를 개선했다.",
            },
        ],
        refined_items=[
            {"item_id": "it_1", "refined_text": "APM으로 병목 확인 → 결제 오류 해결"},
            {
                "item_id": "it_2",
                "refined_text": "캐싱을 도입해 페이지 로딩 속도를 개선했다 (지난 회차, 반려됨)",
            },
        ],
        validation_errors=[
            {
                "item_id": "it_2",
                "code": "content_too_long",
                "message": "내용이 최대 글자 수를 넘었습니다.",
                "repair_target": "refine",
            }
        ],
        repair_count=1,
    )

    result = await refine_text(state)

    # it_2만 LLM에 보내고, it_1은 프롬프트에 아예 없어야 한다.
    assert "[it_2]" in prompts[0]
    assert "[it_1]" not in prompts[0]
    assert result["refined_items"] == [
        # it_1은 재시도 대상이 아니므로 지난 회차 결과를 그대로 재사용한다.
        {"item_id": "it_1", "refined_text": "APM으로 병목 확인 → 결제 오류 해결"},
        # it_2만 새로 정제된다.
        {"item_id": "it_2", "refined_text": "캐싱 도입으로 페이지 로딩 속도 개선"},
    ]


@pytest.mark.asyncio
async def test_validate_structure_and_refine_retry_reprocesses_everything(fake_llm):
    """validate가 structure까지 지목한 회귀는 refine도 전체를 다시 정제한다.

    structured_items 자체가 바뀌었을 수 있으므로, 지난 회차 정제 결과를
    재사용하면 새로 생기거나 없어진 블록을 놓칠 수 있다.
    """
    prompts = fake_llm(
        RefinementOutput(
            items=[
                RefinedItem(item_id="it_1", refined_text="APM으로 병목 확인 → 결제 오류 해결"),
                RefinedItem(item_id="it_2", refined_text="새로 만들어진 블록"),
            ]
        )
    )

    state = make_state(
        structured_items=[
            {
                "item_id": "it_1",
                "action": "add",
                "parent_ref": "b_1",
                "text": "APM으로 병목을 확인해 결제 오류를 해결했다.",
            },
            {
                "item_id": "it_2",
                "action": "add",
                "parent_ref": "b_1",
                "text": "새로 만들어진 블록이다.",
            },
        ],
        refined_items=[
            {"item_id": "it_1", "refined_text": "지난 회차 it_1 (다른 블록 집합 기준)"},
        ],
        validation_errors=[
            {
                "item_id": "__operations__",
                "code": "item_set_mismatch",
                "message": "정제 전후 item 집합이 다릅니다.",
                "repair_target": "structure",
            }
        ],
        repair_count=1,
    )

    result = await refine_text(state)

    assert "[it_1]" in prompts[0]
    assert "[it_2]" in prompts[0]
    assert result["refined_items"] == [
        {"item_id": "it_1", "refined_text": "APM으로 병목 확인 → 결제 오류 해결"},
        {"item_id": "it_2", "refined_text": "새로 만들어진 블록"},
    ]


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
async def test_source_number_deleted_by_refine_falls_back_to_source(fake_llm):
    """정제가 원문 수치를 통째로 지워도 원문으로 되돌린다.

    실제로 재현된 경우다. "알림 시간을 8분에서 3초로 단축하고 2,400건을
    처리했다"를 "알림 처리 성능 개선"으로 뭉뚱그리면 수치가 전부 사라지는데,
    기존 검사는 "정제 결과에 없던 수치가 새로 생겼는지"만 봐서
    (refined ⊆ source) 수치를 지우는 방향은 잡지 못했다.
    """
    fake_llm(
        RefinementOutput(
            items=[
                RefinedItem(item_id="it_1", refined_text="알림 처리 성능 개선"),
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
                "text": "알림 시간을 8분에서 3초로 단축하고 2,400건을 처리했다.",
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

    assert result["refined_items"][0]["refined_text"] == (
        "알림 시간을 8분에서 3초로 단축하고 2,400건을 처리했다."
    )


@pytest.mark.asyncio
async def test_fabricated_korean_method_falls_back_to_source(fake_llm):
    """정제가 원문에 없는 한국어 방법·수단을 지어내도 원문으로 되돌린다.

    실제로 재현된 경우다. "로그 분석을 통해 결제 오류를 해결했다"를
    "고객 인터뷰를 통해 결제 오류 해결"로 바꾸면 방법 자체가 지어낸
    내용인데, 숫자·영문 토큰 검사만으로는 못 잡았다 — 둘 다 새 숫자나
    영문 고유명사를 담고 있지 않기 때문이다.
    """
    fake_llm(
        RefinementOutput(
            items=[
                RefinedItem(item_id="it_1", refined_text="고객 인터뷰를 통해 결제 오류 해결"),
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
                "text": "로그 분석을 통해 결제 오류를 해결했다.",
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

    assert result["refined_items"][0]["refined_text"] == "로그 분석을 통해 결제 오류를 해결했다."


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
