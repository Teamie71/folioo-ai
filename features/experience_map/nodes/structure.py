"""블록 단위 구조화 노드 (에이전트 문서 5-5)."""

import logging
import re

from common.llm import get_experience_map_llm
from features.experience_map.config import get_settings
from features.experience_map.errors import LlmError
from features.experience_map.prompts.structure import (
    render_catalog,
    render_source_items,
    structure_prompt,
)
from features.experience_map.schemas import StructureLlmItem, StructureOutput
from features.experience_map.state import ExperienceMapState
from features.experience_map.templates import TemplateCatalog, get_template_catalog_client

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """공백 차이를 흡수한다. content_filter의 정규화와 같은 규칙을 쓴다."""
    return _WHITESPACE.sub(" ", text).strip()


def _is_concatenation(text: str, sources: list[str]) -> bool:
    """`text`가 `sources`를 순서대로 이어붙인 것인지 확인한다.

    조각 사이 공백은 0개부터 몇 개든 허용한다 — 모델이 붙여 쓰든 띄어 쓰든
    줄바꿈으로 잇든 신경 쓰지 않는다. 허용하지 않는 건 **조각 사이에 새 낱말이
    끼어드는 것**뿐이다. 정확히 공백 한 칸만 허용하면, 모델이 구두점 사이를
    안 띄우고 이어 붙인 정상적인 병합까지 거부하게 된다.
    """
    pattern = r"\s*".join(re.escape(_normalize(source)) for source in sources)
    return re.fullmatch(pattern, _normalize(text)) is not None


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
        pruned_items = _prune_extra_templates(result.items)
        filled_items = _fill_missing_template_slots(pruned_items, catalog)
        items = _validate_output(
            filled_items, source_items=source_items, catalog=catalog, state=state
        )
    except LlmError:
        raise
    except Exception as exc:
        logger.exception("structure: 구조화 실패")
        raise LlmError("내용을 블록 구조로 정리하지 못했습니다.", failed_node="structure") from exc

    updated["structured_items"] = [item.to_structured_item().model_dump() for item in items]
    logger.info(
        "structure: 원문 item %d개를 블록 %d개로 배정 (병합 %d건)",
        len(source_items),
        len(items),
        sum(1 for item in items if len(item.source_item_ids) > 1),
    )
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


def _prune_extra_templates(items: list[StructureLlmItem]) -> list[StructureLlmItem]:
    """앵커 하나에 하위 템플릿을 여러 개 만들었으면, 내용이 실린 템플릿만 남긴다.

    문제해결처럼 하위 템플릿이 여럿인 카테고리에서, 원문이 한두 문장뿐이라
    어느 템플릿이 맞는지 애매하면 모델이 "안전하게" 6종을 전부 만들어 버리는
    사고가 프롬프트를 강화해도 반복됐다. 나머지 5종은 전부 text가 없는
    placeholder뿐이므로 — 실제 내용(text)이 있는 템플릿이 **정확히 하나**면,
    그게 모델이 진짜로 고른 답이라고 보고 나머지는 코드가 조용히 버린다.
    애매해서 여러 템플릿에 내용이 걸쳐 있거나 전부 비어 있으면 그대로 두고
    이후 검증이 명확한 에러로 재시도를 유도하게 한다.
    """
    groups: dict[tuple[str, str], list[StructureLlmItem]] = {}
    for item in items:
        if not item.slot_id or item.slot_id.count(".") != 2:
            continue
        prefix = ".".join(item.slot_id.split(".")[:2])
        parent = item.parent_ref or item.parent_item_id or ""
        groups.setdefault((parent, prefix), []).append(item)

    prefixes_by_parent: dict[str, set[str]] = {}
    for parent, prefix in groups:
        prefixes_by_parent.setdefault(parent, set()).add(prefix)

    drop: set[str] = set()
    for parent, prefixes in prefixes_by_parent.items():
        if len(prefixes) <= 1:
            continue
        with_text = [
            prefix
            for prefix in prefixes
            if any(item.text is not None for item in groups[(parent, prefix)])
        ]
        if len(with_text) != 1:
            continue  # 애매하다 — 그대로 두고 검증기가 에러로 보고하게 한다.
        for prefix in prefixes:
            if prefix != with_text[0]:
                drop.update(item.item_id for item in groups[(parent, prefix)])

    if not drop:
        return items
    return [item for item in items if item.item_id not in drop]


def _fill_missing_template_slots(
    items: list[StructureLlmItem], catalog: TemplateCatalog
) -> list[StructureLlmItem]:
    """모델이 빠뜨린 level 5 빈 슬롯을 코드가 직접 채워 넣는다.

    명세는 "선택한 하위 템플릿의 slot을 빈 슬롯까지 전부 전개"하라고 못박지만,
    원문이 짧을 때 모델이 정보 있는 slot 하나만 만들고 나머지 null placeholder를
    누락하는 사고가 반복됐다. 프롬프트 문구를 아무리 강화해도 매 요청마다
    비결정적으로 재발해서, "어떤 하위 템플릿을 어느 부모 아래 만들었는지"가
    이미 출력에 다 드러난 이상 나머지 slot_id는 코드가 결정론적으로 채운다 —
    모델의 확률적 성실성에 기대지 않는다.
    """
    templates = {
        f"{section.section_id}.{template.template_id}": [slot.slot_id for slot in template.slots]
        for section in catalog.sections
        for template in section.templates
    }

    groups: dict[tuple[str, str], list[StructureLlmItem]] = {}
    for item in items:
        if not item.slot_id or item.slot_id.count(".") != 2:
            continue
        prefix = ".".join(item.slot_id.split(".")[:2])
        parent = item.parent_ref or item.parent_item_id or ""
        groups.setdefault((parent, prefix), []).append(item)

    filled = list(items)
    counter = 0
    for (parent, prefix), group_items in groups.items():
        expected = templates.get(prefix)
        if expected is None:
            continue  # 카탈로그에 없는 템플릿이면 이후 검증이 에러로 보고한다.
        present = {item.slot_id for item in group_items}
        missing = [slot_id for slot_id in expected if slot_id not in present]
        if not missing:
            continue
        anchor = group_items[0]
        for slot_id in missing:
            counter += 1
            filled.append(
                StructureLlmItem(
                    item_id=f"auto_{prefix}_{counter}",
                    action="add",
                    slot_id=slot_id,
                    text=None,
                    source_item_ids=[],
                    parent_ref=anchor.parent_ref,
                    parent_item_id=anchor.parent_item_id,
                    after_ref=None,
                )
            )
    return filled


def _validate_output(
    items: list[StructureLlmItem],
    *,
    source_items: list[dict],
    catalog: TemplateCatalog,
    state: ExperienceMapState,
) -> list[StructureLlmItem]:
    """원문·slot·템플릿 전개 계약을 코드로 검증한다.

    content_filter는 문장·불릿 단위로 자르고 템플릿은 주제 단위 슬롯을
    요구하므로, 원문 item과 출력 블록은 더 이상 1:1이 아니다. **모든 입력
    item_id가 정확히 하나의 출력 블록에서만, 순서를 유지한 채 쓰이는지**를
    검증한다 — 자기 own item_id로 결과를 인덱싱하던 예전 검증은 이 관계를
    표현할 수 없었다.
    """
    expected_text = {item["item_id"]: item["text"] for item in source_items}
    if len(expected_text) != len(source_items):
        raise ValueError("구조화 입력 item_id가 중복되었습니다.")

    item_ids = [item.item_id for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("구조화 결과 item_id가 중복되었습니다.")
    if any(item.action != "add" for item in items):
        raise ValueError("구조화 노드는 add operation만 만들 수 있습니다.")

    _validate_source_coverage(items, expected_text)

    for item in items:
        if not item.source_item_ids and item.text is not None:
            raise ValueError("source_item_ids 없이 text를 만들 수 없습니다.")
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

    # 순서가 중요하다. level 5를 카테고리 컨테이너에 잘못 붙이면 새 카테고리의
    # level 4 슬롯 개수 검사도 같이 걸리는데, 그 일반적인 메시지보다 "앵커
    # 아래에 붙여야 한다" 는 구체적인 원인을 먼저 보여준다.
    _validate_template_slots(items, catalog)
    _validate_new_sections(items, catalog, state)
    _validate_non_empty_anchors(items, catalog)
    _validate_gap_parent(items, state)
    return items


def _validate_non_empty_anchors(items: list[StructureLlmItem], catalog: TemplateCatalog) -> None:
    """새로 만드는 앵커(level 4) 서브트리에는 실제 내용이 하나는 있어야 한다.

    실제로 모델이 관련 없는 **기존** 카테고리(예: 이미 내용이 있는 "성과")
    밑에도 새 앵커 + 하위 템플릿을 통째로 만들면서, 그 서브트리 전체를
    text가 전부 null인 빈 슬롯으로만 채운 적이 있다. "슬롯을 빠짐없이
    전개하라"는 규칙을 엉뚱한 곳까지 적용한 것이다. 빈 슬롯 자체는 정상
    (템플릿의 일부 slot만 채워질 수 있다) 이지만, **앵커 서브트리 전체가
    처음부터 끝까지 비어 있으면** 그건 애초에 만들지 말았어야 할 카테고리다.
    """
    by_parent: dict[str, list[StructureLlmItem]] = {}
    for item in items:
        if item.parent_item_id:
            by_parent.setdefault(item.parent_item_id, []).append(item)

    def subtree_has_text(item: StructureLlmItem) -> bool:
        if item.text is not None:
            return True
        return any(subtree_has_text(child) for child in by_parent.get(item.item_id, []))

    for item in items:
        if _is_anchor_slot(item.slot_id, catalog) and not subtree_has_text(item):
            raise ValueError(
                f"[{item.item_id}] 앵커와 그 아래 슬롯 전체에 실제 내용이 없습니다. "
                "내용 없는 카테고리·템플릿을 통째로 새로 만들지 마세요."
            )


def _validate_source_coverage(items: list[StructureLlmItem], expected_text: dict[str, str]) -> None:
    """모든 원문 item이 정확히 한 블록에서, 이어붙인 그대로 쓰였는지 확인한다."""
    seen: set[str] = set()
    for item in items:
        if not item.source_item_ids:
            continue
        for source_id in item.source_item_ids:
            if source_id not in expected_text:
                raise ValueError(f"존재하지 않는 원문 item_id를 참조했습니다: {source_id}")
            if source_id in seen:
                raise ValueError(f"원문 item이 두 블록 이상에서 쓰였습니다: {source_id}")
            seen.add(source_id)

        if not _is_concatenation(
            item.text or "", [expected_text[sid] for sid in item.source_item_ids]
        ):
            raise ValueError(
                "구조화 노드는 원문 text를 변경할 수 없습니다. "
                "합칠 때도 이어붙인 것과 정확히 같아야 합니다."
            )

    missing = set(expected_text) - seen
    if missing:
        raise ValueError(f"구조화 결과에 원문 item이 누락되었습니다: {sorted(missing)}")


def _validate_new_sections(
    items: list[StructureLlmItem], catalog: TemplateCatalog, state: ExperienceMapState
) -> None:
    """새 3단계 카테고리는 해당 level 4 슬롯을 모두, 딱 한 번만 전개했는지 확인한다.

    같은 `section_kind`를 두 번 만드는 것도 막는다. 실제로 모델이 문제해결
    템플릿 6종을 하나만 고르지 않고, 6개의 `PROBLEM_SOLVING` 카테고리 컨테이너를
    중첩해서 만든 적이 있다 — 명세는 카테고리를 하나만 만들고 그 아래 템플릿을
    하나 골라 채우라고 하는데, 프롬프트가 "정확히 하나" 라는 걸 강조하지
    않아서였다.
    """
    by_parent: dict[str, list[StructureLlmItem]] = {}
    for item in items:
        if item.parent_item_id:
            by_parent.setdefault(item.parent_item_id, []).append(item)

    sections = {section.section_id: section for section in catalog.sections}
    seen_kinds: set[str] = set()
    for item in items:
        if item.section_kind is None:
            continue
        if item.section_kind in seen_kinds:
            raise ValueError(f"같은 카테고리를 두 번 만들 수 없습니다: {item.section_kind}")
        seen_kinds.add(item.section_kind)
        if item.parent_ref != state.get("target_experience_alias"):
            raise ValueError("새 카테고리는 선택 활동 바로 아래에만 만들 수 있습니다.")
        section = sections.get(item.section_kind)
        if section is None:
            raise ValueError("카탈로그에 없는 section_kind입니다.")
        actual = {child.slot_id for child in by_parent.get(item.item_id, [])}
        expected = {slot.slot_id for slot in section.slots}
        if actual != expected:
            raise ValueError("새 카테고리는 해당 level 4 슬롯을 모두 생성해야 합니다.")


def _validate_template_slots(items: list[StructureLlmItem], catalog: TemplateCatalog) -> None:
    """사용한 level 5 템플릿은 빈 슬롯을 포함해 완전하게 전개됐는지,
    그리고 그 부모가 실제 앵커(level 4, is_anchor) 블록인지 확인한다.

    실제로 모델이 level 5 슬롯들을 앵커가 아니라 **카테고리 컨테이너**(level 3)
    바로 아래에 붙인 적이 있다. 명세 3-0은 "level 5는 반드시 앵커 슬롯 아래에
    붙는다" 고 못박는데, 프롬프트가 그 연결 대상을 명시하지 않아 모델이
    카테고리 컨테이너를 앵커로 착각했다.
    """
    templates = {
        f"{section.section_id}.{template.template_id}": {slot.slot_id for slot in template.slots}
        for section in catalog.sections
        for template in section.templates
    }
    anchor_item_ids = {item.item_id for item in items if _is_anchor_slot(item.slot_id, catalog)}

    grouped: dict[tuple[str, str], list[str]] = {}
    for item in items:
        if not item.slot_id or item.slot_id.count(".") != 2:
            continue
        prefix = ".".join(item.slot_id.split(".")[:2])
        parent = item.parent_ref or item.parent_item_id or ""
        grouped.setdefault((parent, prefix), []).append(item.slot_id)

        # parent_ref(기존 블록)는 그게 앵커인지 알 방법이 없어 여기서는 넘어간다.
        # 새로 만든 parent_item_id만, 방금 만든 앵커를 가리키는지 확인한다.
        if item.parent_item_id and item.parent_item_id not in anchor_item_ids:
            raise ValueError(
                "level 5 슬롯은 카테고리 컨테이너가 아니라 그 앵커(level 4) 블록 "
                "아래에 만들어야 합니다."
            )

    for (_, prefix), slot_ids in grouped.items():
        expected = templates.get(prefix)
        if expected is None:
            raise ValueError(f"카탈로그에 없는 하위 템플릿입니다: {prefix}")
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError(f"같은 slot을 두 번 이상 만들었습니다: {prefix}")
        invented = set(slot_ids) - expected
        if invented:
            # 실제로 이런 경우가 있었다 — 모델이 TROUBLESHOOTING 템플릿을 쓰다가
            # BASIC 템플릿의 RESULT 를 끼워 넣었다. "모두 생성해야 한다" 는
            # 뭉뚱그린 메시지 대신, 정확히 어떤 slot이 잘못됐는지 짚는다.
            raise ValueError(f"{prefix} 템플릿에 없는 slot을 만들었습니다: {sorted(invented)}")
        missing = expected - set(slot_ids)
        if missing:
            raise ValueError(f"{prefix} 템플릿의 slot이 빠졌습니다: {sorted(missing)}")

    prefixes_by_parent: dict[str, set[str]] = {}
    for parent, prefix in grouped:
        prefixes_by_parent.setdefault(parent, set()).add(prefix)
    for parent, prefixes in prefixes_by_parent.items():
        if len(prefixes) > 1:
            # 앵커 하나에는 하위 템플릿 중 내용에 맞는 딱 하나만 붙는다. 여러
            # 템플릿을 한꺼번에 만들면 카탈로그 전체를 기계적으로 채운 것이지,
            # 실제 내용에 맞춰 고른 게 아니다.
            raise ValueError(
                f"앵커 [{parent}] 아래에 하위 템플릿을 두 개 이상 만들었습니다: "
                f"{sorted(prefixes)}. 내용에 맞는 템플릿 하나만 고르세요."
            )


def _is_anchor_slot(slot_id: str | None, catalog: TemplateCatalog) -> bool:
    """slot_id가 카탈로그에서 앵커(level 4, is_anchor)인지 확인한다."""
    if slot_id is None:
        return False
    slot = catalog.get_slot(slot_id)
    return slot is not None and slot.is_anchor


def _validate_gap_parent(items: list[StructureLlmItem], state: ExperienceMapState) -> None:
    """new_child gap 답변은 anchor block 바로 아래에만 추가되게 한다.

    gap 답변 item이 다른 원문과 합쳐질 수 있으므로, item_id로 직접 찾지 않고
    **어느 블록이 gap 답변을 source_item_ids에 포함했는지**로 찾는다.
    """
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
    offending = any(
        set(item.source_item_ids) & source_ids and item.parent_ref != anchor_alias for item in items
    )
    if not anchor_alias or offending:
        raise ValueError("new_child gap 답변은 anchor block 바로 아래에만 추가할 수 있습니다.")


def next_node(state: ExperienceMapState) -> str:
    """구조화 결과가 있으면 정제, 없으면 fallback으로 보낸다."""
    return "refine" if state.get("structured_items") else "fallback"
