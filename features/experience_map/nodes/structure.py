"""블록 단위 구조화 노드 (에이전트 문서 5-5)."""

import logging
import re

from common.llm import get_experience_map_llm
from features.experience_map.config import MAX_SOURCE_ITEMS_PER_STRUCTURE_BATCH, get_settings
from features.experience_map.errors import LlmError
from features.experience_map.prompts.structure import (
    render_catalog,
    render_previous_batch_note,
    render_source_items,
    structure_prompt,
)
from features.experience_map.schemas import (
    ExistingCategoryClassification,
    StructureLlmItem,
    StructureOutput,
)
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
        # 재시도 전용 체인은 temperature를 살짝 올린다. temperature 0에서는
        # 같은 프롬프트에 같은 실수를 그대로 반복하는 게 실제로 재현됐다 —
        # 지시문을 더 붙여도(`_missing_items_repair_instruction` 등) 모델이
        # 같은 패턴을 고수했다. 재시도는 애초에 "1차와는 다른 결과"를
        # 바라는 것이므로, 결정론을 깨는 편이 목적에 맞는다.
        retry_llm = get_experience_map_llm(timeout=get_settings().timeouts.llm, temperature=0.4)
        retry_chain = structure_prompt | retry_llm.with_structured_output(StructureOutput)
        base_instruction = _gap_instruction(state)
        base_prompt_vars = {
            "target_alias": state["target_experience_alias"],
            "activity_tree": state["activity_tree_text"],
            "catalog": render_catalog(catalog),
        }

        # 원문이 많으면(실제로 파일 업로드에서 15개 넘는 경우가 흔하다) 한
        # 번에 다 맡길수록 모델이 일부를 빠뜨리거나 잘못 연결하는 사고가
        # 눈에 띄게 잦아진다. 작은 배치로 나눠 순차 처리하면 배치당 실패율이
        # 크게 낮아진다 — 뒤 배치는 앞 배치가 이미 만든 카테고리·앵커를
        # `previous_batch_note`로 안내받아 재사용할 수 있다.
        items: list[StructureLlmItem] = []
        existing_categories: list[ExistingCategoryClassification] = []
        cumulative_source_text: dict[str, str] = {}
        call_index = 0
        batches = [
            source_items[index : index + MAX_SOURCE_ITEMS_PER_STRUCTURE_BATCH]
            for index in range(0, len(source_items), MAX_SOURCE_ITEMS_PER_STRUCTURE_BATCH)
        ]
        for batch_index, batch in enumerate(batches):
            batch_source_text = {item["item_id"]: item["text"] for item in batch}
            cumulative_source_text.update(batch_source_text)
            # 배치 하나당 최대 2번 시도한다(그래프 RetryPolicy의 "노드마다
            # 1회"와는 별개로, 이 노드 실행 하나 안에서 완결된다). 모델이
            # 원문 일부를 통째로 빠뜨리는 사고가 실제로 있었는데, 같은
            # 프롬프트를 그대로 다시 보내도 결과가 매번 조금씩 달라
            # 재시도만으로 고쳐지는 경우가 많았다 — 그렇다면 빠진 항목을
            # 콕 집어 다시 요청하는 편이 통째로 재시도하는 것보다 낫다.
            #
            # 재시도는 **배치 전체를 다시 시키지 않는다** — 처음엔 그렇게
            # 했는데, 이미 잘 배정한 부분까지 모델이 처음부터 다시 만들면서
            # 새로운 실수(같은 슬롯을 두 번 만드는 등)를 또 냈다. 대신
            # 1차에서 성공한 부분은 `previous_batch_note`로 "이미 만든 것"
            # 취급해 그대로 두고, 빠진 item만 새 source_items로 좁혀서
            # 다시 맡긴다 — 모델이 풀어야 할 문제 자체를 작게 줄인다.
            round_note_items = items
            round_source_items = batch
            instruction = base_instruction
            batch_items: list[StructureLlmItem] = []
            for attempt in range(2):
                prompt_vars = {
                    **base_prompt_vars,
                    "previous_batch_note": render_previous_batch_note(
                        _render_previous_batch_lines(round_note_items, catalog)
                    ),
                    "source_items": render_source_items(round_source_items),
                }
                active_chain = chain if attempt == 0 else retry_chain
                try:
                    result: StructureOutput = await active_chain.ainvoke(
                        {**prompt_vars, "gap_instruction": instruction}
                    )
                except Exception:
                    # LLM이 스키마 자체를 어기는 item을 낼 때가 있다(예: parent_ref도
                    # parent_item_id도 없는 item) — pydantic이 파싱 단계에서 바로
                    # 거부해 결과를 볼 기회조차 없다. 이걸 여기서 안 잡으면 배치
                    # 하나의 일시적 실수가 이미 끝낸 앞 배치들까지 통째로 날린다.
                    #
                    # 같은 프롬프트를 그대로 다시 보내면 온도 0에서는 같은 실수를
                    # 그대로 반복한다 — 실제로 재현됐다. 무엇이 잘못됐는지 구체적인
                    # 지시문을 덧붙여 입력 자체를 바꿔야 재시도가 의미 있다.
                    if attempt == 1:
                        raise
                    logger.warning(
                        "structure: 배치 %d/%d 응답 파싱 실패, 같은 배치 재시도",
                        batch_index + 1,
                        len(batches),
                        exc_info=True,
                    )
                    instruction = base_instruction + (
                        "**주의: 이전 시도에서 일부 item에 parent_ref와 parent_item_id가 "
                        "둘 다 없었습니다.** 모든 item은 둘 중 하나를 반드시 가져야 "
                        "합니다 — 새 카테고리 컨테이너는 parent_ref(활동 별칭)만, "
                        "그 아래 앵커·하위 슬롯은 parent_item_id(방금 만든 item_id)만 "
                        "씁니다.\n\n"
                    )
                    continue
                # 이번 호출이 새로 만든 item_id가 이전 호출(다른 배치, 또는
                # 같은 배치의 이전 시도)과 겹칠 수 있다 — 각 호출은 서로의
                # 출력을 모르는 채 독립적으로 "blk_1" 같은 이름을 짓는다.
                # 병합할 게 있는(call_index > 0) 모든 호출에 접두사를 붙여
                # 겹치지 않게 한다. 첫 호출(call_index == 0)은 병합 대상이
                # 없으니 그대로 둔다 — item_id가 굳이 안 바뀌는 편이 낫다.
                namespaced = (
                    _namespace_batch_item_ids(result.items, call_index)
                    if call_index > 0
                    else result.items
                )
                call_index += 1
                candidate = _apply_structuring_fixups(
                    round_note_items + namespaced, cumulative_source_text, state, catalog
                )
                existing_categories = result.existing_categories
                missing = _missing_source_ids(candidate, batch_source_text)
                batch_items = candidate
                if not missing:
                    break
                if attempt == 0:
                    logger.warning(
                        "structure: 배치 %d/%d 원문 item 누락 감지, 빠진 것만 좁혀서 재시도 (누락 %d개)",
                        batch_index + 1,
                        len(batches),
                        len(missing),
                    )
                    round_note_items = candidate
                    round_source_items = [item for item in batch if item["item_id"] in missing]
                    instruction = base_instruction + _missing_items_repair_instruction(
                        missing, batch
                    )
            items = batch_items

        # 배치를 다 처리한 뒤 딱 한 번만 빈 슬롯을 채운다 — 배치마다 채우면
        # 뒤 배치가 실제로 채우려는 slot을 앞 배치가 먼저 빈 슬롯으로 선점해
        # 같은 slot이 두 번 생긴다.
        filled_items = _fill_missing_template_slots(items, catalog, state)
        validated = _validate_output(
            filled_items,
            source_items=source_items,
            catalog=catalog,
            state=state,
            existing_categories=existing_categories,
        )
    except LlmError:
        raise
    except Exception as exc:
        logger.exception("structure: 구조화 실패")
        raise LlmError("내용을 블록 구조로 정리하지 못했습니다.", failed_node="structure") from exc

    updated["structured_items"] = [item.to_structured_item().model_dump() for item in validated]
    logger.info(
        "structure: 원문 item %d개를 블록 %d개로 배정 (병합 %d건)",
        len(source_items),
        len(validated),
        sum(1 for item in validated if len(item.source_item_ids) > 1),
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


def _merge_duplicate_slot_items(items: list[StructureLlmItem]) -> list[StructureLlmItem]:
    """같은 부모 아래 같은 slot_id를 두 번 이상 만들면 하나로 합친다.

    실제로 모델이 같은 template slot(예: TASK.BASIC.PURPOSE)을 같은 부모
    아래 두 item으로 쪼개 만든 적이 있다 — 검증(`_validate_template_slots`)이
    "같은 slot을 두 번 이상 만들었습니다"로 거부하는데, 재시도해도 같은
    실수가 반복됐다. 어느 쪽을 버릴지는 모호하다(둘 다 서로 다른 원문을
    담고 있을 수 있다) — 그래서 버리지 않고 `source_item_ids`를 합쳐
    첫 item 하나로 만든다. 이미 있는 "여러 원문이 한 슬롯에 합쳐질 수
    있다"는 규칙의 연장이다.
    """
    order: list[tuple[str, str]] = []
    groups: dict[tuple[str, str], list[StructureLlmItem]] = {}
    for item in items:
        if not item.slot_id:
            continue
        key = (item.parent_ref or item.parent_item_id or "", item.slot_id)
        if key not in groups:
            order.append(key)
        groups.setdefault(key, []).append(item)

    merge_into: dict[str, list[str]] = {}
    drop: set[str] = set()
    for key in order:
        group = groups[key]
        if len(group) <= 1:
            continue
        keep_id = group[0].item_id
        combined: list[str] = []
        for member in group:
            combined.extend(member.source_item_ids)
            if member.item_id != keep_id:
                drop.add(member.item_id)
        merge_into[keep_id] = combined

    if not drop:
        return items
    result: list[StructureLlmItem] = []
    for item in items:
        if item.item_id in drop:
            continue
        if item.item_id in merge_into:
            result.append(item.model_copy(update={"source_item_ids": merge_into[item.item_id]}))
        else:
            result.append(item)
    return result


def _drop_empty_invalid_slot_items(
    items: list[StructureLlmItem], catalog: TemplateCatalog
) -> list[StructureLlmItem]:
    """내용도 없고 카탈로그에도 없는 slot_id를 단 item은 통째로 버린다.

    실제로 재현된 경우다. 모델이 실제 슬롯을 채우는 item과는 별개로,
    text도 source_item_ids도 없는 빈 item에 slot_id로 실제 슬롯이 아니라
    템플릿 id 자체(예: `TASK.BASIC.PURPOSE`가 아니라 `TASK.BASIC`)를 적어
    넣은 적이 있다. 내용이 전혀 없으니 버려도 잃을 정보가 없고, 카탈로그에
    없는 slot_id를 그대로 두면 이후 검증만 막힌다 — 다른 item이 이 item을
    `parent_item_id`로 참조하고 있지 않을 때만 안전하게 버린다.
    """
    referenced_as_parent = {item.parent_item_id for item in items if item.parent_item_id}
    return [
        item
        for item in items
        if not (
            item.slot_id is not None
            and catalog.get_slot(item.slot_id) is None
            and item.text is None
            and not item.source_item_ids
            and item.item_id not in referenced_as_parent
        )
    ]


def _apply_structuring_fixups(
    raw_items: list[StructureLlmItem],
    source_text: dict[str, str],
    state: ExperienceMapState,
    catalog: TemplateCatalog,
) -> list[StructureLlmItem]:
    """LLM 원시 출력에 결정론적 보정을 순서대로 적용한다.

    순서가 중요하다. 프루닝은 "같은 부모 밑 여러 템플릿 중 내용 있는 것만
    남긴다"는 판단을 parent_item_id 기준으로 한다. 앵커를 건너뛴 level 5가
    서로 다른 가짜 부모(컨테이너 별칭 vs 가짜 앵커 형제)를 쓰고 있으면,
    프루닝이 이걸 먼저 하면 "같은 부모"로 안 보여 둘 다 살아남는다 — 앵커
    연결부터 정리해야 프루닝이 제대로 본다. 배치 안에서 방금 만든 item을
    parent_ref로 잘못 가리키는 것부터 parent_item_id로 바로잡아야, 그다음
    앵커 연결 정리가 진짜 부모 체인을 볼 수 있다.

    **빈 슬롯 채우기(`_fill_missing_template_slots`)는 여기 없다.** 여러
    배치로 나눠 처리할 때 배치마다 이걸 하면, 뒤 배치가 아직 안 채운 slot을
    앞 배치가 먼저 "빈 슬롯"으로 채워 넣어 버린다 — 뒤 배치가 그 slot에 실제
    내용을 채우면 같은 slot이 두 번 생겨 거부된다. 모든 배치를 다 처리한
    뒤 한 번만 불러야 한다.

    같은 (부모, slot_id) 중복을 텍스트 재조립보다 먼저 병합하는 이유도
    같다 — 병합된 item의 `source_item_ids`가 재조립 단계에 그대로
    들어가야 최종 text가 원문을 빠짐없이 담는다.
    """
    merged = _merge_duplicate_slot_items(raw_items)
    pruned_junk = _drop_empty_invalid_slot_items(merged, catalog)
    reconstructed = _reconstruct_verbatim_text(pruned_junk, source_text)
    rerefed = _fix_batch_local_parent_ref(reconstructed, state)
    dereffed = _clear_invalid_after_ref(rerefed, state)
    rerooted = _fix_new_section_parent(dereffed, state)
    reparented = _reparent_orphan_level5_items(rerooted, catalog)
    # `_reuse_existing_filled_anchor`는 반드시 `_reparent_orphan_level5_items`
    # 뒤에 와야 한다. 앞서 두면, 이게 앵커를 level 5로 바꿔 기존 앵커에
    # `parent_ref`로 직접 붙인 결과를 `_reparent_orphan_level5_items`가
    # "앵커를 건너뛴 level 5"로 오해해 가짜 앵커를 또 만들어 버린다 —
    # `parent_ref`가 실제로 앵커를 가리키는지는 그 함수가 알 방법이 없다.
    reused = _reuse_existing_filled_anchor(reparented, catalog, state)
    deduped = _dedupe_anchor_matching_child_source(reused, catalog)
    return _prune_extra_templates(deduped)


def _missing_source_ids(items: list[StructureLlmItem], source_text: dict[str, str]) -> set[str]:
    """어느 블록에도 배정되지 않은 원문 item_id를 찾는다.

    `_validate_source_coverage`와 달리 중복 배정은 신경 쓰지 않는다 — 중복은
    이미 결정론적 보정으로 처리되므로, 여기서는 재시도로 이어질 "완전히
    빠뜨린" 경우만 미리 알아내 재시도 프롬프트에 콕 집어 넣는다.
    """
    used = {sid for item in items for sid in item.source_item_ids}
    return set(source_text) - used


def _missing_items_repair_instruction(missing: set[str], source_items: list[dict]) -> str:
    """빠뜨린 원문 item을 다시 배정하라는 지시문을 만든다."""
    lines = "\n".join(
        f"- [{item['item_id']}] {item['text']}"
        for item in source_items
        if item["item_id"] in missing
    )
    return (
        "**주의: 이전 시도에서 다음 원문 item을 어느 블록에도 배정하지 않고 "
        f"빠뜨렸습니다. 이번에는 반드시 포함해 배정하세요:**\n{lines}\n\n"
    )


def _render_previous_batch_lines(
    items: list[StructureLlmItem], catalog: TemplateCatalog
) -> list[str]:
    """앞 배치가 이번 요청 안에서 이미 만든 카테고리·앵커·슬롯을 프롬프트용 목록으로 만든다."""
    lines: list[str] = []
    for item in items:
        if item.section_kind is not None:
            lines.append(f"- [{item.item_id}] 카테고리 컨테이너 (section_kind={item.section_kind})")
        elif item.slot_id is not None:
            anchor = " · 앵커" if _is_anchor_slot(item.slot_id, catalog) else ""
            content = f': "{item.text}"' if item.text else " (빈 슬롯)"
            lines.append(f"- [{item.item_id}] {item.slot_id}{anchor}{content}")
    return lines


def _namespace_batch_item_ids(
    raw_items: list[StructureLlmItem], batch_index: int
) -> list[StructureLlmItem]:
    """이번 배치가 새로 만든 item_id에 접두사를 붙여 다른 배치와 안 겹치게 한다.

    각 배치는 서로의 출력을 모르는 채 독립적으로 item_id를 짓는다 — 실제로
    두 배치가 똑같이 "blk_1"을 써서 병합 시 `item_id가 중복되었습니다`로
    거부된 적이 있다. `parent_item_id`가 이번 배치 안의 item을 가리키면
    같이 바꾸고, 앞 배치의 item(예: `previous_batch_note`로 안내받아
    재사용하는 앵커)을 가리키면 이미 고유하므로 그대로 둔다.

    `parent_ref`도 같은 이유로 살핀다 — 배치 안에서 방금 만든 item을
    `parent_ref`로 잘못 가리키는 사고(`_fix_batch_local_parent_ref`가 뒤에서
    바로잡는 패턴)가 여기서 먼저 처리되지 않으면, 그 검증이 찾는 값이 이미
    접두사가 붙어 바뀐 뒤라 못 알아본다.
    """
    prefix = f"batch{batch_index}_"
    own_ids = {item.item_id for item in raw_items}

    def remap(value: str | None) -> str | None:
        return f"{prefix}{value}" if value is not None and value in own_ids else value

    return [
        item.model_copy(
            update={
                "item_id": f"{prefix}{item.item_id}",
                "parent_ref": remap(item.parent_ref),
                "parent_item_id": remap(item.parent_item_id),
            }
        )
        for item in raw_items
    ]


def _clear_invalid_after_ref(
    items: list[StructureLlmItem], state: ExperienceMapState
) -> list[StructureLlmItem]:
    """존재하지 않는 별칭을 가리키는 `after_ref`는 비운다.

    `after_ref`는 프롬프트가 명시하듯 **기존에 있던** 형제 블록에만 쓸 수
    있다 — 방금 만든 블록끼리의 순서는 `items` 배열 순서를 따르므로,
    `after_ref`가 없어도 순서 정보를 잃지 않는다. 실제로 모델이 존재하지
    않는 별칭(다른 배치의 item_id 등)을 `after_ref`에 써서 거부된 적이
    있다 — 순서는 이미 배열 순서로 확보되니, 잘못된 참조는 거부 대신
    코드가 비운다.
    """
    known_aliases = state.get("alias_to_block_id", {})
    return [
        item.model_copy(update={"after_ref": None})
        if item.after_ref is not None and item.after_ref not in known_aliases
        else item
        for item in items
    ]


def _reconstruct_verbatim_text(
    items: list[StructureLlmItem], source_text: dict[str, str]
) -> list[StructureLlmItem]:
    """블록 text를 LLM이 다시 타이핑한 것 대신, 원문을 코드가 직접 이어붙여 만든다.

    LLM이 실제로 결정해야 하는 건 "어느 원문 item을 어느 슬롯에 배정할지"뿐이다
    — 그런데 그 배정된 원문을 `text`로 다시 타이핑하게 시키면, 살짝 요약하거나
    윤문해 버리는 실수가 반복됐다(원문 보존 검증 실패의 최대 원인이었다).
    `source_item_ids`로 어느 원문인지는 이미 확정됐으니, 실제 커밋되는 text는
    LLM이 쓴 걸 검증하는 대신 코드가 원문 그대로 이어붙여 확정한다 — 원문 보존을
    "검증해서 걸러내는" 대신 애초에 어길 수 없게 만든다.

    `source_item_ids`가 가리키는 원문 id가 존재하지 않거나 중복 사용되는 경우는
    건드리지 않고 그대로 둔다 — 그건 별도 계약 위반이라 이후 검증이 원래
    메시지로 잡아야 사용자가 원인을 정확히 알 수 있다.
    """
    rebuilt: list[StructureLlmItem] = []
    for item in items:
        if not item.source_item_ids or any(sid not in source_text for sid in item.source_item_ids):
            rebuilt.append(item)
            continue
        text = " ".join(source_text[sid].strip() for sid in item.source_item_ids)
        rebuilt.append(item.model_copy(update={"text": text}))
    return rebuilt


def _fix_batch_local_parent_ref(
    items: list[StructureLlmItem], state: ExperienceMapState
) -> list[StructureLlmItem]:
    """방금 만든 item을 `parent_ref`로 가리키면 `parent_item_id`로 바로잡는다.

    `parent_ref`는 활동 트리에 이미 있는 블록 별칭(`b_1` 등)에만 써야 하고,
    같은 요청에서 방금 만든 블록은 `parent_item_id`로 가리켜야 한다 — 실제로
    모델이 이 둘을 헷갈려서, 방금 만든 앵커(`blk_3`)를 `parent_item_id`가
    아니라 `parent_ref="blk_3"`으로 가리킨 적이 있다. `blk_3`은 활동 트리의
    별칭일 수 없으므로(그런 별칭은 서버가 절대 `b_`가 아닌 이런 이름으로
    안 준다), 방금 만든 item_id를 가리키려던 의도가 명백하다 — 검증해서
    거부하는 대신 코드가 바로 `parent_item_id`로 고쳐 끼운다.
    """
    known_aliases = state.get("alias_to_block_id", {})
    item_ids = {item.item_id for item in items}
    return [
        item.model_copy(update={"parent_ref": None, "parent_item_id": item.parent_ref})
        if item.parent_ref is not None
        and item.parent_ref not in known_aliases
        and item.parent_ref in item_ids
        else item
        for item in items
    ]


def _fix_new_section_parent(
    items: list[StructureLlmItem], state: ExperienceMapState
) -> list[StructureLlmItem]:
    """새 카테고리 컨테이너는 항상 선택 활동 바로 아래에만 만들 수 있다.

    실제로 한 요청에 서로 다른 두 카테고리(하나는 기존 재사용, 하나는 신규
    생성)를 같이 처리할 때, 모델이 새 컨테이너의 `parent_ref`를 활동 별칭이
    아니라 **같은 요청에서 다루던 다른 기존 카테고리 블록**으로 잘못 쓰는
    사고가 있었다. 이 규칙은 항상 참이라 모호함이 없으므로, 검증해서
    거부하는 대신 코드가 바로 활동 별칭으로 고쳐 끼운다.
    """
    target_alias = state.get("target_experience_alias")
    if not target_alias:
        return items
    return [
        item.model_copy(update={"parent_ref": target_alias})
        if item.section_kind is not None and item.parent_ref != target_alias
        else item
        for item in items
    ]


def _reparent_orphan_level5_items(
    items: list[StructureLlmItem], catalog: TemplateCatalog
) -> list[StructureLlmItem]:
    """level 5 슬롯이 앵커를 안 거치고 컨테이너에 닿으면 코드가 바로잡는다.

    "기존 카테고리를 재사용하라"는 지시를 따르는 모델이 앵커를 건너뛰는
    사고가 프롬프트를 몇 번 강화해도 계속 재발했다. 두 가지 모양으로
    나타난다: (1) level 5가 컨테이너 별칭에 형제로 직접 붙거나, (2) 형제
    level 5 중 하나를 마치 앵커인 것처럼 `parent_item_id`로 가리킨다 — 그
    "가짜 앵커"도 결국 컨테이너 별칭에 직접 붙어 있다. 두 경우 다 각 item의
    부모 체인을 위로 따라가며 **진짜 앵커를 거치지 않고** 컨테이너 별칭에
    닿는지로 판별한다. 같은 컨테이너·section에 이미 진짜 앵커가 있으면
    거기로 연결하고, 없으면 코드가 빈 앵커를 새로 만들어 끼워 넣는다.
    """
    anchor_slot_by_section: dict[str, str] = {
        section.section_id: slot.slot_id
        for section in catalog.sections
        for slot in section.slots
        if slot.is_anchor
    }
    by_id = {item.item_id: item for item in items}

    def resolve_container_alias(item: StructureLlmItem) -> str | None:
        """앵커 여부와 상관없이, item의 부모 체인을 끝까지 따라가 컨테이너 별칭을 찾는다."""
        current = item
        seen: set[str] = set()
        while True:
            if current.item_id in seen:
                return None  # 순환 참조 — 다른 검증이 잡는다.
            seen.add(current.item_id)
            if current.parent_ref is not None:
                return current.parent_ref
            if current.parent_item_id is None:
                return None
            parent = by_id.get(current.parent_item_id)
            if parent is None:
                return None
            current = parent

    def find_broken_container_alias(item: StructureLlmItem) -> str | None:
        """진짜 앵커를 거치지 않고 컨테이너 별칭에 닿으면 그 별칭을 반환한다."""
        current = item
        seen: set[str] = set()
        while True:
            if current.item_id in seen:
                return None  # 순환 참조 — 다른 검증이 잡는다.
            seen.add(current.item_id)
            if current.parent_ref is not None:
                return current.parent_ref
            if current.parent_item_id is None:
                return None
            parent = by_id.get(current.parent_item_id)
            if parent is None:
                return None
            if _is_anchor_slot(parent.slot_id, catalog):
                return None  # 정상 — 앵커를 거쳤다.
            current = parent

    orphan_groups: dict[str, list[StructureLlmItem]] = {}
    for item in items:
        if not item.slot_id or item.slot_id.count(".") != 2:
            continue
        section_id = item.slot_id.split(".")[0]
        if section_id not in anchor_slot_by_section:
            continue  # 하위 템플릿이 없는 section이면 애초에 level 5가 없다.
        alias = find_broken_container_alias(item)
        if alias is None:
            continue
        orphan_groups.setdefault(f"{alias}::{section_id}", []).append(item)

    if not orphan_groups:
        return items

    new_anchors: list[StructureLlmItem] = []
    reparent_to: dict[str, str] = {}  # orphan item_id -> anchor item_id
    counter = 0
    for key, group in orphan_groups.items():
        parent_ref, section_id = key.split("::", 1)
        anchor_slot_id = anchor_slot_by_section[section_id]
        existing_anchor = next(
            (
                it
                for it in items
                if it.slot_id == anchor_slot_id and resolve_container_alias(it) == parent_ref
            ),
            None,
        )
        if existing_anchor is not None:
            anchor_id = existing_anchor.item_id
        else:
            counter += 1
            anchor_id = f"auto_anchor_{section_id}_{counter}"
            new_anchors.append(
                StructureLlmItem(
                    item_id=anchor_id,
                    action="add",
                    slot_id=anchor_slot_id,
                    text=None,
                    source_item_ids=[],
                    parent_ref=parent_ref,
                )
            )
        for orphan in group:
            reparent_to[orphan.item_id] = anchor_id

    result = [
        item.model_copy(update={"parent_ref": None, "parent_item_id": reparent_to[item.item_id]})
        if item.item_id in reparent_to
        else item
        for item in items
    ]
    result.extend(new_anchors)
    return result


def _dedupe_anchor_matching_child_source(
    items: list[StructureLlmItem], catalog: TemplateCatalog
) -> list[StructureLlmItem]:
    """앵커가 자기 바로 아래 level 5 슬롯과 같은 원문을 또 쓰면 앵커를 비운다.

    프롬프트는 "같은 입력 item을 SUMMARY 앵커와 세부 슬롯에 동시에 쓰지
    말라 — 더 구체적인 세부 슬롯에만 배정하고, 앵커는 요약할 별도 원문이
    없으면 비워 둔다"고 명시하는데도, 모델이 앵커와 그 하위 level 5 슬롯에
    **완전히 같은** `source_item_ids`를 중복 배정하는 사고가 있었다. 어느
    쪽이 "더 구체적인 배정"인지 모호하지 않다 — level 5가 항상 더
    구체적이므로, 앵커 쪽 배정을 비워서 프롬프트가 원래 원하는 모습으로
    코드가 되돌린다. 완전히 같은 집합일 때만 다룬다 — 일부만 겹치거나
    앵커가 더 많은 원문을 요약한 경우는 모호해서 건드리지 않는다.
    """
    children_by_parent: dict[str, list[StructureLlmItem]] = {}
    for item in items:
        if item.parent_item_id:
            children_by_parent.setdefault(item.parent_item_id, []).append(item)

    result = list(items)
    for index, item in enumerate(result):
        if not item.source_item_ids or not _is_anchor_slot(item.slot_id, catalog):
            continue
        anchor_sources = set(item.source_item_ids)
        for child in children_by_parent.get(item.item_id, []):
            if child.source_item_ids and set(child.source_item_ids) == anchor_sources:
                result[index] = item.model_copy(update={"text": None, "source_item_ids": []})
                break
    return result


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
    items: list[StructureLlmItem], catalog: TemplateCatalog, state: ExperienceMapState
) -> list[StructureLlmItem]:
    """모델이 빠뜨린 level 5 빈 슬롯을 코드가 직접 채워 넣는다.

    명세는 "선택한 하위 템플릿의 slot을 빈 슬롯까지 전부 전개"하라고 못박지만,
    원문이 짧을 때 모델이 정보 있는 slot 하나만 만들고 나머지 null placeholder를
    누락하는 사고가 반복됐다. 프롬프트 문구를 아무리 강화해도 매 요청마다
    비결정적으로 재발해서, "어떤 하위 템플릿을 어느 부모 아래 만들었는지"가
    이미 출력에 다 드러난 이상 나머지 slot_id는 코드가 결정론적으로 채운다 —
    모델의 확률적 성실성에 기대지 않는다.

    **부모가 이미 존재하는(커밋된) 블록이면 채우지 않는다.** 그 앵커
    아래 실제로 무슨 slot이 이미 있는지는 활동 트리 문자열만으로는 알
    방법이 없다 — 채워진 실제 내용은 placeholder 문구를 안 남기기
    때문이다(빈 슬롯만 문구가 남는다, 명세 3-7). 실제로 이미 채워진
    슬롯 옆에 "빠진 슬롯"이라며 또 빈 슬롯을 만들어 중복시킨 적이 있다.
    이번 턴에 방금 새로 만든 앵커(배치 내부 item_id로만 연결된 경우)만
    전체 템플릿 구조를 확신할 수 있으므로 그 경우에만 채운다.
    """
    known_aliases = state.get("alias_to_block_id", {})
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
        if parent in known_aliases:
            continue  # 기존 블록 밑이면 옆에 다른 실제 slot이 있을 수 있어 안 건드린다.
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
    existing_categories: list[ExistingCategoryClassification] | None = None,
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
            raise ValueError(f"카탈로그에 없는 slot_id입니다: [{item.item_id}] {item.slot_id}")
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
    _validate_template_slots(items, catalog, state)
    _validate_new_sections(items, catalog, state)
    _validate_category_reuse(items, existing_categories or [], state)
    _validate_anchor_reuse(items, existing_categories or [], catalog, state)
    _validate_anchor_reuse_deterministic(items, catalog, state)
    _validate_non_empty_subtrees(items, catalog)
    _validate_gap_parent(items, state)
    return items


def _validate_category_reuse(
    items: list[StructureLlmItem],
    existing_categories: list[ExistingCategoryClassification],
    state: ExperienceMapState,
) -> None:
    """모델이 스스로 분류한 기존 카테고리와 같은 section을 또 새로 만들면 거부한다.

    "활동 트리에 이미 있으면 재사용하라"는 지시만으로는 모델이 자기가 방금
    분류한 것과 모순되게 행동하는 사고가 절반 가까이 재발했다 — 트리를 읽고
    기존 컨테이너의 section을 맞게 판단해 놓고도, 그 판단과 무관하게 새
    `section_kind` 컨테이너를 또 만들었다. `existing_categories`로 그 판단을
    출력에 미리 못박게 해서, 이후 자기모순(판단 따로·행동 따로)을 코드가
    바로 걸러 재시도를 유도한다.

    실제로 모델이 카테고리 컨테이너가 하나도 없는 활동에서 **활동 별칭 자체**를
    "이미 있는 카테고리"로 잘못 신고한 적이 있다 — 새 카테고리는 항상 활동
    별칭을 `parent_ref`로 삼으므로, 그 신고를 곧이곧대로 믿으면 정상적으로
    새 카테고리를 만드는 정상 케이스까지 자기모순으로 오판해 거부하게 된다.
    카테고리 컨테이너는 언제나 활동의 **하위**(level 3)이지 활동 자신이 될 수
    없으므로, 활동 별칭을 가리키는 신고는 무시한다.
    """
    target_alias = state.get("target_experience_alias")
    classified_sections = {
        category.section_kind for category in existing_categories if category.alias != target_alias
    }
    for item in items:
        if item.section_kind is not None and item.section_kind in classified_sections:
            alias = next(
                category.alias
                for category in existing_categories
                if category.section_kind == item.section_kind and category.alias != target_alias
            )
            raise ValueError(
                f"[{item.section_kind}] section은 이미 활동 트리의 [{alias}]로 판단하고도 "
                "새 카테고리를 또 만들었습니다. 새로 만들지 말고 그 별칭을 parent_ref로 "
                "재사용하세요."
            )


def _validate_anchor_reuse(
    items: list[StructureLlmItem],
    existing_categories: list[ExistingCategoryClassification],
    catalog: TemplateCatalog,
    state: ExperienceMapState,
) -> None:
    """기존 컨테이너를 재사용하면서 그 안에 이미 있는 앵커를 또 새로 만들면 거부한다.

    카테고리 컨테이너는 제대로 재사용해도(`section_kind`를 또 안 만들어도),
    그 아래 앵커(level 4)는 매 턴 새로 하나씩 또 만드는 사고가 있었다 — 같은
    컨테이너 밑에 "문제해결 요약" 앵커가 두 개씩 생기는 식이다. 앵커도
    `existing_categories.existing_anchor_alias`로 미리 신고하게 해서, 이미
    있다고 신고한 컨테이너에 새 앵커를 또 붙이면 코드가 걸러 재시도를 유도한다.

    `_validate_category_reuse`와 같은 이유로, 활동 별칭 자체를 컨테이너로
    신고한 항목은 무시한다 — 카테고리 컨테이너는 활동의 하위일 뿐 활동 자신일
    수 없다.
    """
    target_alias = state.get("target_experience_alias")
    anchor_alias_by_container = {
        category.alias: category.existing_anchor_alias
        for category in existing_categories
        if category.existing_anchor_alias and category.alias != target_alias
    }
    for item in items:
        if item.parent_ref is None or not _is_anchor_slot(item.slot_id, catalog):
            continue
        existing_anchor = anchor_alias_by_container.get(item.parent_ref)
        if existing_anchor is not None:
            raise ValueError(
                f"[{item.parent_ref}] 아래에는 이미 앵커 [{existing_anchor}]가 있다고 "
                "신고하고도 새 앵커를 또 만들었습니다. 새로 만들지 말고 그 별칭을 "
                "parent_ref로 재사용하세요."
            )


_TREE_LINE_RE = re.compile(r"^( *)\[(\w+)\] (.*)$")
_EMPTY_SLOT_GUIDE_RE = re.compile(r"^\(빈 블록 — 가이드: (.+)\)$")


def _parse_tree_lines(tree_text: str) -> list[tuple[int, str, str]]:
    """activity_tree_text를 (depth, alias, label) 목록으로 파싱한다.

    depth는 `map_context.py`의 렌더링 규칙(들여쓰기 2칸당 1단계)을 그대로 따른다.
    """
    parsed: list[tuple[int, str, str]] = []
    for line in tree_text.splitlines():
        match = _TREE_LINE_RE.match(line)
        if not match:
            continue
        indent, alias, label = match.groups()
        parsed.append((len(indent) // 2, alias, label))
    return parsed


def _placeholder_to_slot_map(catalog: TemplateCatalog) -> dict[str, str]:
    """빈 슬롯 가이드 문구로 slot_id를 되짚을 수 있는, placeholder가 겹치지 않는 슬롯만."""
    by_placeholder: dict[str, list[str]] = {}
    for slot in catalog.iter_slots():
        by_placeholder.setdefault(slot.placeholder, []).append(slot.slot_id)
    return {
        placeholder: slot_ids[0]
        for placeholder, slot_ids in by_placeholder.items()
        if len(slot_ids) == 1
    }


def _subtree_known_slot_ids(
    tree_lines: list[tuple[int, str, str]], root_alias: str, placeholder_to_slot: dict[str, str]
) -> set[str]:
    """root_alias 서브트리 안에서, 빈 슬롯 가이드 문구로 알아낼 수 있는 slot_id들.

    한 번이라도 커밋된 빈 슬롯은 카탈로그의 placeholder 문구를 그대로 달고
    있다(명세 3-7). 그 문구를 거꾸로 slot_id에 매핑하면 "이 서브트리가 이미
    어느 section의 템플릿을 갖고 있는지"를 LLM 자기 신고 없이도 코드가
    독립적으로 판별할 수 있다 — `existing_categories` 자기 신고 자체를
    빠뜨리는 사고까지 잡기 위한 이중 방어다.
    """
    depth_by_alias = {alias: depth for depth, alias, _ in tree_lines}
    if root_alias not in depth_by_alias:
        return set()
    root_depth = depth_by_alias[root_alias]

    slot_ids: set[str] = set()
    in_subtree = False
    for depth, alias, label in tree_lines:
        if alias == root_alias:
            in_subtree = True
            continue
        if not in_subtree:
            continue
        if depth <= root_depth:
            break
        match = _EMPTY_SLOT_GUIDE_RE.match(label)
        if match:
            slot_id = placeholder_to_slot.get(match.group(1))
            if slot_id:
                slot_ids.add(slot_id)
    return slot_ids


def _subtree_known_slots_with_parent(
    tree_lines: list[tuple[int, str, str]], root_alias: str, placeholder_to_slot: dict[str, str]
) -> list[tuple[str, str, str]]:
    """`_subtree_known_slot_ids`와 같지만, 슬롯을 아는 블록의 별칭·부모 별칭도 같이 낸다.

    빈 슬롯의 부모가 곧 그 템플릿의 진짜 앵커다 — 이 정보가 있어야 "이미 있는
    앵커의 별칭이 무엇인지"까지 알아내 새 앵커를 그 별칭으로 되돌릴 수 있다.
    """
    depth_by_alias = {alias: depth for depth, alias, _ in tree_lines}
    if root_alias not in depth_by_alias:
        return []
    root_depth = depth_by_alias[root_alias]

    results: list[tuple[str, str, str]] = []
    stack: list[tuple[int, str]] = [(root_depth, root_alias)]
    in_subtree = False
    for depth, alias, label in tree_lines:
        if alias == root_alias:
            in_subtree = True
            continue
        if not in_subtree:
            continue
        if depth <= root_depth:
            break
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent_alias = stack[-1][1] if stack else root_alias
        stack.append((depth, alias))
        match = _EMPTY_SLOT_GUIDE_RE.match(label)
        if match:
            slot_id = placeholder_to_slot.get(match.group(1))
            if slot_id:
                results.append((slot_id, alias, parent_alias))
    return results


def _reuse_existing_filled_anchor(
    items: list[StructureLlmItem], catalog: TemplateCatalog, state: ExperienceMapState
) -> list[StructureLlmItem]:
    """활동 트리에 이미 채워진 앵커가 있는데 새 앵커를 또 만들면, 가능하면 코드가 되돌린다.

    `_validate_anchor_reuse`(자기 신고 대조)와 `existing_categories`
    안내만으로는, 컨테이너 안의 앵커가 **이미 실제 내용으로 채워져 있는**
    경우까지 모델이 놓치는 사고가 있었다 — 빈 슬롯 안내문(명세 3-7)이 없는
    채워진 블록은 지금까지의 결정론적 검사(`_subtree_known_slot_ids`)로도
    안 잡혔다. 실제로 재현된 경우다: 이미 채워진 앵커 옆에 새 앵커 +
    빈 템플릿 전체를 또 만들었는데, 그 서브트리에 남은 진짜 빈 슬롯은 이번에
    반영하려는 내용과 정확히 하나만 일치했다 — 그렇다면 새 앵커가 아니라
    그 빈 슬롯을 채우려던 의도가 명백하므로, 코드가 새 앵커를 그 슬롯으로
    바꾸고 그 앵커의 부모(컨테이너)를 원래 앵커로 되돌린다. 일치가 하나가
    아니면(0개 또는 여러 개) 모호해서 그대로 두고 이후 검증이 에러로
    보고하게 한다.
    """
    tree_lines = _parse_tree_lines(state.get("activity_tree_text") or "")
    placeholder_to_slot = _placeholder_to_slot_map(catalog)
    by_id = {item.item_id: item for item in items}
    drop: set[str] = set()
    changed = False

    for item in items:
        if item.parent_ref is None or not _is_anchor_slot(item.slot_id, catalog):
            continue
        section = item.slot_id.split(".")[0]
        known_by_slot = {
            slot_id: (alias, parent_alias)
            for slot_id, alias, parent_alias in _subtree_known_slots_with_parent(
                tree_lines, item.parent_ref, placeholder_to_slot
            )
            if slot_id.split(".")[0] == section
        }
        if not known_by_slot:
            continue  # 겹치는 section 자체가 없다 — 진짜 새 앵커다.

        # 이 가짜 앵커 자신과 그 아래 자동 생성된 하위 템플릿 중, 실제
        # 내용(text 또는 source_item_ids)이 있는 것만 "무엇을 채우려던
        # 의도인지"를 판단할 근거로 쓴다 — 빈 자리 채우기용 자동 생성
        # item은 section만 같을 뿐 아무 것도 알려주지 않는다.
        group = [item] + [other for other in items if other.parent_item_id == item.item_id]
        filled = [entry for entry in group if entry.text or entry.source_item_ids]
        if not filled:
            continue

        # 내용 있는 item의 slot_id가 실제 빈 슬롯 중 하나와 정확히 같으면
        # 그 자체가 확실한 증거다 — 다른 후보가 몇 개든 상관없다(예: 같은
        # section에 빈 슬롯이 둘 있어도, 모델이 이미 그중 하나와 정확히
        # 같은 slot_id를 골랐다면 나머지 후보는 무시해도 안전하다).
        resolved: dict[str, str] = {}  # item_id -> real_anchor_alias
        unresolved = []
        used_slots: set[str] = set()
        for entry in filled:
            if entry.slot_id in known_by_slot:
                resolved[entry.item_id] = known_by_slot[entry.slot_id][1]
                used_slots.add(entry.slot_id)
            else:
                unresolved.append(entry)

        if unresolved:
            # slot_id 자체로 확인이 안 된 나머지는, 아직 안 쓰인 빈 슬롯이
            # 정확히 하나 더 남아 있을 때만(그리고 안 쓰인 게 단 하나뿐일
            # 때만) 그리로 되돌린다 — 모델이 slot_id를 잘못 골랐어도(예:
            # 앵커 자신에 실제로는 세부 슬롯 내용을 적음), 남는 자리가
            # 하나뿐이면 의도가 명백하다.
            remaining = {sid: v for sid, v in known_by_slot.items() if sid not in used_slots}
            if len(unresolved) != 1 or len(remaining) != 1:
                continue  # 모호하다 — 이 그룹은 손대지 않는다.
            entry = unresolved[0]
            target_slot_id, (_, real_anchor_alias) = next(iter(remaining.items()))
            by_id[entry.item_id] = entry.model_copy(
                update={"slot_id": target_slot_id, "parent_ref": None, "parent_item_id": None}
            )
            resolved[entry.item_id] = real_anchor_alias

        for entry_id, real_anchor_alias in resolved.items():
            current = by_id[entry_id]
            by_id[entry_id] = current.model_copy(
                update={"parent_ref": real_anchor_alias, "parent_item_id": None}
            )

        # 가짜 앵커 자신(내용이 옮겨진 경우 제외)과 내용 없는 나머지 자동
        # 생성 slot은 실제 앵커 쪽에 이미 자리가 있으므로 버린다.
        if item.item_id not in resolved:
            drop.add(item.item_id)
        drop.update(
            other.item_id
            for other in group
            if other.item_id not in resolved and other.item_id != item.item_id
        )
        changed = True

    if not changed:
        return items
    return [by_id[item.item_id] for item in items if item.item_id not in drop]


def _validate_anchor_reuse_deterministic(
    items: list[StructureLlmItem], catalog: TemplateCatalog, state: ExperienceMapState
) -> None:
    """활동 트리에 이미 있는 빈 슬롯 가이드 문구로, 자기 신고 없이도 앵커 중복을 잡는다.

    `_reuse_existing_filled_anchor`가 확신할 수 있는 경우(겹치는 빈 슬롯이
    정확히 하나)는 이미 고쳤다 — 여기는 그 마지막 방어선이다. 고칠 수
    없을 만큼 모호했던 경우(겹치는 빈 슬롯이 0개 또는 여러 개)만 여기
    걸려 재시도를 유도한다.
    """
    tree_lines = _parse_tree_lines(state.get("activity_tree_text") or "")
    placeholder_to_slot = _placeholder_to_slot_map(catalog)
    for item in items:
        if item.parent_ref is None or not _is_anchor_slot(item.slot_id, catalog):
            continue
        section = item.slot_id.split(".")[0]
        existing_slot_ids = _subtree_known_slot_ids(
            tree_lines, item.parent_ref, placeholder_to_slot
        )
        if any(slot_id.split(".")[0] == section for slot_id in existing_slot_ids):
            raise ValueError(
                f"[{item.parent_ref}] 아래에는 이미 {section} section의 앵커·하위 템플릿이 "
                "있습니다. 새 앵커를 또 만들지 말고 기존 구조를 재사용하세요."
            )


def _validate_non_empty_subtrees(items: list[StructureLlmItem], catalog: TemplateCatalog) -> None:
    """새로 만드는 앵커·카테고리 서브트리에는 실제 내용이 하나는 있어야 한다.

    실제로 모델이 관련 없는 **기존** 카테고리(예: 이미 내용이 있는 "성과")
    밑에 새 앵커 + 하위 템플릿을 통째로 만들거나, `DETAIL`·`LEARNING`처럼
    앵커가 없는 section에 새 컨테이너 자체를 통째로 만들면서 그 서브트리
    전체를 text가 전부 null인 빈 슬롯으로만 채운 적이 있다. "슬롯을
    빠짐없이 전개하라"는 규칙을 엉뚱한 곳까지 적용한 것이다. 빈 슬롯 자체는
    정상(슬롯 일부만 채워질 수 있다) 이지만, **서브트리 전체가 처음부터
    끝까지 비어 있으면** 그건 애초에 만들지 말았어야 할 카테고리다.
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
        is_root = _is_anchor_slot(item.slot_id, catalog) or item.section_kind is not None
        if is_root and not subtree_has_text(item):
            raise ValueError(
                f"[{item.item_id}] 서브트리 전체에 실제 내용이 없습니다. "
                "내용 없는 카테고리·템플릿을 통째로 새로 만들지 마세요."
            )


def _validate_source_coverage(items: list[StructureLlmItem], expected_text: dict[str, str]) -> None:
    """모든 원문 item이 정확히 한 블록에서만 쓰였는지 확인한다.

    text 자체가 원문과 정확히 같은지는 여기서 안 본다 — `_reconstruct_verbatim_text`
    가 이미 `source_item_ids`로부터 text를 코드로 다시 조립해서, 어긋날 수가
    없다. 여기서 보는 건 배정 그래프 자체의 정합성(존재하는 id인지, 두 번
    안 쓰였는지, 빠짐없이 다 쓰였는지)뿐이다.
    """
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


def _validate_template_slots(
    items: list[StructureLlmItem], catalog: TemplateCatalog, state: ExperienceMapState
) -> None:
    """사용한 level 5 템플릿은 빈 슬롯을 포함해 완전하게 전개됐는지,
    그리고 그 부모가 실제 앵커(level 4, is_anchor) 블록인지 확인한다.

    실제로 모델이 level 5 슬롯들을 앵커가 아니라 **카테고리 컨테이너**(level 3)
    바로 아래에 붙인 적이 있다. 명세 3-0은 "level 5는 반드시 앵커 슬롯 아래에
    붙는다" 고 못박는데, 프롬프트가 그 연결 대상을 명시하지 않아 모델이
    카테고리 컨테이너를 앵커로 착각했다.

    **부모가 이미 존재하는(커밋된) 블록이면 "빠짐없이 전개" 요구는 넘어간다.**
    `_fill_missing_template_slots`와 같은 이유다 — 그 앵커 아래 이미 무슨
    slot이 있는지 활동 트리 문자열만으로는 알 수 없으므로, 이번 턴에
    새로 만든 slot 몇 개만으로 "빠졌다"고 단정할 수 없다. 중복·엉뚱한
    slot 검사는 이 경우에도 그대로 한다 — 그건 새 앵커든 기존 앵커든
    항상 잘못이다.
    """
    known_aliases = state.get("alias_to_block_id", {})
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

    for (parent, prefix), slot_ids in grouped.items():
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
        if parent in known_aliases:
            continue  # 기존 블록 밑이면 옆에 다른 실제 slot이 있을 수 있어 안 건드린다.
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
