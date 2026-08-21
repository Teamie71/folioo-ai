"""블록 단위 구조화 노드 테스트 (에이전트 문서 3절, 5-5)."""

import pytest
from langchain_core.runnables import RunnableLambda

from features.experience_map.errors import LlmError
from features.experience_map.nodes import structure as structure_node
from features.experience_map.nodes.structure import _validate_output, next_node, structure_blocks
from features.experience_map.schemas import StructureLlmItem, StructureOutput
from features.experience_map.state import start_turn
from features.experience_map.templates import TemplateCatalog, TemplateCatalogClient

SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"


def catalog_payload() -> dict:
    """카테고리와 하위 템플릿을 가진 최소 카탈로그."""
    return {
        "version": "v1",
        "sections": [
            {
                "section_id": "DETAIL",
                "label": "상세정보",
                "slots": [
                    {
                        "slot_id": "DETAIL.MOTIVATION",
                        "level": 4,
                        "placeholder": "시작 계기",
                        "example": "문제를 해결하고 싶었습니다.",
                    }
                ],
                "templates": [],
            },
            {
                "section_id": "TASK",
                "label": "담당업무",
                "slots": [
                    {
                        "slot_id": "TASK.SUMMARY",
                        "level": 4,
                        "placeholder": "업무 요약",
                        "example": "사용자 조사",
                        "is_anchor": True,
                    }
                ],
                "templates": [
                    {
                        "template_id": "BASIC",
                        "label": "기본",
                        "slots": [
                            {
                                "slot_id": "TASK.BASIC.PURPOSE",
                                "level": 5,
                                "placeholder": "목적",
                                "example": "전환율 개선",
                            },
                            {
                                "slot_id": "TASK.BASIC.RESULT",
                                "level": 5,
                                "placeholder": "결과",
                                "example": "전환율 상승",
                            },
                        ],
                    }
                ],
            },
        ],
    }


def make_state(**overrides):
    """선택 활동과 원문 하나를 가진 state."""
    state = start_turn(
        {"user_id": "1", "session_id": SESSION_ID},
        request_id=REQUEST_ID,
        request_hash="a" * 64,
        user_message="결제 오류를 해결했다",
    )
    state.update(
        target_experience_alias="exp_1",
        alias_to_block_id={"exp_1": "101", "b_1": "305"},
        activity_tree_text="[exp_1] 교내 커머스 리뉴얼\n  [b_1] 담당업무",
        new_items=[{"item_id": "it_1", "text": "결제 오류를 해결했다", "source": "message"}],
    )
    state.update(overrides)
    return state


@pytest.fixture
def fake_dependencies(monkeypatch):
    """카탈로그와 LLM 대역을 주입하고 렌더된 프롬프트를 모은다."""

    def _set(result: StructureOutput | Exception) -> list[str]:
        prompts: list[str] = []

        async def _fetcher():
            return catalog_payload()

        client = TemplateCatalogClient(_fetcher)
        monkeypatch.setattr(structure_node, "get_template_catalog_client", lambda: client)

        def _handle(prompt_value) -> StructureOutput:
            prompts.append(prompt_value.to_string())
            if isinstance(result, Exception):
                raise result
            return result

        class _FakeLlm:
            def with_structured_output(self, schema):
                assert schema is StructureOutput
                return RunnableLambda(_handle)

        monkeypatch.setattr(structure_node, "get_experience_map_llm", lambda **kw: _FakeLlm())
        return prompts

    return _set


@pytest.mark.asyncio
async def test_new_category_expands_all_section_slots_and_preserves_source(fake_dependencies):
    """새 카테고리는 템플릿 슬롯을 빠짐없이 만들고 원문을 바꾸지 않는다."""
    prompts = fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="category_1", action="add", parent_ref="exp_1", section_kind="DETAIL"
                ),
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_item_id="category_1",
                    slot_id="DETAIL.MOTIVATION",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
            ]
        )
    )

    result = await structure_blocks(make_state())

    assert [item["item_id"] for item in result["structured_items"]] == ["category_1", "blk_1"]
    assert result["structured_items"][1]["text"] == "결제 오류를 해결했다"
    # source_item_ids 는 구조화 노드 내부 전용이라 공용 스키마로 나가지 않는다.
    assert "source_item_ids" not in result["structured_items"][1]
    assert "[exp_1] 교내 커머스 리뉴얼" in prompts[0]
    assert "[DETAIL.MOTIVATION]" in prompts[0]


@pytest.mark.asyncio
async def test_template_expands_empty_slots(fake_dependencies):
    """템플릿을 사용하면 정보 없는 level 5 슬롯도 text 없이 남긴다."""
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
                StructureLlmItem(
                    item_id="empty_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.RESULT",
                ),
            ]
        )
    )

    result = await structure_blocks(make_state())

    assert result["structured_items"][1]["text"] is None


@pytest.mark.asyncio
async def test_multiple_source_items_can_merge_into_one_slot(fake_dependencies):
    """content_filter 는 불릿 단위, 템플릿은 주제 단위라 여러 원문이 한 슬롯에 모일 수 있다.

    합친 text 는 원문을 이어붙인 것과 정확히 같아야 하며(공백 차이는 허용),
    출력 item_id 는 입력 item_id 를 재사용하지 않는다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="원인을 조사했다 해결책을 적용했다",
                    source_item_ids=["it_1", "it_2"],
                ),
                StructureLlmItem(
                    item_id="empty_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.RESULT",
                ),
            ]
        )
    )
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "원인을 조사했다", "source": "message"},
            {"item_id": "it_2", "text": "해결책을 적용했다", "source": "message"},
        ]
    )

    result = await structure_blocks(state)

    assert result["structured_items"][0]["text"] == "원인을 조사했다 해결책을 적용했다"


@pytest.mark.asyncio
async def test_merge_without_separator_is_accepted(fake_dependencies):
    """조각 사이에 공백이 하나도 없이 붙여 써도 병합으로 인정한다.

    실제로 재현된 경우다. 문장이 마침표로 끝나는데 모델이 다음 조각을 띄어쓰기
    없이 바로 이어 붙였다. 조각 사이 공백을 정확히 한 칸으로 강제하면 이런
    정상적인 병합까지 "text를 바꿨다" 며 거부하게 된다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="원인을 조사했다.해결책을 적용했다",  # 공백 없이 이어붙임
                    source_item_ids=["it_1", "it_2"],
                ),
                StructureLlmItem(
                    item_id="empty_1", action="add", parent_ref="b_1", slot_id="TASK.BASIC.RESULT"
                ),
            ]
        )
    )
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "원인을 조사했다.", "source": "message"},
            {"item_id": "it_2", "text": "해결책을 적용했다", "source": "message"},
        ]
    )

    result = await structure_blocks(state)

    assert result["structured_items"][0]["text"] == "원인을 조사했다.해결책을 적용했다"


@pytest.mark.asyncio
async def test_merged_text_must_match_concatenation_exactly(fake_dependencies):
    """합친 text 가 원문 이어붙인 것과 다르면(요약·새 문장) 거부한다."""
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="원인 조사 후 해결",  # 요약됨 — 원문과 다름
                    source_item_ids=["it_1", "it_2"],
                ),
                StructureLlmItem(
                    item_id="empty_1", action="add", parent_ref="b_1", slot_id="TASK.BASIC.RESULT"
                ),
            ]
        )
    )
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "원인을 조사했다", "source": "message"},
            {"item_id": "it_2", "text": "해결책을 적용했다", "source": "message"},
        ]
    )

    with pytest.raises(LlmError):
        await structure_blocks(state)


@pytest.mark.asyncio
async def test_source_item_used_twice_is_rejected(fake_dependencies):
    """같은 원문 item이 두 블록에 나눠 들어가면 안 된다."""
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
                StructureLlmItem(
                    item_id="blk_2",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.RESULT",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
            ]
        )
    )

    with pytest.raises(LlmError):
        await structure_blocks(make_state())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "items",
    [
        [],
        [
            StructureLlmItem(
                item_id="blk_1",
                action="add",
                parent_ref="exp_1",
                text="바뀐 원문",
                source_item_ids=["it_1"],
            )
        ],
    ],
)
async def test_missing_or_changed_source_is_rejected(fake_dependencies, items):
    """구조화가 원문을 누락하거나 고쳐 쓰면 다음 단계로 넘기지 않는다."""
    fake_dependencies(StructureOutput(items=items))

    with pytest.raises(LlmError) as exc_info:
        await structure_blocks(make_state())

    assert exc_info.value.failed_node == "structure"


@pytest.mark.asyncio
async def test_level5_slot_must_attach_to_anchor_not_container(fake_dependencies):
    """level 5 슬롯은 카테고리 컨테이너가 아니라 앵커(level 4) 블록 아래에 붙는다.

    실제로 재현된 경우다. 모델이 하위 템플릿의 level 5 슬롯들을 카테고리
    컨테이너에 바로 매달았다. 이 미배치는 "카테고리가 level 4 슬롯을 모두
    생성해야 한다" 는 기존 검사도 같이 걸리게 만드는데(level 5 슬롯이 그
    개수 계산에 섞여 들어가서), 그 일반적인 메시지 대신 **원인을 정확히
    짚는 메시지**가 먼저 뜨는지까지 확인한다 — 검증 순서를 바꿔 뒀다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="category_1", action="add", parent_ref="exp_1", section_kind="TASK"
                ),
                StructureLlmItem(
                    item_id="anchor_1",
                    action="add",
                    parent_item_id="category_1",
                    slot_id="TASK.SUMMARY",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_item_id="category_1",  # 컨테이너에 바로 붙임 — 잘못됨
                    slot_id="TASK.BASIC.PURPOSE",
                    text="원인을 조사했다",
                    source_item_ids=["it_2"],
                ),
            ]
        )
    )
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "결제 오류를 해결했다", "source": "message"},
            {"item_id": "it_2", "text": "원인을 조사했다", "source": "message"},
        ]
    )

    with pytest.raises(LlmError) as exc_info:
        await structure_blocks(state)

    # 카테고리가 level 4 슬롯을 모두 생성 못 했다는 일반 메시지가 아니라,
    # 원인을 정확히 짚는 메시지여야 한다.
    assert "앵커" in str(exc_info.value.__cause__)


@pytest.mark.asyncio
async def test_duplicate_section_kind_is_rejected(fake_dependencies):
    """같은 카테고리를 두 번 만들 수 없다.

    실제로 재현된 경우다. 문제해결 템플릿 6종 중 하나만 골라야 하는데, 모델이
    템플릿마다 별도의 PROBLEM_SOLVING 카테고리 컨테이너를 중첩해서 만들었다.

    두 카테고리 모두 슬롯까지 올바르게 채워서 만든다 — 그래야 "슬롯을 모두
    생성해야 한다" 는 기존 검사가 아니라 **중복 검사 자체**가 걸리는지 본다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="category_1", action="add", parent_ref="exp_1", section_kind="DETAIL"
                ),
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_item_id="category_1",
                    slot_id="DETAIL.MOTIVATION",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
                StructureLlmItem(
                    item_id="category_2", action="add", parent_ref="exp_1", section_kind="DETAIL"
                ),
                StructureLlmItem(
                    item_id="blk_2",
                    action="add",
                    parent_item_id="category_2",
                    slot_id="DETAIL.MOTIVATION",
                    text="재시도 로직을 추가했다",
                    source_item_ids=["it_2"],
                ),
            ]
        )
    )
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "결제 오류를 해결했다", "source": "message"},
            {"item_id": "it_2", "text": "재시도 로직을 추가했다", "source": "message"},
        ]
    )

    with pytest.raises(LlmError):
        await structure_blocks(state)


@pytest.mark.asyncio
async def test_new_sibling_after_ref_is_rejected(fake_dependencies):
    """방금 만든 블록의 id를 after_ref 에 쓰면 거부한다.

    실제로 재현된 경우다. `after_ref` 는 기존 블록 별칭만 가리킬 수 있는데,
    모델이 이걸로 새로 만든 카테고리끼리 순서를 매기려다 걸렸다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="category_1", action="add", parent_ref="exp_1", section_kind="DETAIL"
                ),
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_item_id="category_1",
                    slot_id="DETAIL.MOTIVATION",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
                StructureLlmItem(
                    item_id="category_2",
                    action="add",
                    parent_ref="exp_1",
                    section_kind="TASK",
                    after_ref="category_1",  # 기존 별칭이 아니라 방금 만든 id
                ),
            ]
        )
    )

    with pytest.raises(LlmError):
        await structure_blocks(make_state())


@pytest.mark.asyncio
async def test_unknown_slot_and_partial_template_are_rejected(fake_dependencies):
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                )
            ]
        )
    )

    with pytest.raises(LlmError):
        await structure_blocks(make_state())


@pytest.mark.asyncio
async def test_foreign_parent_alias_is_rejected(fake_dependencies):
    """다른 활동의 alias를 지어내도 구조화 단계에서 차단한다."""
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="exp_999",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                )
            ]
        )
    )

    with pytest.raises(LlmError):
        await structure_blocks(make_state())


@pytest.mark.asyncio
async def test_missing_activity_tree_is_rejected_before_llm(fake_dependencies):
    """상세 alias 트리 없이 LLM이 부모를 추측하게 두지 않는다."""
    prompts = fake_dependencies(StructureOutput(items=[]))

    with pytest.raises(LlmError) as exc_info:
        await structure_blocks(make_state(activity_tree_text=None))

    assert exc_info.value.failed_node == "structure"
    assert prompts == []


@pytest.mark.asyncio
async def test_new_child_gap_is_fixed_to_anchor(fake_dependencies):
    """gap 답변은 다른 활동이나 다른 블록 아래로 재배정할 수 없다."""
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="exp_1",
                    text="재시도 로직을 추가했다",
                    source_item_ids=["it_1"],
                )
            ]
        )
    )
    state = make_state(
        new_items=[],
        gap_answer_items=[
            {"item_id": "it_1", "text": "재시도 로직을 추가했다", "source": "message"}
        ],
        active_gap={"gap_type": "new_child_block", "anchor_block_id": "305"},
    )

    with pytest.raises(LlmError):
        await structure_blocks(state)


def test_next_node_uses_refine_only_with_structure_result():
    assert next_node({"structured_items": [{"item_id": "it_1"}]}) == "refine"
    assert next_node({"structured_items": []}) == "fallback"


def test_catalog_fixture_is_valid():
    """테스트 fixture도 실제 카탈로그 제약을 만족해야 한다."""
    assert TemplateCatalog.model_validate(catalog_payload()).version == "v1"


@pytest.mark.parametrize(
    "template_id",
    ["BASIC", "INTERPERSONAL", "PERFORMANCE", "TROUBLESHOOTING", "FEEDBACK", "RECOVERY"],
)
def test_problem_solving_templates_accept_all_required_slots(template_id):
    """문제해결 6종은 선택된 템플릿의 5단계 슬롯 4개를 모두 전개해야 한다."""
    slot_ids = [f"PROBLEM_SOLVING.{template_id}.SLOT_{index}" for index in range(1, 5)]
    catalog = TemplateCatalog.model_validate(
        {
            "version": "v1",
            "sections": [
                {
                    "section_id": "PROBLEM_SOLVING",
                    "label": "문제해결",
                    "slots": [
                        {
                            "slot_id": "PROBLEM_SOLVING.SUMMARY",
                            "level": 4,
                            "placeholder": "에피소드 요약",
                            "example": "결제 오류 해결",
                            "is_anchor": True,
                        }
                    ],
                    "templates": [
                        {
                            "template_id": template_id,
                            "label": template_id,
                            "slots": [
                                {
                                    "slot_id": slot_id,
                                    "level": 5,
                                    "placeholder": "작성 가이드",
                                    "example": "작성 예시",
                                }
                                for slot_id in slot_ids
                            ],
                        }
                    ],
                }
            ],
        }
    )
    source = [{"item_id": "it_1", "text": "결제 오류를 분석하고 재시도 로직을 추가했다."}]
    items = [
        StructureLlmItem(
            item_id="blk_1",
            action="add",
            parent_ref="b_1",
            slot_id=slot_ids[0],
            text=source[0]["text"],
            source_item_ids=["it_1"],
        ),
        *[
            StructureLlmItem(
                item_id=f"empty_{index}", action="add", parent_ref="b_1", slot_id=slot_id
            )
            for index, slot_id in enumerate(slot_ids[1:], start=1)
        ],
    ]

    validated = _validate_output(
        items,
        source_items=source,
        catalog=catalog,
        state={"target_experience_alias": "exp_1", "alias_to_block_id": {"b_1": "305"}},
    )

    assert [item.slot_id for item in validated] == slot_ids
