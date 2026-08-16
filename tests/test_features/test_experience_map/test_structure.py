"""블록 단위 구조화 노드 테스트 (에이전트 문서 3절, 5-5)."""

import pytest
from langchain_core.runnables import RunnableLambda

from features.experience_map.errors import LlmError
from features.experience_map.nodes import structure as structure_node
from features.experience_map.nodes.structure import _validate_output, next_node, structure_blocks
from features.experience_map.schemas import StructuredItem, StructureOutput
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
                StructuredItem(
                    item_id="category_1", action="add", parent_ref="exp_1", section_kind="DETAIL"
                ),
                StructuredItem(
                    item_id="it_1",
                    action="add",
                    parent_item_id="category_1",
                    slot_id="DETAIL.MOTIVATION",
                    text="결제 오류를 해결했다",
                ),
            ]
        )
    )

    result = await structure_blocks(make_state())

    assert [item["item_id"] for item in result["structured_items"]] == ["category_1", "it_1"]
    assert result["structured_items"][1]["text"] == "결제 오류를 해결했다"
    assert "[exp_1] 교내 커머스 리뉴얼" in prompts[0]
    assert "[DETAIL.MOTIVATION]" in prompts[0]


@pytest.mark.asyncio
async def test_template_expands_empty_slots(fake_dependencies):
    """템플릿을 사용하면 정보 없는 level 5 슬롯도 text 없이 남긴다."""
    fake_dependencies(
        StructureOutput(
            items=[
                StructuredItem(
                    item_id="it_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="결제 오류를 해결했다",
                ),
                StructuredItem(
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
@pytest.mark.parametrize(
    "items",
    [
        [],
        [StructuredItem(item_id="it_1", action="add", parent_ref="exp_1", text="바뀐 원문")],
    ],
)
async def test_missing_or_changed_source_is_rejected(fake_dependencies, items):
    """구조화가 원문을 누락하거나 고쳐 쓰면 다음 단계로 넘기지 않는다."""
    fake_dependencies(StructureOutput(items=items))

    with pytest.raises(LlmError) as exc_info:
        await structure_blocks(make_state())

    assert exc_info.value.failed_node == "structure"


@pytest.mark.asyncio
async def test_unknown_slot_and_partial_template_are_rejected(fake_dependencies):
    fake_dependencies(
        StructureOutput(
            items=[
                StructuredItem(
                    item_id="it_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="결제 오류를 해결했다",
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
                StructuredItem(
                    item_id="it_1",
                    action="add",
                    parent_ref="exp_999",
                    text="결제 오류를 해결했다",
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
                StructuredItem(
                    item_id="it_1",
                    action="add",
                    parent_ref="exp_1",
                    text="재시도 로직을 추가했다",
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
        StructuredItem(
            item_id="it_1",
            action="add",
            parent_ref="b_1",
            slot_id=slot_ids[0],
            text=source[0]["text"],
        ),
        *[
            StructuredItem(
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
