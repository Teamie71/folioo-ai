"""블록 단위 구조화 노드 (에이전트 문서 5-5)."""

import logging
import re

from common.llm import get_experience_map_llm
from features.experience_map.config import (
    MAX_FILE_SOURCE_CHARS_PER_STRUCTURE_BATCH,
    MAX_FILE_SOURCE_ITEMS_PER_STRUCTURE_BATCH,
    MAX_SOURCE_CHARS_PER_STRUCTURE_BATCH,
    MAX_SOURCE_ITEMS_PER_STRUCTURE_BATCH,
    get_settings,
)
from features.experience_map.errors import LlmError, NodeTimeoutError
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

# 구조화 모델이 카탈로그의 의미는 맞게 고르고 leaf 이름만 일반적인 표현으로
# 바꿔 내는, 실환경에서 확인된 경우만 정규화한다. prefix까지 정확히 일치해야
# 하므로 다른 템플릿의 동명 슬롯이나 완전히 지어낸 slot_id는 통과하지 않는다.
_KNOWN_SLOT_ALIASES = {
    "PROBLEM_SOLVING.TROUBLESHOOTING.RESULT": ("PROBLEM_SOLVING.TROUBLESHOOTING.VERIFICATION"),
}

_LEARNING_SLOT_SUFFIXES = frozenset({"LEARNING", "LESSON", "LESSONS"})
_LEARNING_DESTINATION_SLOT = "LEARNING.GROWTH"
_LEARNING_FALLBACK_SLOT = "TASK.BASIC.RESULT"

_DOCUMENT_HEADING_PREFIX = re.compile(r"^(?:[#>*_`-]+\s*)?(?:\d+[.)]\s*)?")
_QUANTITATIVE_MARKER = re.compile(
    r"(?:\d|%|퍼센트|\b\d+(?:\.\d+)?\s*(?:배|건|명|회|분|초|ms)\b)",
    re.IGNORECASE,
)
_DOCUMENT_SLOT_LABELS = {
    "TASK.SUMMARY": "담당 업무 > 업무·역할 요약",
    "PROBLEM_SOLVING.SUMMARY": "문제해결 > 에피소드 요약",
    "PROBLEM_SOLVING.TROUBLESHOOTING.PROBLEM": "문제해결 > 상황",
    "PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE": "문제해결 > 원인 분석",
    "PROBLEM_SOLVING.TROUBLESHOOTING.SOLUTION": "문제해결 > 해결 과정",
    "PROBLEM_SOLVING.TROUBLESHOOTING.VERIFICATION": "문제해결 > 결과·검증",
    "ACHIEVEMENT.QUANTITATIVE": "주요성과 > 정량 성과",
    "ACHIEVEMENT.QUALITATIVE": "주요성과 > 정성 성과",
    "LEARNING.GROWTH": "배운 점 > 성장·활용",
}

_BASIC_TO_TROUBLESHOOTING_SLOTS = {
    "PROBLEM_SOLVING.BASIC.PROBLEM": "PROBLEM_SOLVING.TROUBLESHOOTING.PROBLEM",
    "PROBLEM_SOLVING.BASIC.CAUSE": "PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE",
    "PROBLEM_SOLVING.BASIC.SOLUTION": "PROBLEM_SOLVING.TROUBLESHOOTING.SOLUTION",
    "PROBLEM_SOLVING.BASIC.RESULT": "PROBLEM_SOLVING.TROUBLESHOOTING.VERIFICATION",
}


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
        document_slot_hints = _document_slot_hints(source_items, state.get("extracted_text"))

        # 원문이 많으면(실제로 파일 업로드에서 15개 넘는 경우가 흔하다) 한
        # 번에 다 맡길수록 모델이 일부를 빠뜨리거나 잘못 연결하는 사고가
        # 눈에 띄게 잦아진다. 작은 배치로 나눠 순차 처리하면 배치당 실패율이
        # 크게 낮아진다 — 뒤 배치는 앞 배치가 이미 만든 카테고리·앵커를
        # `previous_batch_note`로 안내받아 재사용할 수 있다.
        items: list[StructureLlmItem] = []
        existing_categories: list[ExistingCategoryClassification] = []
        cumulative_source_text: dict[str, str] = {}
        call_index = 0
        batches = _source_batches(source_items)
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
                    "document_context": _render_document_context(
                        round_source_items, document_slot_hints
                    ),
                    "source_items": render_source_items(round_source_items),
                }
                active_chain = chain if attempt == 0 else retry_chain
                try:
                    result: StructureOutput = await active_chain.ainvoke(
                        {**prompt_vars, "gap_instruction": instruction}
                    )
                except Exception as exc:
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
                    if _is_timeout_exception(exc):
                        logger.warning(
                            "structure: 배치 %d/%d LLM 제한 시간 초과, 같은 배치 재시도",
                            batch_index + 1,
                            len(batches),
                        )
                        instruction = base_instruction + (
                            "**주의: 이전 시도는 제한 시간 안에 끝나지 않았습니다.** "
                            "이번 원문 item만 가장 가까운 카테고리와 템플릿 하나에 "
                            "배정하고, 관련 없는 카테고리·템플릿은 출력하지 마세요.\n\n"
                        )
                        continue
                    logger.warning(
                        "structure: 배치 %d/%d 응답 파싱 실패, 같은 배치 재시도",
                        batch_index + 1,
                        len(batches),
                        exc_info=True,
                    )
                    instruction = base_instruction + (
                        "**주의: 이전 시도에서 일부 item의 부모 참조가 스키마 규칙을 "
                        "어겼습니다.** 모든 add item은 parent_ref와 parent_item_id 중 "
                        "정확히 하나만 가져야 합니다. 기존 블록 아래면 parent_ref만, "
                        "이번 응답에서 만든 새 블록 아래면 parent_item_id만 쓰고 다른 "
                        "필드는 null로 두세요.\n\n"
                    )
                    continue
                # 이번 호출이 새로 만든 item_id가 이전 호출(다른 배치, 또는
                # 같은 배치의 이전 시도)과 겹칠 수 있다 — 각 호출은 서로의
                # 출력을 모르는 채 독립적으로 "blk_1" 같은 이름을 짓는다.
                # 병합할 게 있는(call_index > 0) 모든 호출에 접두사를 붙여
                # 겹치지 않게 한다. 첫 호출(call_index == 0)은 병합 대상이
                # 없으니 그대로 둔다 — item_id가 굳이 안 바뀌는 편이 낫다.
                if any(raw_item.action != "add" for raw_item in result.items):
                    # 구조화 LLM은 항상 add만 낸다 — update는 결정론적 보정
                    # (`_reuse_existing_filled_anchor`,
                    # `_redirect_leaf_add_to_existing_empty_slot`)이 add를
                    # 안전한 경우에만 코드로 바꿔치기하는 용도로만 쓴다.
                    raise ValueError("구조화 노드는 add operation만 만들 수 있습니다.")
                namespaced = (
                    _namespace_batch_item_ids(result.items, call_index)
                    if call_index > 0
                    else result.items
                )
                call_index += 1
                candidate = _apply_structuring_fixups(
                    round_note_items + namespaced,
                    cumulative_source_text,
                    state,
                    catalog,
                    document_slot_hints,
                )
                existing_categories = result.existing_categories
                missing = _missing_source_ids(candidate, batch_source_text)
                duplicated = _duplicate_source_ids(candidate) & set(batch_source_text)
                batch_items = candidate
                if not missing and not duplicated:
                    break
                if attempt == 0:
                    if missing:
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
                    else:
                        logger.warning(
                            "structure: 배치 %d/%d 원문 item 중복 배정 감지, "
                            "그 원문만 좁혀서 재시도 (중복 %d개)",
                            batch_index + 1,
                            len(batches),
                            len(duplicated),
                        )
                        # 중복 배정한 item은 "이미 잘 만든 것" 취급하면 안 된다 —
                        # previous_batch_note에서 빼서 모델이 다시 판단하게 한다.
                        round_note_items = [
                            item
                            for item in candidate
                            if not (set(item.source_item_ids) & duplicated)
                        ]
                        round_source_items = [
                            item for item in batch if item["item_id"] in duplicated
                        ]
                        instruction = base_instruction + _duplicate_source_repair_instruction(
                            duplicated, batch
                        )
            items = batch_items

        # 배치를 다 처리한 뒤 딱 한 번만 빈 슬롯을 채운다 — 배치마다 채우면
        # 뒤 배치가 실제로 채우려는 slot을 앞 배치가 먼저 빈 슬롯으로 선점해
        # 같은 slot이 두 번 생긴다.
        non_empty_items = _drop_empty_new_section_subtrees(items)
        meaningful_template_items = _drop_empty_template_groups(non_empty_items)
        section_filled_items = _fill_missing_section_slots(meaningful_template_items, catalog)
        filled_items = _fill_missing_template_slots(section_filled_items, catalog, state)
        ordered_items = _order_parents_before_children(filled_items)
        try:
            validated = _validate_output(
                ordered_items,
                source_items=source_items,
                catalog=catalog,
                state=state,
                existing_categories=existing_categories,
            )
        except Exception:
            logger.error(
                "structure: 검증 실패 item 연결 정보=%s",
                [
                    {
                        "item_id": item.item_id,
                        "parent_ref": item.parent_ref,
                        "parent_item_id": item.parent_item_id,
                        "section_kind": item.section_kind,
                        "slot_id": item.slot_id,
                        "has_text": item.text is not None,
                        "source_count": len(item.source_item_ids),
                    }
                    for item in ordered_items
                ],
            )
            raise
    except LlmError:
        raise
    except Exception as exc:
        if _is_timeout_exception(exc):
            logger.exception("structure: LLM 제한 시간 초과")
            raise NodeTimeoutError(
                "블록 구조화 시간이 초과되었습니다.", failed_node="structure"
            ) from exc
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


def _is_timeout_exception(exc: BaseException) -> bool:
    """LangChain/OpenAI/httpx 예외 체인에 제한 시간 초과가 있는지 확인한다."""
    timeout_names = {
        "APITimeoutError",
        "ConnectTimeout",
        "PoolTimeout",
        "ReadTimeout",
        "TimeoutException",
        "WriteTimeout",
    }
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError) or type(current).__name__ in timeout_names:
            return True
        current = current.__cause__ or current.__context__
    return False


def _source_items(state: ExperienceMapState) -> list[dict]:
    """구조화가 맡을 새 내용과 new_child gap 답변을 모은다."""
    items = list(state.get("new_items", []))
    if (state.get("active_gap") or {}).get("gap_type") == "new_child_block":
        items.extend(state.get("gap_answer_items", []))
    return items


def _source_batches(source_items: list[dict]) -> list[list[dict]]:
    """원문 item 수와 총 글자 수를 함께 제한해 구조화 배치를 만든다.

    정상 경로에서는 content_filter가 item 하나를 먼저 제한한다. 이 글자 수 제한은
    체크포인트 복구나 직접 노드 호출처럼 그 단계를 우회한 입력도 한 번의 LLM
    호출에 과도하게 합쳐지지 않도록 하는 방어선이다.
    """
    has_file_source = any(item.get("source") == "file" for item in source_items)
    max_items = (
        MAX_FILE_SOURCE_ITEMS_PER_STRUCTURE_BATCH
        if has_file_source
        else MAX_SOURCE_ITEMS_PER_STRUCTURE_BATCH
    )
    max_chars = (
        MAX_FILE_SOURCE_CHARS_PER_STRUCTURE_BATCH
        if has_file_source
        else MAX_SOURCE_CHARS_PER_STRUCTURE_BATCH
    )

    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0

    for item in source_items:
        item_chars = len(str(item.get("text") or ""))
        exceeds_count = len(current) >= max_items
        exceeds_chars = current and current_chars + item_chars > max_chars
        if exceeds_count or exceeds_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars

    if current:
        batches.append(current)
    return batches


def _document_heading_slot(line: str, current_section: str | None) -> tuple[str, str] | None:
    """문서 제목을 section·slot 문맥으로 변환한다.

    제목 뒤 본문이 이어지는 문서의 원래 계층을 사용하므로, 일반
    문장 내의 "결과"나 "상황"은 제목으로 오인하지 않는다.
    """
    heading = _DOCUMENT_HEADING_PREFIX.sub("", line).strip(" *_`:：")
    compact = re.sub(r"\s+", " ", heading)

    if compact == "담당 업무":
        return "TASK", "TASK.SUMMARY"
    if compact == "주요 성과":
        return "ACHIEVEMENT", "ACHIEVEMENT.QUANTITATIVE"
    if compact == "배운 점":
        return "LEARNING", "LEARNING.GROWTH"
    if compact == "문제 해결 경험" or re.match(r"^문제\s*해결\s*경험\s*[—:\-]", compact):
        return "PROBLEM_SOLVING", "PROBLEM_SOLVING.SUMMARY"

    if current_section != "PROBLEM_SOLVING":
        return None
    problem_slots = {
        "상황": "PROBLEM_SOLVING.TROUBLESHOOTING.PROBLEM",
        "상황 설명": "PROBLEM_SOLVING.TROUBLESHOOTING.PROBLEM",
        "원인 분석": "PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE",
        "해결 과정": "PROBLEM_SOLVING.TROUBLESHOOTING.SOLUTION",
        "결과": "PROBLEM_SOLVING.TROUBLESHOOTING.VERIFICATION",
    }
    slot_id = problem_slots.get(compact)
    return (current_section, slot_id) if slot_id else None


def _document_slot_hints(source_items: list[dict], extracted_text: str | None) -> dict[str, str]:
    """파일 item에 가장 가까운 앞쪽 문서 제목의 슬롯을 연결한다.

    content_filter가 제목을 내용 item에서 제외해도 원본 전체는
    `extracted_text`에 남아 있다. 구조화 배치가 한 item씩 나뉘는 파일이어도
    이 위치 정보로 상황·원인·해결·결과 문맥을 잃지 않는다.
    """
    if not extracted_text or not any(item.get("source") == "file" for item in source_items):
        return {}

    document_parts: list[str] = []
    markers: list[tuple[int, str, str]] = []
    offset = 0
    current_section: str | None = None
    for raw_line in extracted_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if document_parts:
            offset += 1
        start = offset
        document_parts.append(line)
        offset += len(line)

        marker = _document_heading_slot(line, current_section)
        if marker is not None:
            current_section, slot_id = marker
            markers.append((start, current_section, slot_id))

    if not markers:
        return {}

    document = " ".join(document_parts)
    hints: dict[str, str] = {}
    search_from = 0
    for item in source_items:
        if item.get("source") != "file":
            continue
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text:
            continue
        position = document.find(text, search_from)
        if position < 0:
            position = document.find(text)
        if position < 0:
            continue
        search_from = position + len(text)
        preceding = [marker for marker in markers if marker[0] <= position]
        if not preceding:
            continue
        _, section, slot_id = preceding[-1]
        if section == "ACHIEVEMENT":
            slot_id = (
                "ACHIEVEMENT.QUANTITATIVE"
                if _QUANTITATIVE_MARKER.search(text)
                else "ACHIEVEMENT.QUALITATIVE"
            )
        hints[str(item["item_id"])] = slot_id
    return hints


def _render_document_context(source_items: list[dict], hints: dict[str, str]) -> str:
    """구조화 LLM이 배치 밖 문서 제목을 잃지 않게 힌트를 렌더링한다."""
    lines = [
        f"- [{item['item_id']}] {_DOCUMENT_SLOT_LABELS[hints[item['item_id']]]} "
        f"(slot_id={hints[item['item_id']]})"
        for item in source_items
        if item.get("item_id") in hints
    ]
    if not lines:
        return ""
    return "문서 내 위치 힌트(배정 판단에만 사용, text에 복사 금지):\n" + "\n".join(lines) + "\n\n"


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
            for source_id in member.source_item_ids:
                if source_id not in combined:
                    combined.append(source_id)
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


def _merge_duplicate_section_items(items: list[StructureLlmItem]) -> list[StructureLlmItem]:
    """같은 section_kind의 신규 카테고리를 하나로 합치고 자식을 다시 연결한다."""
    canonical_by_kind: dict[str, str] = {}
    redirect: dict[str, str] = {}
    for item in items:
        if item.section_kind is None:
            continue
        canonical = canonical_by_kind.setdefault(item.section_kind, item.item_id)
        if canonical != item.item_id:
            redirect[item.item_id] = canonical

    if not redirect:
        return items
    return [
        item.model_copy(
            update={"parent_item_id": redirect.get(item.parent_item_id, item.parent_item_id)}
        )
        for item in items
        if item.item_id not in redirect
    ]


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
    document_slot_hints: dict[str, str] | None = None,
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
    normalized_slots = _normalize_known_slot_aliases(raw_items, catalog)
    context_aligned = _apply_document_slot_hints(
        normalized_slots, catalog, document_slot_hints or {}
    )
    merged_sections = _merge_duplicate_section_items(context_aligned)
    merged = _merge_duplicate_slot_items(merged_sections)
    pruned_junk = _drop_empty_invalid_slot_items(merged, catalog)
    reconstructed = _reconstruct_verbatim_text(pruned_junk, source_text)
    rerefed = _fix_batch_local_parent_ref(reconstructed, state)
    repaired_refs = _repair_unknown_slot_parent_refs(rerefed, state)
    dereffed = _clear_invalid_after_ref(repaired_refs, state)
    rerooted = _fix_new_section_parent(dereffed, state)
    normalized_hierarchy = _normalize_new_hierarchy(rerooted, catalog, state)
    reparented = _reparent_orphan_level5_items(normalized_hierarchy, catalog)
    # `_reuse_existing_filled_anchor`는 반드시 `_reparent_orphan_level5_items`
    # 뒤에 와야 한다. 앞서 두면, 이게 앵커를 level 5로 바꿔 기존 앵커에
    # `parent_ref`로 직접 붙인 결과를 `_reparent_orphan_level5_items`가
    # "앵커를 건너뛴 level 5"로 오해해 가짜 앵커를 또 만들어 버린다 —
    # `parent_ref`가 실제로 앵커를 가리키는지는 그 함수가 알 방법이 없다.
    reused = _reuse_existing_filled_anchor(reparented, catalog, state)
    collapsed = _collapse_basic_troubleshooting_templates(reused)
    remerged = _merge_duplicate_slot_items(collapsed)
    rebuilt = _reconstruct_verbatim_text(remerged, source_text)
    deduped = _dedupe_anchor_matching_child_source(rebuilt, catalog)
    pruned = _prune_extra_templates(deduped)
    return _redirect_leaf_add_to_existing_empty_slot(pruned, catalog, state)


def _normalize_known_slot_aliases(
    items: list[StructureLlmItem], catalog: TemplateCatalog
) -> list[StructureLlmItem]:
    """관찰된 비공식 slot 별칭을 의미가 확정된 공식 slot_id로 바꾼다."""
    normalized: list[StructureLlmItem] = []
    for item in items:
        official = _KNOWN_SLOT_ALIASES.get(item.slot_id or "")
        # 모델은 PDF의 명시적인 "배운 점" 제목을 보고
        # TASK.BASIC.LEARNING이나 PROBLEM_SOLVING.RECOVERY.LEARNING처럼
        # 비공식 슬롯을 만들었다. 에이전트 카탈로그에는 배운 점 전용
        # `LEARNING.GROWTH`가 있으므로 그 공식 슬롯으로 귀속한다.
        learning_destination = (
            _LEARNING_DESTINATION_SLOT
            if catalog.get_slot(_LEARNING_DESTINATION_SLOT) is not None
            else _LEARNING_FALLBACK_SLOT
        )
        if (
            official is None
            and item.slot_id is not None
            and catalog.get_slot(item.slot_id) is None
            and item.slot_id.rsplit(".", maxsplit=1)[-1] in _LEARNING_SLOT_SUFFIXES
            and catalog.get_slot(learning_destination) is not None
        ):
            official = learning_destination
        if official is not None and catalog.get_slot(official) is not None:
            logger.warning(
                "structure: 비공식 slot_id를 정규화합니다 (%s -> %s)",
                item.slot_id,
                official,
            )
            normalized.append(item.model_copy(update={"slot_id": official}))
        else:
            normalized.append(item)
    return normalized


def _apply_document_slot_hints(
    items: list[StructureLlmItem],
    catalog: TemplateCatalog,
    hints: dict[str, str],
) -> list[StructureLlmItem]:
    """문서 제목으로 확정된 item의 슬롯을 LLM 출력에 강제한다.

    한 블록에 합쳐진 원문이 모두 같은 문서 구획에 속할 때만 바꾼다.
    서로 다른 구획 item이 한 블록에 섞였다면 의미가 모호하므로 기존
    검증에 맡긴다.
    """
    aligned: list[StructureLlmItem] = []
    for item in items:
        hinted_slots = {
            hints[source_id] for source_id in item.source_item_ids if source_id in hints
        }
        if len(hinted_slots) != 1:
            aligned.append(item)
            continue
        hinted_slot = next(iter(hinted_slots))
        if catalog.get_slot(hinted_slot) is None:
            aligned.append(item)
            continue
        if item.slot_id != hinted_slot:
            logger.info(
                "structure: 문서 구획에 맞게 slot_id를 보정합니다 (%s -> %s)",
                item.slot_id,
                hinted_slot,
            )
        aligned.append(item.model_copy(update={"slot_id": hinted_slot}))
    return aligned


def _collapse_basic_troubleshooting_templates(
    items: list[StructureLlmItem],
) -> list[StructureLlmItem]:
    """같은 앵커의 BASIC+TROUBLESHOOTING을 더 구체적인 템플릿으로 합친다.

    두 템플릿은 문제·원인·해결·결과(검증)가 일대일로 대응한다. 파일 원문을
    여러 배치로 처리할 때 앞 배치가 BASIC을, 뒤 배치가 TROUBLESHOOTING을
    선택해 같은 앵커에 둘 다 붙이는 실수가 확인됐다. 다른 템플릿 조합은 이런
    안전한 대응 관계가 없으므로 건드리지 않고 기존 검증에서 거부한다.
    """
    prefixes_by_parent: dict[str, set[str]] = {}
    for item in items:
        if not item.slot_id or item.slot_id.count(".") != 2:
            continue
        parent = item.parent_ref or item.parent_item_id or ""
        prefix = ".".join(item.slot_id.split(".")[:2])
        prefixes_by_parent.setdefault(parent, set()).add(prefix)

    collapsible_parents = {
        parent
        for parent, prefixes in prefixes_by_parent.items()
        if prefixes == {"PROBLEM_SOLVING.BASIC", "PROBLEM_SOLVING.TROUBLESHOOTING"}
    }
    if not collapsible_parents:
        return items

    result: list[StructureLlmItem] = []
    for item in items:
        parent = item.parent_ref or item.parent_item_id or ""
        official = _BASIC_TO_TROUBLESHOOTING_SLOTS.get(item.slot_id or "")
        if parent in collapsible_parents and official is not None:
            result.append(item.model_copy(update={"slot_id": official}))
        else:
            result.append(item)
    return result


def _missing_source_ids(items: list[StructureLlmItem], source_text: dict[str, str]) -> set[str]:
    """어느 블록에도 배정되지 않은 원문 item_id를 찾는다.

    `_validate_source_coverage`와 달리 중복 배정은 신경 쓰지 않는다 — 중복은
    대부분 결정론적 보정(`_merge_duplicate_slot_items`, 앵커-자식 동일
    source 비우기)으로 처리된다. 그걸로 못 잡는 "서로 다른 slot 여럿이
    같은 source를 나눠 가진" 경우는 `_duplicate_source_ids`가 별도로 잡는다.
    """
    used = {sid for item in items for sid in item.source_item_ids}
    return set(source_text) - used


def _duplicate_source_ids(items: list[StructureLlmItem]) -> set[str]:
    """서로 다른 item에 두 번 이상 배정된(보정으로도 안 풀린) source_item_id를 찾는다.

    실제로 재현된 경우다. 원문이 한 문장(예: 대인관계 갈등 상황을 서술한
    한 단락)뿐이라 content_filter가 이를 나누지 않고 하나의 원문 item으로
    넘겼는데, 모델이 그 문장을 대인관계 템플릿의 서로 다른 level 5 슬롯
    2~3개(SITUATION/ACTION/OUTCOME)에 전부 같은 출처로 붙였다. 기존
    결정론적 보정은 "같은 (부모, slot_id) 중복"이나 "앵커와 그 자식이 완전히
    같은 source" 만 다루므로, 서로 다른 형제 슬롯끼리 같은 source를 나눠
    가지는 이 경우는 못 잡고 그대로 최종 검증까지 흘러가 재시도 기회 없이
    바로 실패했다. 배치 완료 시점에 이를 미리 감지해, "빠뜨린 원문"과
    같은 방식으로 한 번 더 재시도할 기회를 준다.
    """
    seen: set[str] = set()
    duplicated: set[str] = set()
    for item in items:
        for source_id in item.source_item_ids:
            if source_id in seen:
                duplicated.add(source_id)
            seen.add(source_id)
    return duplicated


def _duplicate_source_repair_instruction(duplicated: set[str], source_items: list[dict]) -> str:
    """같은 원문을 여러 슬롯에 나눠 붙이지 말라는 지시문을 만든다."""
    lines = "\n".join(
        f"- [{item['item_id']}] {item['text']}"
        for item in source_items
        if item["item_id"] in duplicated
    )
    return (
        "**주의: 이전 시도에서 다음 원문 item을 서로 다른 블록 여러 개에 "
        "나눠 붙였습니다.** 한 원문 item은 정확히 하나의 블록에만 배정해야 "
        "합니다. 그 원문이 여러 슬롯의 내용을 한 문장에 담고 있어도, 가장 "
        "핵심적인 슬롯 **하나에만** 배정하세요.\n"
        "**이번 응답에는 그 원문을 배정할 블록 하나만 출력하세요.** 같은 "
        "템플릿의 나머지 형제 슬롯(빈 슬롯 포함)은 이미 앞서 만들어졌거나 "
        "시스템이 자동으로 채우므로, 이번에 다시 만들지 마세요 — 또 나눠서 "
        "붙이면 같은 오류가 반복됩니다:\n"
        f"{lines}\n\n"
    )


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
            lines.append(f"- [{item.item_id}] {item.slot_id}{anchor}")
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


def _repair_unknown_slot_parent_refs(
    items: list[StructureLlmItem], state: ExperienceMapState
) -> list[StructureLlmItem]:
    """신규 슬롯이 지어낸 기존 별칭을 가리키면 선택 활동부터 계층을 다시 만든다.

    validate 보정 뒤 구조화를 다시 할 때 모델이 아직 커밋되지 않은 신규
    카테고리를 활동 트리의 기존 별칭(`b_1` 등)으로 착각하는 경우가 있다.
    `alias_to_block_id`에 없는 값은 기존 블록일 수 없고, slot_id가 있으면
    section과 앵커를 카탈로그로 결정할 수 있다. 우선 선택 활동 바로 아래로
    되돌리면 `_normalize_new_hierarchy`가 올바른 카테고리·앵커를 끼워 넣는다.
    """
    target_alias = state.get("target_experience_alias")
    known_aliases = state.get("alias_to_block_id", {})
    if not target_alias:
        return items
    return [
        item.model_copy(update={"parent_ref": target_alias})
        if item.action == "add"
        and item.slot_id is not None
        and item.parent_ref is not None
        and item.parent_ref not in known_aliases
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


def _normalize_new_hierarchy(
    items: list[StructureLlmItem],
    catalog: TemplateCatalog,
    state: ExperienceMapState,
) -> list[StructureLlmItem]:
    """신규 블록의 카테고리·앵커·level 5 부모 계층을 복구한다.

    파일 원문을 한 item씩 배치 처리하면 뒤 배치가 앞 배치의 item_id를 보면서도
    그 의미를 잘못 읽는 경우가 있다. 확인된 형태는 앵커를 활동 바로 아래에
    붙이거나, level 5를 같은 section의 다른 level 5 또는 다른 section 앵커
    아래에 붙이는 것이다. slot_id의 첫 부분이 section이고 카탈로그가 앵커를
    명시하므로 올바른 부모는 결정적이다. 기존 블록 별칭의 실제 level은 이
    응답만으로 알 수 없으므로, 신규 item 체인 또는 선택 활동 별칭까지 연결된
    경우만 보정한다.
    """
    target_alias = state.get("target_experience_alias")
    if not target_alias:
        return items

    anchor_slot_by_section = {
        section.section_id: slot.slot_id
        for section in catalog.sections
        for slot in section.slots
        if slot.is_anchor
    }
    direct_section_by_slot = {
        slot.slot_id: section.section_id for section in catalog.sections for slot in section.slots
    }
    if not direct_section_by_slot:
        return items

    result = list(items)
    used_ids = {item.item_id for item in result}

    def unique_id(prefix: str) -> str:
        counter = 1
        candidate = f"{prefix}_{counter}"
        while candidate in used_ids:
            counter += 1
            candidate = f"{prefix}_{counter}"
        used_ids.add(candidate)
        return candidate

    def by_id() -> dict[str, StructureLlmItem]:
        return {item.item_id: item for item in result}

    def ancestor(item: StructureLlmItem, predicate) -> StructureLlmItem | None:
        lookup = by_id()
        current = item
        seen: set[str] = set()
        while current.parent_item_id is not None:
            if current.item_id in seen:
                return None
            seen.add(current.item_id)
            parent = lookup.get(current.parent_item_id)
            if parent is None:
                return None
            if predicate(parent):
                return parent
            current = parent
        return None

    def root_ref(item: StructureLlmItem) -> str | None:
        lookup = by_id()
        current = item
        seen: set[str] = set()
        while True:
            if current.item_id in seen:
                return None
            seen.add(current.item_id)
            if current.parent_ref is not None:
                return current.parent_ref
            if current.parent_item_id is None:
                return None
            parent = lookup.get(current.parent_item_id)
            if parent is None:
                return None
            current = parent

    def category_for(section_id: str) -> StructureLlmItem:
        existing = next(
            (item for item in result if item.section_kind == section_id),
            None,
        )
        if existing is not None:
            return existing
        category = StructureLlmItem(
            item_id=unique_id(f"auto_category_{section_id}"),
            action="add",
            parent_ref=target_alias,
            section_kind=section_id,  # type: ignore[arg-type]
        )
        result.append(category)
        return category

    # 먼저 level 4 슬롯(앵커 포함)을 올바른 카테고리 바로 아래로
    # 옮긴다. 문서 구획 보정으로 TASK 자식이 LEARNING.GROWTH나
    # ACHIEVEMENT.QUANTITATIVE로 바뀌어도 부모까지 같이 바뀌어야 한다.
    # 그래야 이어지는 level 5 보정도 안정된 부모 체인을 기준으로
    # 판단할 수 있다.
    for index, item in enumerate(list(result)):
        section_id = direct_section_by_slot.get(item.slot_id or "")
        if section_id is None:
            continue
        direct_parent = by_id().get(item.parent_item_id or "")
        if direct_parent is not None and direct_parent.section_kind == section_id:
            continue
        matching_category = ancestor(item, lambda parent: parent.section_kind == section_id)
        if matching_category is not None:
            result[index] = item.model_copy(
                update={"parent_ref": None, "parent_item_id": matching_category.item_id}
            )
            continue
        # 기존 별칭 바로 아래의 앵커는 그 별칭이 기존 카테고리일 수 있어
        # 보존한다. 선택 활동까지 닿거나 다른 신규 section 체인에 들어간
        # 경우에만 같은 section 카테고리를 만들거나 재사용한다.
        if root_ref(item) == target_alias:
            category = category_for(section_id)
            result[index] = item.model_copy(
                update={"parent_ref": None, "parent_item_id": category.item_id}
            )

    def anchor_for(section_id: str, child: StructureLlmItem) -> StructureLlmItem | None:
        anchor_slot_id = anchor_slot_by_section.get(section_id)
        if anchor_slot_id is None:
            return None
        matching = ancestor(child, lambda parent: parent.slot_id == anchor_slot_id)
        if matching is not None:
            return matching

        matching_category = ancestor(child, lambda parent: parent.section_kind == section_id)
        child_root_ref = root_ref(child)
        if matching_category is None and child_root_ref == target_alias:
            matching_category = category_for(section_id)

        if matching_category is not None:
            existing = next(
                (
                    item
                    for item in result
                    if item.slot_id == anchor_slot_id
                    and item.parent_item_id == matching_category.item_id
                ),
                None,
            )
            if existing is not None:
                return existing
            anchor = StructureLlmItem(
                item_id=unique_id(f"auto_anchor_{section_id}"),
                action="add",
                parent_item_id=matching_category.item_id,
                slot_id=anchor_slot_id,
                text=None,
                source_item_ids=[],
            )
            result.append(anchor)
            return anchor

        # 선택 활동이 아닌 기존 별칭에서 끝나면 그 별칭은 기존 카테고리로
        # 간주한다. 기존 앵커인지 카테고리인지 판별할 정보가 없던 종전 규칙과
        # 동일하게, 이미 level 5가 직접 붙어 있으면 건드리지 않는다.
        return None

    for index, item in enumerate(list(result)):
        if not item.slot_id or item.slot_id.count(".") != 2:
            continue
        section_id = item.slot_id.split(".")[0]
        expected_anchor_slot = anchor_slot_by_section.get(section_id)
        if expected_anchor_slot is None:
            continue
        direct_parent = by_id().get(item.parent_item_id or "")
        if direct_parent is not None and direct_parent.slot_id == expected_anchor_slot:
            continue
        anchor = anchor_for(section_id, item)
        if anchor is not None:
            result[index] = item.model_copy(
                update={"parent_ref": None, "parent_item_id": anchor.item_id}
            )

    return result


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


def _drop_empty_template_groups(items: list[StructureLlmItem]) -> list[StructureLlmItem]:
    """원문이 하나도 배정되지 않은 level 5 템플릿 전체를 제외한다.

    파일의 담당업무 불릿이 모두 `TASK.SUMMARY`로 보정된 뒤에도
    모델이 미리 만든 `TASK.BASIC.*` 빈 슬롯이 남았다. 하위 템플릿은
    실제 내용을 하나라도 배정했을 때만 전체 슬롯을 펼치므로, 그룹
    전체가 비었으면 생성하지 않는다.
    """
    groups: dict[tuple[str, str], list[StructureLlmItem]] = {}
    for item in items:
        if not item.slot_id or item.slot_id.count(".") != 2:
            continue
        parent = item.parent_ref or item.parent_item_id or ""
        prefix = ".".join(item.slot_id.split(".")[:2])
        groups.setdefault((parent, prefix), []).append(item)

    referenced = {item.parent_item_id for item in items if item.parent_item_id}
    drop_ids = {
        item.item_id
        for group in groups.values()
        if all(member.text is None and not member.source_item_ids for member in group)
        for item in group
        if item.item_id not in referenced
    }
    if drop_ids:
        logger.info("내용 없는 하위 템플릿 블록 %d개를 제외합니다", len(drop_ids))
    return [item for item in items if item.item_id not in drop_ids]


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


def _fill_missing_section_slots(
    items: list[StructureLlmItem], catalog: TemplateCatalog
) -> list[StructureLlmItem]:
    """새 카테고리에서 모델이 생략한 level 4 빈 슬롯을 카탈로그대로 채운다.

    새 section과 그 자식 관계가 확정된 뒤에는 빠진 level 4 슬롯의
    ``slot_id``·부모·빈 text가 모두 결정적이다. level 5에 적용하던 같은
    원칙으로 코드가 생성하며, 원문이 배정된 기존 item은 건드리지 않는다.
    """
    sections = {section.section_id: section for section in catalog.sections}
    children_by_parent: dict[str, set[str]] = {}
    used_item_ids = {item.item_id for item in items}
    for item in items:
        if item.parent_item_id and item.slot_id:
            children_by_parent.setdefault(item.parent_item_id, set()).add(item.slot_id)

    filled = list(items)
    counter = 0
    for category in items:
        if category.section_kind is None:
            continue
        section = sections.get(category.section_kind)
        if section is None:
            continue
        present = children_by_parent.get(category.item_id, set())
        for slot in section.slots:
            if slot.slot_id in present:
                continue
            counter += 1
            item_id = f"auto_section_slot_{counter}"
            while item_id in used_item_ids:
                counter += 1
                item_id = f"auto_section_slot_{counter}"
            used_item_ids.add(item_id)
            filled.append(
                StructureLlmItem(
                    item_id=item_id,
                    action="add",
                    parent_item_id=category.item_id,
                    slot_id=slot.slot_id,
                    text=None,
                    source_item_ids=[],
                )
            )
    return filled


def _drop_empty_new_section_subtrees(
    items: list[StructureLlmItem],
) -> list[StructureLlmItem]:
    """최종 배정 뒤 실제 내용이 전혀 없는 신규 카테고리 서브트리를 제거한다.

    여러 파일 배치를 처리하는 동안 모델이 관련 가능성이 있는 카테고리를 먼저
    만들었지만 끝까지 어떤 원문도 배정하지 않는 경우가 있다. 중간에는 뒤 배치가
    채울 수 있으므로 유지하고, 모든 배치가 끝난 뒤 text/source가 없는 서브트리만
    제거한다.
    """
    children_by_parent: dict[str, list[StructureLlmItem]] = {}
    for item in items:
        if item.parent_item_id:
            children_by_parent.setdefault(item.parent_item_id, []).append(item)

    def subtree_ids(root: StructureLlmItem) -> set[str]:
        collected: set[str] = set()
        pending = [root]
        while pending:
            current = pending.pop()
            if current.item_id in collected:
                continue
            collected.add(current.item_id)
            pending.extend(children_by_parent.get(current.item_id, []))
        return collected

    by_id = {item.item_id: item for item in items}
    drop: set[str] = set()
    for category in items:
        if category.section_kind is None:
            continue
        candidate_ids = subtree_ids(category)
        has_content = any(
            by_id[item_id].text is not None or bool(by_id[item_id].source_item_ids)
            for item_id in candidate_ids
        )
        if not has_content:
            drop.update(candidate_ids)

    if not drop:
        return items
    return [item for item in items if item.item_id not in drop]


def _order_parents_before_children(
    items: list[StructureLlmItem],
) -> list[StructureLlmItem]:
    """신규 add 부모가 자식보다 먼저 오도록 안정적으로 위상 정렬한다.

    구조 보정은 카테고리·앵커를 필요해진 시점에 목록 끝에 추가한다. 그러면
    앞에 있던 level 5 자식이 뒤에서 생성된 부모를 가리킬 수 있다. 구조 자체는
    유효하지만 commit operation 계약은 ``parent_item_id``가 앞선 item만
    가리키도록 요구하므로 validate가 구조화를 불필요하게 다시 실행했다.

    원래 순서를 가능한 한 유지하며 부모 의존성이 충족된 item부터 내보낸다.
    순환이나 존재하지 않는 부모는 임의로 고치지 않고 마지막에 원래 순서로
    남겨 이후 검증이 정확한 계약 위반으로 처리하게 한다.
    """
    pending = list(items)
    ordered: list[StructureLlmItem] = []
    emitted: set[str] = set()
    all_item_ids = {item.item_id for item in items}

    while pending:
        ready = [
            item
            for item in pending
            if item.action != "add"
            or item.parent_item_id is None
            or item.parent_item_id in emitted
            or item.parent_item_id not in all_item_ids
        ]
        if not ready:
            ordered.extend(pending)
            break
        ready_ids = {item.item_id for item in ready}
        ordered.extend(ready)
        emitted.update(ready_ids)
        pending = [item for item in pending if item.item_id not in ready_ids]

    return ordered


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

    _validate_source_coverage(items, expected_text)

    for item in items:
        if item.action == "update":
            # LLM은 add만 낼 수 있다 — update는 결정론적 보정
            # (`_reuse_existing_filled_anchor`,
            # `_redirect_leaf_add_to_existing_empty_slot`)이 이미 있는 빈
            # 슬롯 블록을 add로 중복 생성하지 않고 그 블록 자신을 채우도록
            # 코드가 바꿔치기한 것만 여기 온다.
            if item.target_ref not in state.get("alias_to_block_id", {}):
                raise ValueError("선택 활동에 없는 target_ref를 사용할 수 없습니다.")
            if not item.source_item_ids and item.text is not None:
                raise ValueError("source_item_ids 없이 text를 만들 수 없습니다.")
            continue
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
    known_aliases = state.get("alias_to_block_id", {})
    reported_existing = [
        category
        for category in existing_categories
        if category.alias in known_aliases and category.alias != target_alias
    ]
    classified_sections = {category.section_kind for category in reported_existing}
    for item in items:
        if item.section_kind is not None and item.section_kind in classified_sections:
            alias = next(
                category.alias
                for category in reported_existing
                if category.section_kind == item.section_kind
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
    known_aliases = state.get("alias_to_block_id", {})
    anchor_alias_by_container = {
        category.alias: category.existing_anchor_alias
        for category in existing_categories
        if category.alias in known_aliases
        and category.existing_anchor_alias in known_aliases
        and category.alias != target_alias
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
    그 빈 슬롯을 채우려던 의도가 명백하다.

    **`add`로 앵커에 다시 붙이면 안 된다.** 빈 슬롯(예: `b_5`)은 이미
    실제 block_id를 가진 블록이다 — `add`는 항상 새 블록을 만들므로, 그
    슬롯을 `parent_ref`로 삼아 다시 add해도 `b_5`는 그대로 빈 채로 남고
    형제 블록만 하나 더 생긴다. 실제로 재현된 경우다: 에러 없이 통과는
    됐는데, 정작 화면에는 기존 빈 "목적" 블록은 그대로 비어 있고 새 블록이
    하나 더 생겼다. 그 빈 슬롯 자신의 별칭을 `target_ref`로 삼아 `update`로
    바꿔야 진짜로 그 블록을 채운다. 일치가 하나가 아니면(0개 또는 여러 개)
    모호해서 그대로 두고 이후 검증이 에러로 보고하게 한다.
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
        resolved: dict[str, str] = {}  # item_id -> 빈 슬롯 자신의 별칭(target_ref)
        unresolved = []
        used_slots: set[str] = set()
        for entry in filled:
            if entry.slot_id in known_by_slot:
                resolved[entry.item_id] = known_by_slot[entry.slot_id][0]
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
            _, (target_alias, _) = next(iter(remaining.items()))
            resolved[entry.item_id] = target_alias

        for entry_id, target_alias in resolved.items():
            current = by_id[entry_id]
            by_id[entry_id] = current.model_copy(
                update={
                    "action": "update",
                    "target_ref": target_alias,
                    "parent_ref": None,
                    "parent_item_id": None,
                    "slot_id": None,
                    "section_kind": None,
                    "after_ref": None,
                }
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


def _redirect_leaf_add_to_existing_empty_slot(
    items: list[StructureLlmItem], catalog: TemplateCatalog, state: ExperienceMapState
) -> list[StructureLlmItem]:
    """이미 있는 앵커에 슬롯을 새로 add할 때, 그 slot이 이미 빈 블록으로 있으면 update로 바꾼다.

    실제로 재현된 경우다(gap 질문에 답하는 흐름). 모델이 `existing_anchor_alias`를
    올바르게 골라 기존 앵커(예: `b_2`)에 직접 `add`로 level 5 슬롯을 붙였다
    — 앵커 자체는 정확히 재사용했다. 하지만 그 슬롯(예: "목적")은 이미
    빈 블록(`b_3`)으로 트리에 있었는데, `add`는 항상 새 블록을 만들므로
    기존 `b_3`는 그대로 빈 채 새 형제 블록이 하나 더 생겼다 — 화면에는
    "이미 있던 빈 블록을 안 채우고 새 블록을 또 만든" 것으로 보인다.
    `parent_ref`의 **직속 자식** 중 같은 slot_id를 가진 빈 블록이 있으면,
    그 블록 자신을 `target_ref`로 삼아 `update`로 바꾼다.
    """
    tree_lines = _parse_tree_lines(state.get("activity_tree_text") or "")
    placeholder_to_slot = _placeholder_to_slot_map(catalog)
    known_aliases = state.get("alias_to_block_id", {})

    result: list[StructureLlmItem] = []
    changed = False
    for item in items:
        if (
            item.action == "add"
            and item.parent_ref is not None
            and item.parent_ref in known_aliases
            and item.slot_id is not None
            and not _is_anchor_slot(item.slot_id, catalog)
            and (item.text or item.source_item_ids)
        ):
            direct_children = {
                slot_id: alias
                for slot_id, alias, parent_alias in _subtree_known_slots_with_parent(
                    tree_lines, item.parent_ref, placeholder_to_slot
                )
                if parent_alias == item.parent_ref
            }
            target_alias = direct_children.get(item.slot_id)
            if target_alias is not None:
                result.append(
                    item.model_copy(
                        update={
                            "action": "update",
                            "target_ref": target_alias,
                            "parent_ref": None,
                            "parent_item_id": None,
                            "slot_id": None,
                            "section_kind": None,
                            "after_ref": None,
                        }
                    )
                )
                changed = True
                continue
        result.append(item)
    return result if changed else items


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
        set(item.source_item_ids) & source_ids
        and item.action == "add"
        and item.parent_ref != anchor_alias
        for item in items
    )
    # update로 바뀐 item(`_reuse_existing_filled_anchor`,
    # `_redirect_leaf_add_to_existing_empty_slot`)은 이미 그 anchor
    # 서브트리 안에서 찾은 기존 빈 슬롯만 target_ref로 삼으므로 따로
    # parent_ref를 비교할 필요가 없다 — update는 parent_ref 자체가 없다.
    if not anchor_alias or offending:
        raise ValueError("new_child gap 답변은 anchor block 바로 아래에만 추가할 수 있습니다.")


def next_node(state: ExperienceMapState) -> str:
    """구조화 결과가 있으면 정제, 없으면 fallback으로 보낸다."""
    return "refine" if state.get("structured_items") else "fallback"
