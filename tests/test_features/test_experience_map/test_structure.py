"""블록 단위 구조화 노드 테스트 (에이전트 문서 3절, 5-5)."""

import pytest
from langchain_core.runnables import RunnableLambda

from features.experience_map.errors import LlmError
from features.experience_map.nodes import structure as structure_node
from features.experience_map.nodes.structure import (
    _prune_extra_templates,
    _reparent_orphan_level5_items,
    _validate_output,
    next_node,
    structure_blocks,
)
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
    """카탈로그와 LLM 대역을 주입하고 렌더된 프롬프트를 모은다.

    `result`에 리스트를 주면 호출 순서대로 하나씩 반환한다(마지막 값은
    이후 호출에도 계속 쓰인다) — 원문 누락 재시도처럼 첫 호출과 재시도
    호출의 응답이 달라야 하는 테스트에 쓴다.
    """

    def _set(result: StructureOutput | Exception | list) -> list[str]:
        prompts: list[str] = []
        sequence = result if isinstance(result, list) else [result]

        async def _fetcher():
            return catalog_payload()

        client = TemplateCatalogClient(_fetcher)
        monkeypatch.setattr(structure_node, "get_template_catalog_client", lambda: client)

        async def _handle(prompt_value) -> StructureOutput:
            prompts.append(prompt_value.to_string())
            current = sequence[min(len(prompts) - 1, len(sequence) - 1)]
            if isinstance(current, Exception):
                raise current
            return current

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
async def test_merge_ignores_llm_typed_separator(fake_dependencies):
    """병합 블록의 text는 LLM이 뭐라고 썼든 코드가 원문을 그대로 이어붙여 만든다.

    실제로 재현된 경우다. 문장이 마침표로 끝나는데 모델이 다음 조각을 띄어쓰기
    없이 바로 이어 붙이는 등, 이어붙이는 방식이 매번 달랐다. text는 이제
    LLM이 검증만 통과하면 되는 게 아니라 애초에 코드가 새로 조립하므로,
    LLM이 뭘 쓰든(공백 유무 등) 항상 같은 결과가 나와야 한다.
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

    assert result["structured_items"][0]["text"] == "원인을 조사했다. 해결책을 적용했다"


@pytest.mark.asyncio
async def test_merged_text_ignores_llm_summary(fake_dependencies):
    """LLM이 합친 text를 요약·윤문해도, 실제 커밋되는 text는 원문 그대로다.

    예전엔 이 경우를 거부하고 재시도를 유도했는데, LLM이 재시도에서도 같은
    실수를 반복하는 일이 잦았다. 이제는 LLM이 뭐라고 썼든 코드가 무시하고
    원문을 그대로 조립하므로, 이 실수 자체가 실패로 이어지지 않는다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="원인 조사 후 해결",  # 요약됨 — 무시되고 원문으로 대체된다
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

    result = await structure_blocks(state)

    assert result["structured_items"][0]["text"] == "원인을 조사했다 해결책을 적용했다"


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
async def test_missing_source_is_rejected(fake_dependencies):
    """구조화가 원문 item을 아예 누락하면 다음 단계로 넘기지 않는다."""
    fake_dependencies(StructureOutput(items=[]))

    with pytest.raises(LlmError) as exc_info:
        await structure_blocks(make_state())

    assert exc_info.value.failed_node == "structure"


@pytest.mark.asyncio
async def test_changed_source_text_is_silently_corrected(fake_dependencies):
    """LLM이 원문을 고쳐 써도, source_item_ids만 맞으면 실패하지 않고 원문으로
    바로잡힌 채 커밋된다 — text 자체는 이제 검증 대상이 아니다."""
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="exp_1",
                    text="바뀐 원문",
                    source_item_ids=["it_1"],
                )
            ]
        )
    )

    result = await structure_blocks(make_state())

    assert result["structured_items"][0]["text"] == "결제 오류를 해결했다"


@pytest.mark.asyncio
async def test_level5_slot_attached_to_container_is_auto_reparented_to_anchor(fake_dependencies):
    """level 5 슬롯이 앵커가 아니라 카테고리 컨테이너에 바로 붙으면, 코드가
    같은 배치의 진짜 앵커 밑으로 자동으로 옮긴다.

    실제로 재현된 경우다. 모델이 하위 템플릿의 level 5 슬롯 하나를 앵커가
    아니라 카테고리 컨테이너에 바로 매달았다. 예전엔 이걸 에러로 거부하고
    재시도를 유도했는데, 모델이 재시도에서도 같은 실수를 반복하는 일이
    잦아서 — 이미 배치 안에 진짜 앵커가 있는 이상 코드가 결정론적으로
    바로잡는다.
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

    result = await structure_blocks(state)

    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    anchor_id = items_by_slot["TASK.SUMMARY"]["item_id"]
    assert items_by_slot["TASK.BASIC.PURPOSE"]["parent_item_id"] == anchor_id


@pytest.mark.asyncio
async def test_level5_using_parent_ref_for_batch_local_anchor_is_reinterpreted(
    fake_dependencies,
):
    """방금 만든 앵커를 `parent_ref`로 가리키면 `parent_item_id`로 바로잡는다.

    실제 PDF 이력서 입력으로 재현된 경우다. 모델이 새로 만든 앵커
    (`blk_1`)를 그 하위 level 5 슬롯의 부모로 연결하면서 `parent_item_id`가
    아니라 `parent_ref="blk_1"`을 썼다. `blk_1`은 활동 트리의 블록 별칭일
    수 없으므로(서버가 그런 별칭을 주지 않는다), 방금 만든 item을 가리키려던
    의도가 명백해 코드가 `parent_item_id`로 고쳐 끼운다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.SUMMARY",
                    text="사내 결제 시스템 백엔드 개발을 담당했다",
                    source_item_ids=["it_1"],
                ),
                StructureLlmItem(
                    item_id="blk_2",
                    action="add",
                    parent_ref="blk_1",  # 잘못됨 — parent_item_id="blk_1"이어야 한다
                    slot_id="TASK.BASIC.PURPOSE",
                    text="전환율 개선을 목표로 했다",
                    source_item_ids=["it_2"],
                ),
                StructureLlmItem(
                    item_id="blk_3",
                    action="add",
                    parent_item_id="blk_1",
                    slot_id="TASK.BASIC.RESULT",
                    text="전환율이 올랐다",
                    source_item_ids=["it_3"],
                ),
            ]
        )
    )
    state = make_state(
        new_items=[
            {
                "item_id": "it_1",
                "text": "사내 결제 시스템 백엔드 개발을 담당했다",
                "source": "file",
            },
            {"item_id": "it_2", "text": "전환율 개선을 목표로 했다", "source": "file"},
            {"item_id": "it_3", "text": "전환율이 올랐다", "source": "file"},
        ]
    )

    result = await structure_blocks(state)

    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    anchor_id = items_by_slot["TASK.SUMMARY"]["item_id"]
    assert items_by_slot["TASK.BASIC.PURPOSE"]["parent_ref"] is None
    assert items_by_slot["TASK.BASIC.PURPOSE"]["parent_item_id"] == anchor_id


@pytest.mark.asyncio
async def test_anchor_reusing_same_source_as_its_level5_child_is_emptied(fake_dependencies):
    """앵커가 하위 level 5 슬롯과 똑같은 원문을 또 쓰면, 앵커를 빈 슬롯으로 되돌린다.

    실제 PDF 이력서 입력으로 재현된 경우다. 프롬프트는 "같은 입력 item을
    SUMMARY 앵커와 세부 슬롯에 동시에 쓰지 말고, 더 구체적인 세부 슬롯에만
    배정하라"고 명시하는데도, 모델이 같은 문장을 앵커와 그 바로 아래
    level 5 슬롯에 완전히 똑같이 중복 배정했다 — 원문 하나가 두 블록에서
    쓰여 `_validate_source_coverage`가 거부했다. level 5가 항상 더
    구체적이므로, 코드가 앵커 쪽 배정만 비워 원문을 한 곳에서만 쓰게 한다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.SUMMARY",
                    text="전환율 개선을 목표로 했다",
                    source_item_ids=["it_1"],
                ),
                StructureLlmItem(
                    item_id="blk_2",
                    action="add",
                    parent_item_id="blk_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="전환율 개선을 목표로 했다",
                    source_item_ids=["it_1"],  # 앵커와 완전히 같은 원문 — 중복
                ),
                StructureLlmItem(
                    item_id="blk_3",
                    action="add",
                    parent_item_id="blk_1",
                    slot_id="TASK.BASIC.RESULT",
                    text="전환율이 올랐다",
                    source_item_ids=["it_2"],
                ),
            ]
        )
    )
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "전환율 개선을 목표로 했다", "source": "file"},
            {"item_id": "it_2", "text": "전환율이 올랐다", "source": "file"},
        ]
    )

    result = await structure_blocks(state)

    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    assert items_by_slot["TASK.SUMMARY"]["text"] is None
    assert items_by_slot["TASK.BASIC.PURPOSE"]["text"] == "전환율 개선을 목표로 했다"


@pytest.mark.asyncio
async def test_missing_source_item_triggers_one_targeted_retry(fake_dependencies):
    """원문 item을 통째로 빠뜨리면, 빠진 것만 콕 집어 한 번 더 시도한다.

    실제 PDF 이력서(원문 15개) 입력으로 재현된 경우다. 첫 시도에서 모델이
    일부 원문 item을 어느 블록에도 배정하지 않고 빠뜨렸다 — 그래프 수준
    RetryPolicy가 같은 프롬프트를 그대로 재시도해도 결과가 매번 달라
    운에 맡기는 것보다, 빠진 item을 명시해 다시 요청하는 편이 낫다. 이
    재시도는 이 노드 실행 한 번 안에서 끝나므로 "노드마다 1회"라는
    문서(3절)의 자동 재시도 정책과 별개다.
    """
    first_attempt = StructureOutput(
        items=[
            StructureLlmItem(
                item_id="blk_1",
                action="add",
                parent_ref="b_1",
                slot_id="TASK.BASIC.PURPOSE",
                text="전환율 개선을 목표로 했다",
                source_item_ids=["it_1"],
            ),
            # it_2 는 빠뜨렸다.
        ]
    )
    second_attempt = StructureOutput(
        items=[
            # 재시도는 빠진 it_2 하나만 새로 맡는다 — blk_1(it_1)은 1차에서
            # 이미 성공했으므로 다시 만들 필요가 없다.
            StructureLlmItem(
                item_id="blk_2",
                action="add",
                parent_ref="b_1",
                slot_id="TASK.BASIC.RESULT",
                text="전환율이 올랐다",
                source_item_ids=["it_2"],
            ),
        ]
    )
    prompts = fake_dependencies([first_attempt, second_attempt])
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "전환율 개선을 목표로 했다", "source": "file"},
            {"item_id": "it_2", "text": "전환율이 올랐다", "source": "file"},
        ]
    )

    result = await structure_blocks(state)

    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    assert items_by_slot["TASK.BASIC.RESULT"]["text"] == "전환율이 올랐다"
    assert len(prompts) == 2
    assert "it_2" in prompts[1] and "전환율이 올랐다" in prompts[1]
    # 재시도 프롬프트의 "반영할 원문 item" 절에는 빠진 it_2만 실린다 — 이미
    # 성공한 it_1을 다시 만들면서 새 실수를 낼 여지를 줄인다.
    retry_source_section = prompts[1].split("반영할 원문 item:")[1]
    assert "it_1" not in retry_source_section


@pytest.mark.asyncio
async def test_schema_violating_response_retries_the_same_batch(fake_dependencies):
    """모델 응답이 스키마 자체를 어겨 파싱이 실패해도, 같은 배치를 한 번 더 시도한다.

    실제로 재현된 경우다. 모델이 `parent_ref`도 `parent_item_id`도 없는 item을
    내서 pydantic이 파싱 단계에서 바로 거부했다 — 결과를 볼 기회조차 없어
    "원문 누락" 재시도 경로를 못 타고 그대로 예외가 번져나갔다. 여러 배치로
    나눠 처리할 때 이 배치 하나의 일시적 실수가 이미 끝낸 앞 배치들까지
    통째로 날리면 안 된다.
    """
    prompts = fake_dependencies(
        [
            ValueError("모델이 parent_ref/parent_item_id 없는 item을 냄 (pydantic 파싱 실패)"),
            StructureOutput(
                items=[
                    StructureLlmItem(
                        item_id="blk_1",
                        action="add",
                        parent_ref="b_1",
                        slot_id="TASK.BASIC.PURPOSE",
                        text="전환율 개선을 목표로 했다",
                        source_item_ids=["it_1"],
                    ),
                ]
            ),
        ]
    )
    state = make_state(
        new_items=[{"item_id": "it_1", "text": "전환율 개선을 목표로 했다", "source": "file"}]
    )

    result = await structure_blocks(state)

    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    assert items_by_slot["TASK.BASIC.PURPOSE"]["text"] == "전환율 개선을 목표로 했다"
    assert len(prompts) == 2


@pytest.mark.asyncio
async def test_large_input_is_split_into_batches_that_reuse_earlier_anchors(
    fake_dependencies, monkeypatch
):
    """원문이 많으면 배치로 나눠 순차 처리하고, 뒤 배치는 앞 배치가 만든 앵커를 재사용한다.

    실제 PDF 이력서(원문 15개)로 재현된 문제의 근본 대책이다. 한 번에 너무
    많이 맡기면 모델이 일부를 빠뜨리거나 잘못 연결하는 사고가 잦아져서,
    작은 배치로 나눠 순차 처리한다 — 뒤 배치는 `previous_batch_note`로 앞
    배치가 이미 만든 카테고리·앵커를 안내받아 새로 만들지 않고 재사용해야
    한다.
    """
    monkeypatch.setattr(structure_node, "MAX_SOURCE_ITEMS_PER_STRUCTURE_BATCH", 2)

    first_batch_response = StructureOutput(
        items=[
            StructureLlmItem(
                item_id="category_1", action="add", parent_ref="exp_1", section_kind="TASK"
            ),
            StructureLlmItem(
                item_id="anchor_1",
                action="add",
                parent_item_id="category_1",
                slot_id="TASK.SUMMARY",
                text="결제 시스템 백엔드 개발을 담당했다",
                source_item_ids=["it_1"],
            ),
            StructureLlmItem(
                item_id="blk_purpose",
                action="add",
                parent_item_id="anchor_1",
                slot_id="TASK.BASIC.PURPOSE",
                text="전환율 개선을 목표로 했다",
                source_item_ids=["it_2"],
            ),
        ]
    )
    second_batch_response = StructureOutput(
        items=[
            StructureLlmItem(
                item_id="blk_result",
                action="add",
                # 앞 배치가 만든 앵커를 재사용한다. 이 요청의 첫 호출(call_index
                # 0)이 만든 item_id는 병합할 대상이 없어 접두사 없이 그대로다.
                parent_item_id="anchor_1",
                slot_id="TASK.BASIC.RESULT",
                text="전환율이 올랐다 재발도 없었다",
                source_item_ids=["it_3", "it_4"],
            ),
        ]
    )
    prompts = fake_dependencies([first_batch_response, second_batch_response])
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "결제 시스템 백엔드 개발을 담당했다", "source": "file"},
            {"item_id": "it_2", "text": "전환율 개선을 목표로 했다", "source": "file"},
            {"item_id": "it_3", "text": "전환율이 올랐다", "source": "file"},
            {"item_id": "it_4", "text": "재발도 없었다", "source": "file"},
        ]
    )

    result = await structure_blocks(state)

    assert len(prompts) == 2
    # 두 번째 배치 프롬프트에 첫 배치가 만든 앵커 안내가 실려야 한다.
    assert "anchor_1" in prompts[1] and "TASK.SUMMARY" in prompts[1]
    # 첫 배치 프롬프트에는 아직 아무것도 안내할 게 없다.
    assert "anchor_1" not in prompts[0]

    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    assert items_by_slot["TASK.SUMMARY"]["text"] == "결제 시스템 백엔드 개발을 담당했다"
    assert items_by_slot["TASK.BASIC.PURPOSE"]["text"] == "전환율 개선을 목표로 했다"
    assert items_by_slot["TASK.BASIC.RESULT"]["text"] == "전환율이 올랐다 재발도 없었다"
    assert (
        items_by_slot["TASK.BASIC.RESULT"]["parent_item_id"]
        == items_by_slot["TASK.SUMMARY"]["item_id"]
    )


@pytest.mark.asyncio
async def test_batches_reusing_the_same_item_id_are_namespaced_apart(
    fake_dependencies, monkeypatch
):
    """서로 다른 배치가 우연히 같은 item_id를 지어도 충돌하지 않는다.

    실제 PDF 이력서(원문 15개, 여러 배치)로 재현된 경우다. 각 배치는 서로의
    출력을 모르는 채 독립적으로 item_id를 지으므로, 두 배치가 똑같이
    "blk_1"을 써서 `item_id가 중복되었습니다`로 거부된 적이 있다.
    """
    monkeypatch.setattr(structure_node, "MAX_SOURCE_ITEMS_PER_STRUCTURE_BATCH", 1)

    first_batch_response = StructureOutput(
        items=[
            StructureLlmItem(
                item_id="blk_1",  # 배치 1의 blk_1
                action="add",
                parent_ref="b_1",
                slot_id="TASK.BASIC.PURPOSE",
                text="전환율 개선을 목표로 했다",
                source_item_ids=["it_1"],
            ),
        ]
    )
    second_batch_response = StructureOutput(
        items=[
            StructureLlmItem(
                item_id="blk_1",  # 배치 2도 우연히 같은 이름을 지었다
                action="add",
                parent_ref="b_1",
                slot_id="TASK.BASIC.RESULT",
                text="전환율이 올랐다",
                source_item_ids=["it_2"],
            ),
        ]
    )
    fake_dependencies([first_batch_response, second_batch_response])
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "전환율 개선을 목표로 했다", "source": "file"},
            {"item_id": "it_2", "text": "전환율이 올랐다", "source": "file"},
        ]
    )

    result = await structure_blocks(state)

    item_ids = [item["item_id"] for item in result["structured_items"]]
    assert len(item_ids) == len(set(item_ids))
    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    assert items_by_slot["TASK.BASIC.PURPOSE"]["text"] == "전환율 개선을 목표로 했다"
    assert items_by_slot["TASK.BASIC.RESULT"]["text"] == "전환율이 올랐다"


@pytest.mark.asyncio
async def test_duplicate_slot_under_same_parent_is_merged_not_rejected(fake_dependencies):
    """같은 부모 아래 같은 slot을 두 item으로 쪼개 만들면 하나로 합친다.

    실제 PDF 이력서 입력으로 재현된 경우다. 모델이 TASK.BASIC.PURPOSE를
    같은 앵커 아래 두 item으로 나눠 만들어 "같은 slot을 두 번 이상
    만들었습니다"로 거부됐다. 어느 쪽을 버릴지 모호하므로(둘 다 서로 다른
    원문일 수 있다) 버리지 않고 원문을 합쳐 하나로 만든다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",
                    text="전환율 개선을 목표로 했다",
                    source_item_ids=["it_1"],
                ),
                StructureLlmItem(
                    item_id="blk_2",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.PURPOSE",  # 같은 부모·같은 slot 중복
                    text="이탈률도 함께 낮추고자 했다",
                    source_item_ids=["it_2"],
                ),
                StructureLlmItem(
                    item_id="blk_3",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.RESULT",
                    text="전환율이 올랐다",
                    source_item_ids=["it_3"],
                ),
            ]
        )
    )
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "전환율 개선을 목표로 했다", "source": "file"},
            {"item_id": "it_2", "text": "이탈률도 함께 낮추고자 했다", "source": "file"},
            {"item_id": "it_3", "text": "전환율이 올랐다", "source": "file"},
        ]
    )

    result = await structure_blocks(state)

    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    assert items_by_slot["TASK.BASIC.PURPOSE"]["text"] == (
        "전환율 개선을 목표로 했다 이탈률도 함께 낮추고자 했다"
    )
    assert items_by_slot["TASK.BASIC.RESULT"]["text"] == "전환율이 올랐다"


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
async def test_new_category_contradicting_self_reported_classification_is_rejected(
    fake_dependencies,
):
    """existing_categories에서 이미 있다고 판단한 section을 또 새로 만들면 거부한다.

    실제로 재현된 경우다. 활동 트리에 이미 TASK 컨테이너(자식이 자유 텍스트로만
    있는)가 있는데, 이후 요청에서 모델이 그 컨테이너를 재사용하지 않고 같은
    section의 새 카테고리를 또 만들었다 — "트리에 있으면 재사용하라"는 지시만
    으로는 재발이 잦아, 모델이 스스로 분류한 결과와 실제 행동이 모순되면 코드가
    바로 걸러 재시도를 유도한다.
    """
    fake_dependencies(
        StructureOutput(
            existing_categories=[{"alias": "b_1", "section_kind": "TASK"}],
            items=[
                StructureLlmItem(
                    item_id="category_1", action="add", parent_ref="exp_1", section_kind="TASK"
                ),
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_item_id="category_1",
                    slot_id="TASK.SUMMARY",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
            ],
        )
    )

    with pytest.raises(LlmError):
        await structure_blocks(make_state())


@pytest.mark.asyncio
async def test_reporting_the_activity_alias_itself_as_existing_category_is_ignored(
    fake_dependencies,
):
    """활동 별칭 자체를 '이미 있는 카테고리'로 잘못 신고해도 새 카테고리 생성은 정상 처리된다.

    실제로 재현된 경우다. 카테고리 컨테이너가 하나도 없는 활동에서 모델이
    existing_categories에 활동 별칭(exp_1)을 section_kind=TASK로 잘못 신고했다.
    새 카테고리는 항상 활동 별칭을 parent_ref로 삼으므로, 이 신고를 곧이곧대로
    믿으면 정상적으로 새 카테고리를 만드는 매 요청이 자기모순으로 오판되어
    거부됐다. 카테고리 컨테이너는 활동의 하위일 뿐 활동 자신일 수 없으므로,
    활동 별칭을 가리키는 신고는 무시해야 한다.
    """
    fake_dependencies(
        StructureOutput(
            existing_categories=[{"alias": "exp_1", "section_kind": "TASK"}],
            items=[
                StructureLlmItem(
                    item_id="category_1", action="add", parent_ref="exp_1", section_kind="TASK"
                ),
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_item_id="category_1",
                    slot_id="TASK.SUMMARY",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
            ],
        )
    )

    result = await structure_blocks(make_state())

    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    assert items_by_slot["TASK.SUMMARY"]["text"] == "결제 오류를 해결했다"


@pytest.mark.asyncio
async def test_new_anchor_contradicting_self_reported_existing_anchor_is_rejected(
    fake_dependencies,
):
    """existing_anchor_alias로 이미 있다고 신고한 앵커를 또 새로 만들면 거부한다.

    실제로 재현된 경우다. 카테고리 컨테이너는 제대로 재사용했는데, 그 아래
    앵커(level 4)는 매 턴 새로 하나씩 또 만들어서 같은 컨테이너 밑에 "담당업무
    요약" 앵커가 두 개 생겼다. 컨테이너 재사용 검증만으로는 안 걸리므로,
    앵커도 스스로 신고하게 하고 그 신고와 모순되면 코드가 거른다.
    """
    fake_dependencies(
        StructureOutput(
            existing_categories=[
                {"alias": "b_1", "section_kind": "TASK", "existing_anchor_alias": "b_4"}
            ],
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.SUMMARY",
                    text="결제 시스템을 강화했다",
                    source_item_ids=["it_1"],
                ),
            ],
        )
    )
    state = make_state(alias_to_block_id={"exp_1": "101", "b_1": "305", "b_4": "306"})

    with pytest.raises(LlmError):
        await structure_blocks(state)


@pytest.mark.asyncio
async def test_new_anchor_duplicating_undeclared_existing_slots_is_rejected(fake_dependencies):
    """`existing_categories` 자기 신고를 아예 빠뜨려도, 트리의 빈 슬롯 가이드 문구로
    코드가 독립적으로 앵커 중복을 잡는다.

    실제로 재현된 경우다. 모델이 `existing_categories`에 아무것도 신고하지
    않은 채(신고 자체를 빠뜨림) 이미 TASK 템플릿이 붙어 있는 컨테이너에 새
    앵커를 또 만들었다. 자기 신고에만 기대면 이 경우를 못 잡으므로, 활동
    트리에 이미 커밋된 빈 슬롯의 가이드 문구(명세 3-7)를 코드가 직접 읽어
    같은 section이 이미 있는지 확인한다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.SUMMARY",
                    text="결제 시스템을 강화했다",
                    source_item_ids=["it_1"],
                ),
            ],
        )
    )
    state = make_state(
        alias_to_block_id={"exp_1": "101", "b_1": "305", "b_4": "306", "b_5": "307"},
        activity_tree_text=(
            "[exp_1] 교내 커머스 리뉴얼\n"
            "  [b_1] (빈 블록)\n"
            "    [b_4] 기존 담당업무 내용\n"
            "      [b_5] (빈 블록 — 가이드: 결과)"
        ),
    )

    with pytest.raises(LlmError):
        await structure_blocks(state)


@pytest.mark.asyncio
async def test_new_sibling_after_ref_is_cleared_not_rejected(fake_dependencies):
    """방금 만든 블록의 id를 after_ref 에 쓰면 코드가 비운다.

    실제로 재현된 경우다. `after_ref` 는 기존 블록 별칭만 가리킬 수 있는데,
    모델이 이걸로 새로 만든 카테고리끼리 순서를 매기려다 걸렸다. 새로
    만드는 형제끼리의 순서는 `items` 배열 순서로 이미 확보되므로, 잘못된
    참조는 거부 대신 코드가 비운다 — 순서 정보를 잃지 않는다.
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
                StructureLlmItem(
                    item_id="anchor_2",
                    action="add",
                    parent_item_id="category_2",
                    slot_id="TASK.SUMMARY",
                    text="전환율 개선을 담당했다",
                    source_item_ids=["it_2"],
                ),
            ]
        )
    )
    state = make_state(
        new_items=[
            {"item_id": "it_1", "text": "결제 오류를 해결했다", "source": "message"},
            {"item_id": "it_2", "text": "전환율 개선을 담당했다", "source": "message"},
        ]
    )

    result = await structure_blocks(state)

    items_by_id = {item["item_id"]: item for item in result["structured_items"]}
    assert items_by_id["category_2"]["after_ref"] is None


@pytest.mark.asyncio
async def test_partial_template_is_auto_filled_not_rejected(fake_dependencies):
    """모델이 빈 슬롯 placeholder를 누락해도 코드가 나머지를 채운다.

    원문이 짧으면 모델이 채울 slot 하나만 만들고 나머지 null placeholder를
    빠뜨리는 사고가 실제로 반복됐다. 프롬프트만으로는 신뢰할 수 없어서, 이미
    출력에 드러난 하위 템플릿·부모 정보로 코드가 결정론적으로 나머지 slot을
    채운다 — 더 이상 이 사고로 요청 전체가 실패하지 않는다.
    """
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

    result = await structure_blocks(make_state())

    items_by_slot = {item["slot_id"]: item for item in result["structured_items"]}
    assert items_by_slot["TASK.BASIC.PURPOSE"]["text"] == "결제 오류를 해결했다"
    assert items_by_slot["TASK.BASIC.RESULT"]["text"] is None


@pytest.mark.asyncio
async def test_unknown_slot_id_is_rejected(fake_dependencies):
    """카탈로그에 없는 slot_id를 지어내면 자동 보정 대신 거부한다."""
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="TASK.BASIC.NOT_A_REAL_SLOT",
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


def test_reparent_orphan_level5_creates_anchor_when_missing():
    """level 5가 앵커 없이 컨테이너 별칭에 바로 붙으면, 빈 앵커를 새로 만들어
    그 아래로 옮긴다 — 실제로 모델이 '기존 카테고리 재사용'을 지시받고도
    앵커를 건너뛰고 level 5를 컨테이너에 형제로 붙이는 사고가 반복됐다.
    """
    catalog = TemplateCatalog.model_validate(catalog_payload())
    items = [
        StructureLlmItem(
            item_id="blk_1",
            action="add",
            parent_ref="b_1",
            slot_id="TASK.BASIC.PURPOSE",
            text="결제 오류를 해결했다",
            source_item_ids=["it_1"],
        ),
    ]

    result = _reparent_orphan_level5_items(items, catalog)

    by_id = {item.item_id: item for item in result}
    orphan = by_id["blk_1"]
    assert orphan.parent_ref is None
    assert orphan.parent_item_id is not None
    anchor = by_id[orphan.parent_item_id]
    assert anchor.slot_id == "TASK.SUMMARY"
    assert anchor.parent_ref == "b_1"
    assert anchor.text is None


def test_reparent_orphan_level5_reuses_anchor_already_in_batch():
    """같은 배치에 이미 맞는 앵커가 있으면, 새로 만들지 않고 거기로 연결한다."""
    catalog = TemplateCatalog.model_validate(catalog_payload())
    items = [
        StructureLlmItem(
            item_id="anchor_1",
            action="add",
            parent_ref="b_1",
            slot_id="TASK.SUMMARY",
            text="결제 오류 해결 업무",
            source_item_ids=["it_1"],
        ),
        StructureLlmItem(
            item_id="blk_1",
            action="add",
            parent_ref="b_1",
            slot_id="TASK.BASIC.PURPOSE",
            text="결제 오류를 해결했다",
            source_item_ids=["it_2"],
        ),
    ]

    result = _reparent_orphan_level5_items(items, catalog)

    assert len(result) == 2  # 앵커를 새로 만들지 않았다.
    by_id = {item.item_id: item for item in result}
    assert by_id["blk_1"].parent_item_id == "anchor_1"
    assert by_id["blk_1"].parent_ref is None


def test_reparent_orphan_level5_fixes_fake_anchor_hub():
    """level 5 형제 중 하나를 마치 앵커인 것처럼 가리키는 경우도 바로잡는다.

    실제로 재현된 경우다 — 진짜 앵커(TASK.SUMMARY)는 안 만들고, level 5
    슬롯 하나(blk_1)를 다른 level 5 슬롯들의 `parent_item_id`로 써서 마치
    앵커처럼 취급했다. blk_1도 결국 컨테이너 별칭에 바로 붙어 있으므로,
    셋 다 새로 만든 진짜 앵커 밑으로 평평하게 옮겨져야 한다.
    """
    catalog = TemplateCatalog.model_validate(catalog_payload())
    items = [
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
            parent_item_id="blk_1",  # blk_1을 가짜 앵커처럼 씀 — 잘못됨
            slot_id="TASK.BASIC.RESULT",
        ),
    ]

    result = _reparent_orphan_level5_items(items, catalog)

    by_id = {item.item_id: item for item in result}
    anchor_candidates = [it for it in result if it.slot_id == "TASK.SUMMARY"]
    assert len(anchor_candidates) == 1
    anchor_id = anchor_candidates[0].item_id
    assert by_id["blk_1"].parent_item_id == anchor_id
    assert by_id["blk_2"].parent_item_id == anchor_id


@pytest.mark.asyncio
async def test_new_section_wrongly_rooted_under_other_category_is_fixed(fake_dependencies):
    """새 카테고리 컨테이너가 활동이 아니라 다른 기존 카테고리 밑에 잘못
    붙어도, 코드가 활동 별칭으로 바로잡는다.

    실제로 재현된 경우다 — 한 요청에 서로 다른 두 카테고리(하나는 재사용,
    하나는 신규 생성)를 같이 처리할 때, 모델이 새 컨테이너의 `parent_ref`를
    활동 별칭이 아니라 같이 다루던 다른 기존 카테고리 블록으로 잘못 썼다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="category_1",
                    action="add",
                    parent_ref="b_1",  # 잘못됨 — exp_1 이어야 한다
                    section_kind="DETAIL",
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

    container = next(
        item for item in result["structured_items"] if item["section_kind"] == "DETAIL"
    )
    assert container["parent_ref"] == "exp_1"


def test_prune_extra_templates_keeps_only_the_one_with_real_content():
    """모델이 원문이 짧아 하위 템플릿 6종을 다 만들어도, 내용 있는 것만 남긴다."""
    items = [
        StructureLlmItem(
            item_id="blk_1",
            action="add",
            parent_ref="b_1",
            slot_id="PROBLEM_SOLVING.FEEDBACK.NEED",
            text="피드백을 받았다",
            source_item_ids=["it_1"],
        ),
        StructureLlmItem(
            item_id="empty_1",
            action="add",
            parent_ref="b_1",
            slot_id="PROBLEM_SOLVING.BASIC.PROBLEM",
        ),
        StructureLlmItem(
            item_id="empty_2",
            action="add",
            parent_ref="b_1",
            slot_id="PROBLEM_SOLVING.TROUBLESHOOTING.PROBLEM",
        ),
    ]

    pruned = _prune_extra_templates(items)

    assert [item.item_id for item in pruned] == ["blk_1"]


def test_prune_extra_templates_leaves_ambiguous_multi_content_alone():
    """두 템플릿 다 실제 내용이 있으면 애매하니 건드리지 않고 검증기에 맡긴다."""
    items = [
        StructureLlmItem(
            item_id="blk_1",
            action="add",
            parent_ref="b_1",
            slot_id="PROBLEM_SOLVING.FEEDBACK.NEED",
            text="피드백을 받았다",
            source_item_ids=["it_1"],
        ),
        StructureLlmItem(
            item_id="blk_2",
            action="add",
            parent_ref="b_1",
            slot_id="PROBLEM_SOLVING.BASIC.PROBLEM",
            text="문제가 있었다",
            source_item_ids=["it_2"],
        ),
    ]

    pruned = _prune_extra_templates(items)

    assert {item.item_id for item in pruned} == {"blk_1", "blk_2"}


@pytest.mark.asyncio
async def test_entirely_empty_new_anchor_subtree_is_rejected(fake_dependencies):
    """새 앵커 서브트리 전체가 비어 있으면(내용이 하나도 없으면) 거부한다.

    실제로 모델이 이번 입력과 무관한 **기존** 카테고리(예: 이미 내용이 있는
    "성과") 밑에도 앵커 + 빈 하위 슬롯을 통째로 만들어버린 적이 있다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                # 실제로 반영하는 내용 — 이건 정상.
                StructureLlmItem(
                    item_id="blk_1",
                    action="add",
                    parent_ref="b_1",
                    slot_id="DETAIL.MOTIVATION",
                    text="결제 오류를 해결했다",
                    source_item_ids=["it_1"],
                ),
                # 무관한 기존 카테고리(b_2)에 내용 없이 앵커+빈 슬롯만 새로 추가.
                StructureLlmItem(
                    item_id="empty_anchor",
                    action="add",
                    parent_ref="b_2",
                    slot_id="TASK.SUMMARY",
                ),
                StructureLlmItem(
                    item_id="empty_child",
                    action="add",
                    parent_item_id="empty_anchor",
                    slot_id="TASK.BASIC.PURPOSE",
                ),
            ]
        )
    )

    with pytest.raises(LlmError):
        await structure_blocks(
            make_state(alias_to_block_id={"exp_1": "101", "b_1": "305", "b_2": "306"})
        )


@pytest.mark.asyncio
async def test_entirely_empty_new_section_container_is_rejected(fake_dependencies):
    """앵커가 없는 section(`DETAIL` 등)도, 새 컨테이너 서브트리가 전부
    비어 있으면 거부한다 — 앵커가 있는 section에만 걸리면 이 경로는 빠진다.
    """
    fake_dependencies(
        StructureOutput(
            items=[
                StructureLlmItem(
                    item_id="empty_container",
                    action="add",
                    parent_ref="exp_1",
                    section_kind="DETAIL",
                ),
                StructureLlmItem(
                    item_id="empty_slot",
                    action="add",
                    parent_item_id="empty_container",
                    slot_id="DETAIL.MOTIVATION",
                ),
            ]
        )
    )

    with pytest.raises(LlmError):
        await structure_blocks(make_state())


def test_two_templates_under_one_anchor_are_rejected():
    """앵커 하나에는 문제해결 하위 템플릿 6종 중 정확히 하나만 붙어야 한다.

    실제로 모델이 원문이 짧을 때 6종 전부를 한꺼번에 만든 적이 있다 — 내용에
    맞춰 고른 게 아니라 카탈로그를 기계적으로 다 채운 것이다.
    """
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
                            "template_id": "BASIC",
                            "label": "BASIC",
                            "slots": [
                                {
                                    "slot_id": "PROBLEM_SOLVING.BASIC.PROBLEM",
                                    "level": 5,
                                    "placeholder": "문제",
                                    "example": "예시",
                                }
                            ],
                        },
                        {
                            "template_id": "FEEDBACK",
                            "label": "FEEDBACK",
                            "slots": [
                                {
                                    "slot_id": "PROBLEM_SOLVING.FEEDBACK.NEED",
                                    "level": 5,
                                    "placeholder": "필요",
                                    "example": "예시",
                                }
                            ],
                        },
                    ],
                }
            ],
        }
    )
    source = [{"item_id": "it_1", "text": "피드백을 받아 개선했다"}]
    items = [
        StructureLlmItem(
            item_id="blk_1",
            action="add",
            parent_ref="b_1",
            slot_id="PROBLEM_SOLVING.BASIC.PROBLEM",
            text=source[0]["text"],
            source_item_ids=["it_1"],
        ),
        StructureLlmItem(
            item_id="blk_2",
            action="add",
            parent_ref="b_1",
            slot_id="PROBLEM_SOLVING.FEEDBACK.NEED",
        ),
    ]

    with pytest.raises(ValueError, match="하위 템플릿을 두 개 이상"):
        _validate_output(
            items,
            source_items=source,
            catalog=catalog,
            state={"target_experience_alias": "exp_1", "alias_to_block_id": {"b_1": "305"}},
        )
