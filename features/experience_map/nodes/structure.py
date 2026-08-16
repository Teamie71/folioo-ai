"""블록 단위 구조화 노드 (에이전트 문서 5-5)."""

import logging

from common.llm import get_experience_map_llm
from features.experience_map.config import get_settings
from features.experience_map.errors import LlmError
from features.experience_map.prompts.structure import (
    render_catalog,
    render_source_items,
    structure_prompt,
)
from features.experience_map.schemas import StructuredItem, StructureOutput
from features.experience_map.state import ExperienceMapState
from features.experience_map.templates import TemplateCatalog, get_template_catalog_client

logger = logging.getLogger(__name__)


async def structure_blocks(state: ExperienceMapState) -> ExperienceMapState:
    """새 원문을 선택 활동의 블록 생성 operation으로 배정한다.

    LLM 출력은 원문 보존과 카탈로그 전체 슬롯 전개를 코드로 검증한다. 검증 실패는
    불완전한 operation을 다음 단계로 넘기지 않고 재시도 가능한 노드 오류로 만든다.

    Raises:
        LlmError: 카탈로그/LLM 조회 또는 구조화 계약 검증에 실패한 경우
    """
    updated = dict(state)
    updated["current_node"] = "structure"
    source_items = _source_items(state)
    if not source_items or not state.get("target_experience_alias"):
        updated["fallback_reason"] = "nothing_to_apply"
        return updated  # type: ignore[return-value]
    if not (state.get("activity_tree_text") or "").strip():
        raise LlmError("선택한 활동의 상세 구조를 불러오지 못했습니다.", failed_node="structure")

    try:
        catalog = await get_template_catalog_client().get_catalog()
        llm = get_experience_map_llm(timeout=get_settings().timeouts.llm)
        chain = structure_prompt | llm.with_structured_output(StructureOutput)
        result: StructureOutput = await chain.ainvoke(
            {
                "target_alias": state["target_experience_alias"],
                "activity_tree": state["activity_tree_text"],
                "catalog": render_catalog(catalog),
                "gap_instruction": _gap_instruction(state),
                "source_items": render_source_items(source_items),
            }
        )
        items = _validate_output(
            result.items, source_items=source_items, catalog=catalog, state=state
        )
    except LlmError:
        raise
    except Exception as exc:
        logger.exception("structure: 구조화 실패")
        raise LlmError("내용을 블록 구조로 정리하지 못했습니다.", failed_node="structure") from exc

    updated["structured_items"] = [item.model_dump() for item in items]
    logger.info("structure: 원문 %d개를 operation %d개로 배정", len(source_items), len(items))
    return updated  # type: ignore[return-value]


def _source_items(state: ExperienceMapState) -> list[dict]:
    """구조화가 맡을 새 내용과 new_child gap 답변을 모은다."""
    items = list(state.get("new_items", []))
    if (state.get("active_gap") or {}).get("gap_type") == "new_child_block":
        items.extend(state.get("gap_answer_items", []))
    return items


def _gap_instruction(state: ExperienceMapState) -> str:
    """new_child gap 답변의 부모를 고정하는 프롬프트 지시문."""
    active_gap = state.get("active_gap") or {}
    if active_gap.get("gap_type") != "new_child_block" or not state.get("gap_answer_items"):
        return ""

    anchor_id = active_gap.get("anchor_block_id")
    anchor_alias = next(
        (
            alias
            for alias, block_id in state.get("alias_to_block_id", {}).items()
            if block_id == anchor_id
        ),
        None,
    )
    if not anchor_alias:
        return "gap 기준 블록 별칭을 확인할 수 없습니다. 임의 배정하지 마세요.\n\n"
    return f"gap 답변 item은 반드시 기존 [{anchor_alias}] 바로 아래에 추가하세요.\n\n"


def _validate_output(
    items: list[StructuredItem],
    *,
    source_items: list[dict],
    catalog: TemplateCatalog,
    state: ExperienceMapState,
) -> list[StructuredItem]:
    """원문·slot·템플릿 전개 계약을 코드로 검증한다."""
    expected_text = {item["item_id"]: item["text"] for item in source_items}
    if len(expected_text) != len(source_items):
        raise ValueError("구조화 입력 item_id가 중복되었습니다.")

    item_ids = [item.item_id for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("구조화 결과 item_id가 중복되었습니다.")
    if any(item.action != "add" for item in items):
        raise ValueError("구조화 노드는 add operation만 만들 수 있습니다.")

    result_by_id = {item.item_id: item for item in items}
    if set(expected_text) - set(result_by_id):
        raise ValueError("구조화 결과에 원문 item이 누락되었습니다.")
    for item_id, text in expected_text.items():
        if result_by_id[item_id].text != text:
            raise ValueError("구조화 노드는 원문 text를 변경할 수 없습니다.")

    for item in items:
        if item.item_id not in expected_text and item.text is not None:
            raise ValueError("원문에 없는 text를 가진 블록을 만들 수 없습니다.")
        if item.slot_id is not None and catalog.get_slot(item.slot_id) is None:
            raise ValueError("카탈로그에 없는 slot_id입니다.")
        if item.text is None and item.slot_id is None and item.section_kind is None:
            raise ValueError("내용 없는 블록에는 slot_id 또는 section_kind가 필요합니다.")
        if item.parent_ref is not None and item.parent_ref not in state.get(
            "alias_to_block_id", {}
        ):
            raise ValueError("선택 활동에 없는 parent_ref를 사용할 수 없습니다.")
        if item.after_ref is not None and item.after_ref not in state.get("alias_to_block_id", {}):
            raise ValueError("선택 활동에 없는 after_ref를 사용할 수 없습니다.")
        if item.section_kind is not None and (item.text is not None or item.slot_id is not None):
            raise ValueError("카테고리 컨테이너에는 text나 slot_id를 둘 수 없습니다.")

    _validate_new_sections(items, catalog, state)
    _validate_template_slots(items, catalog)
    _validate_gap_parent(items, state)
    return items


def _validate_new_sections(
    items: list[StructuredItem], catalog: TemplateCatalog, state: ExperienceMapState
) -> None:
    """새 3단계 카테고리는 해당 level 4 슬롯을 모두 전개했는지 확인한다."""
    by_parent: dict[str, list[StructuredItem]] = {}
    for item in items:
        if item.parent_item_id:
            by_parent.setdefault(item.parent_item_id, []).append(item)

    sections = {section.section_id: section for section in catalog.sections}
    for item in items:
        if item.section_kind is None:
            continue
        if item.parent_ref != state.get("target_experience_alias"):
            raise ValueError("새 카테고리는 선택 활동 바로 아래에만 만들 수 있습니다.")
        section = sections.get(item.section_kind)
        if section is None:
            raise ValueError("카탈로그에 없는 section_kind입니다.")
        actual = {child.slot_id for child in by_parent.get(item.item_id, [])}
        expected = {slot.slot_id for slot in section.slots}
        if actual != expected:
            raise ValueError("새 카테고리는 해당 level 4 슬롯을 모두 생성해야 합니다.")


def _validate_template_slots(items: list[StructuredItem], catalog: TemplateCatalog) -> None:
    """사용한 level 5 템플릿은 빈 슬롯을 포함해 완전하게 전개됐는지 확인한다."""
    templates = {
        f"{section.section_id}.{template.template_id}": {slot.slot_id for slot in template.slots}
        for section in catalog.sections
        for template in section.templates
    }
    grouped: dict[tuple[str, str], list[str]] = {}
    for item in items:
        if not item.slot_id or item.slot_id.count(".") != 2:
            continue
        prefix = ".".join(item.slot_id.split(".")[:2])
        parent = item.parent_ref or item.parent_item_id or ""
        grouped.setdefault((parent, prefix), []).append(item.slot_id)

    for (_, prefix), slot_ids in grouped.items():
        if set(slot_ids) != templates.get(prefix) or len(slot_ids) != len(set(slot_ids)):
            raise ValueError("하위 템플릿은 모든 slot을 한 번씩 생성해야 합니다.")


def _validate_gap_parent(items: list[StructuredItem], state: ExperienceMapState) -> None:
    """new_child gap 답변은 anchor block 바로 아래에만 추가되게 한다."""
    active_gap = state.get("active_gap") or {}
    if active_gap.get("gap_type") != "new_child_block" or not state.get("gap_answer_items"):
        return

    anchor_id = active_gap.get("anchor_block_id")
    anchor_alias = next(
        (
            alias
            for alias, block_id in state.get("alias_to_block_id", {}).items()
            if block_id == anchor_id
        ),
        None,
    )
    source_ids = {item["item_id"] for item in state.get("gap_answer_items", [])}
    if not anchor_alias or any(
        item.item_id in source_ids and item.parent_ref != anchor_alias for item in items
    ):
        raise ValueError("new_child gap 답변은 anchor block 바로 아래에만 추가할 수 있습니다.")


def next_node(state: ExperienceMapState) -> str:
    """구조화 결과가 있으면 정제, 없으면 fallback으로 보낸다."""
    return "refine" if state.get("structured_items") else "fallback"
