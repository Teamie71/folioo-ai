"""문장 정제 노드 (에이전트 문서 5-6)."""

import logging
import re

from common.llm import get_experience_map_llm
from features.experience_map.config import MAX_CONTENT_LENGTH, get_settings
from features.experience_map.errors import LlmError
from features.experience_map.prompts.refine import refine_prompt, render_refinement_items
from features.experience_map.schemas import RefinedItem, RefinementOutput
from features.experience_map.state import ExperienceMapState

logger = logging.getLogger(__name__)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?(?:%|년|개월|명|회|건|개)?")
_ENGLISH_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9._+-]*")


async def refine_text(state: ExperienceMapState) -> ExperienceMapState:
    """한 활동의 구조화 문장과 extend gap 답변을 한 번에 정제한다.

    Returns:
        `refined_items`와 필요 시 `gap_update_item`을 채운 state.

    Raises:
        LlmError: 활동 트리·anchor 원문을 찾지 못하거나 LLM 출력 계약이 깨진 경우
    """
    updated = dict(state)
    updated["current_node"] = "refine"
    candidates, gap_update = _build_candidates(state)
    if not candidates:
        updated["fallback_reason"] = "nothing_to_apply"
        return updated  # type: ignore[return-value]
    if not (state.get("activity_tree_text") or "").strip():
        raise LlmError("선택한 활동의 상세 구조를 불러오지 못했습니다.", failed_node="refine")

    try:
        llm = get_experience_map_llm(timeout=get_settings().timeouts.llm)
        chain = refine_prompt | llm.with_structured_output(RefinementOutput)
        result: RefinementOutput = await chain.ainvoke(
            {
                "activity_tree": state["activity_tree_text"],
                "items": render_refinement_items(candidates),
            }
        )
        refined_items = _validate_output(result.items, candidates)
    except LlmError:
        raise
    except Exception as exc:
        logger.exception("refine: 문장 정제 실패")
        raise LlmError("문장을 정제하지 못했습니다.", failed_node="refine") from exc

    updated["refined_items"] = [item.model_dump() for item in refined_items]
    updated["gap_update_item"] = gap_update
    logger.info("refine: 활동 단위 %d개 문장 정제", len(candidates))
    return updated  # type: ignore[return-value]


def _build_candidates(state: ExperienceMapState) -> tuple[list[dict], dict | None]:
    """구조화 item과 extend gap update 후보를 한 활동 입력으로 합친다."""
    candidates = [
        {"item_id": item["item_id"], "text": item.get("text")}
        for item in state.get("structured_items", [])
    ]
    gap_update = _build_gap_update(state)
    if gap_update is not None:
        candidates.append({"item_id": gap_update["item_id"], "text": gap_update["text"]})
    return candidates, gap_update


def _build_gap_update(state: ExperienceMapState) -> dict | None:
    """extend gap 답변을 기존 anchor 내용과 결합한 update metadata로 만든다."""
    active_gap = state.get("active_gap") or {}
    answers = state.get("gap_answer_items", [])
    if active_gap.get("gap_type") != "extend_block" or not answers:
        return None

    anchor_id = active_gap.get("anchor_block_id")
    if not isinstance(anchor_id, str):
        raise LlmError("gap 기준 블록을 확인하지 못했습니다.", failed_node="refine")
    anchor_alias = next(
        (
            alias
            for alias, block_id in state.get("alias_to_block_id", {}).items()
            if block_id == anchor_id
        ),
        None,
    )
    existing_text = state.get("block_id_to_content", {}).get(anchor_id)
    if not anchor_alias or not existing_text:
        raise LlmError("gap 기준 블록의 기존 내용을 확인하지 못했습니다.", failed_node="refine")

    answer_text = "\n".join(str(item.get("text", "")).strip() for item in answers).strip()
    if not answer_text:
        raise LlmError("정제할 gap 답변이 비어 있습니다.", failed_node="refine")
    return {
        "item_id": f"gap_update:{anchor_id}",
        "action": "update",
        "target_ref": anchor_alias,
        "text": f"{existing_text}\n{answer_text}",
    }


def _validate_output(output: list[RefinedItem], candidates: list[dict]) -> list[RefinedItem]:
    """item 집합·빈 슬롯·새 수치를 검증해 hallucination을 차단한다."""
    expected = {item["item_id"]: item["text"] for item in candidates}
    actual = {item.item_id: item for item in output}
    if len(actual) != len(output) or set(actual) != set(expected):
        raise ValueError("정제 전후 item_id 집합이 일치하지 않습니다.")

    for item_id, source_text in expected.items():
        refined = actual[item_id].refined_text
        if source_text is None:
            if refined is not None:
                raise ValueError("템플릿 빈 슬롯은 정제할 수 없습니다.")
            continue
        if not refined or not refined.strip():
            raise ValueError("내용이 있는 항목의 정제 결과는 비어 있을 수 없습니다.")
        if len(refined.strip()) > MAX_CONTENT_LENGTH:
            raise ValueError("정제 결과가 최대 글자 수를 넘었습니다.")
        if not set(_NUMBER.findall(refined)).issubset(_NUMBER.findall(source_text)):
            raise ValueError("원문에 없는 수치가 정제 결과에 포함됐습니다.")
        source_english_tokens = {token.casefold() for token in _ENGLISH_TOKEN.findall(source_text)}
        refined_english_tokens = {token.casefold() for token in _ENGLISH_TOKEN.findall(refined)}
        if not refined_english_tokens.issubset(source_english_tokens):
            raise ValueError("원문에 없는 영문 고유명사가 정제 결과에 포함됐습니다.")
    return output


def next_node(state: ExperienceMapState) -> str:
    """정제 결과가 있으면 validate, 없으면 fallback으로 보낸다."""
    return "validate" if state.get("refined_items") else "fallback"
