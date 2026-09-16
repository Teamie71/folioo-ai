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
_WHITESPACE = re.compile(r"\s+")

# 정제 결과가 원문과 다른 사실을 담고 있는지(한국어 단어 치환·삭제)는 숫자·영문
# 검사로는 못 잡는다 — 실제로 재현된 경우다: "로그 분석을 통해 결제 오류를
# 해결했다" → "고객 인터뷰를 통해 결제 오류 해결"처럼 원문에 없는 한국어
# 방법을 지어내도 통과했다. 완벽한 의미 대조는 LLM 판단 없이는 불가능하지만,
# 정제는 "명사종결·화살표 활용" 정도의 표현 변화만 허용하고 핵심 단어 자체는
# 살려야 한다(문서 5-6 정제 기준: "사실과 의미를 유지"). 공백을 뗀 문자
# bigram의 겹침 비율로 원문 핵심 단어가 얼마나 살아남았는지를 결정론적으로
# 어림한다 — 조사가 붙어 정확히 같은 단어로는 안 잡히는 한국어 특성에서도,
# 핵심 명사·어간의 bigram은 대체로 겹친다.
_MIN_BIGRAM_OVERLAP = 0.45
_MIN_BIGRAM_CHECK_LENGTH = 6


def _char_bigrams(text: str) -> list[str]:
    """공백을 뗀 문자열의 2글자 슬라이딩 윈도우를 만든다.

    영문은 대소문자를 구분하지 않는다 — "ui" → "UI" 같은 표기 정규화는
    새 사실이 아니다(영문 토큰 검사와 같은 원칙).
    """
    compact = _WHITESPACE.sub("", text).casefold()
    return [compact[i : i + 2] for i in range(len(compact) - 1)]


def _bigram_overlap_ratio(source: str, refined: str) -> float:
    """정제 결과에 원문 bigram이 얼마나 남아 있는지(0~1)를 계산한다."""
    source_bigrams = _char_bigrams(source)
    if len(source_bigrams) < _MIN_BIGRAM_CHECK_LENGTH:
        return 1.0  # 너무 짧으면 노이즈만 크므로 검사하지 않는다.
    refined_bigrams = set(_char_bigrams(refined))
    matched = sum(1 for bigram in source_bigrams if bigram in refined_bigrams)
    return matched / len(source_bigrams)


async def refine_text(state: ExperienceMapState) -> ExperienceMapState:
    """한 활동의 구조화 문장과 extend gap 답변을 한 번에 정제한다.

    validate가 refine만 지목한 재시도(`repair_target`이 전부 "refine")라면
    `structured_items`는 이번 루프에서 바뀌지 않았으므로, 지목되지 않은
    item은 지난 회차 결과를 그대로 재사용하고 지목된 item만 다시 LLM에
    보낸다(`_reusable_refined_item_ids`). validate가 structure까지 지목한
    회귀는 `structured_items` 자체가 바뀌었을 수 있어 전체를 다시 정제한다.

    Returns:
        `refined_items`와 필요 시 `gap_update_item`을 채운 state.

    Raises:
        LlmError: 활동 트리·anchor 원문을 찾지 못하거나 LLM 출력 계약이 깨진 경우
    """
    updated = dict(state)
    updated["current_node"] = "refine"
    candidates, gap_update = _build_candidates(state)
    refinable = [item for item in candidates if item.get("text") is not None]
    if not refinable:
        updated["fallback_reason"] = "nothing_to_apply"
        return updated  # type: ignore[return-value]
    if not (state.get("activity_tree_text") or "").strip():
        raise LlmError("선택한 활동의 상세 구조를 불러오지 못했습니다.", failed_node="refine")

    previous_refined = {
        item["item_id"]: item for item in state.get("refined_items", []) if item.get("item_id")
    }
    reusable_ids = _reusable_refined_item_ids(state, candidates, previous_refined)
    to_refine = [item for item in refinable if item["item_id"] not in reusable_ids]

    try:
        refined_content: dict[str, RefinedItem] = {}
        if to_refine:
            llm = get_experience_map_llm(timeout=get_settings().timeouts.llm)
            chain = refine_prompt | llm.with_structured_output(RefinementOutput)
            result: RefinementOutput = await chain.ainvoke(
                {
                    "activity_tree": state["activity_tree_text"],
                    "items": render_refinement_items(to_refine),
                }
            )
            refined_content = {
                item.item_id: item for item in _validate_output(result.items, to_refine)
            }
        # 빈 템플릿 슬롯은 LLM에 보내지 않는다. 모델이 null 자리를 임의로
        # 채우는 실패를 원천 차단하면서 validate 단계가 요구하는 전체 item
        # 집합은 코드가 결정론적으로 복원한다. 재사용 가능한 item은 지난
        # 회차의 검증된 결과를 그대로 쓴다.
        refined_items = [
            _refined_result_for(item, refined_content, previous_refined, reusable_ids)
            for item in candidates
        ]
    except LlmError:
        raise
    except Exception as exc:
        logger.exception("refine: 문장 정제 실패")
        raise LlmError("문장을 정제하지 못했습니다.", failed_node="refine") from exc

    updated["refined_items"] = [item.model_dump() for item in refined_items]
    updated["gap_update_item"] = gap_update
    logger.info(
        "refine: 활동 단위 %d개 문장 정제 (재사용 %d개)",
        len(to_refine),
        len(reusable_ids),
    )
    return updated  # type: ignore[return-value]


def _reusable_refined_item_ids(
    state: ExperienceMapState,
    candidates: list[dict],
    previous_refined: dict[str, dict],
) -> set[str]:
    """지난 회차 정제 결과를 그대로 재사용할 수 있는 item_id 집합을 돌려준다.

    validate → refine 회귀에서만, 그리고 이번 회귀가 정확히 refine만
    지목했을 때만(즉 structure는 이번 루프에서 실행되지 않아
    `structured_items`가 안 바뀌었을 때만) 재사용한다. 그 외(첫 정제,
    structure까지 지목된 회귀)는 빈 집합을 돌려줘 항상 전체를 다시
    정제하게 한다 — `structured_items`가 바뀌었을 수 있는데 지난 회차
    item_id를 기준으로 재사용하면 새로 생긴·없어진 블록을 놓친다.
    """
    errors = state.get("validation_errors") or []
    if not errors or any(error["repair_target"] != "refine" for error in errors):
        return set()
    flagged_ids = {error["item_id"] for error in errors}
    candidate_ids = {item["item_id"] for item in candidates}
    return {
        item_id
        for item_id in candidate_ids
        if item_id not in flagged_ids and item_id in previous_refined
    }


def _refined_result_for(
    item: dict,
    refined_content: dict[str, RefinedItem],
    previous_refined: dict[str, dict],
    reusable_ids: set[str],
) -> RefinedItem:
    """candidate 하나의 최종 정제 결과를 고른다: 새로 정제 > 재사용 > 빈 슬롯."""
    item_id = item["item_id"]
    if item_id in refined_content:
        return refined_content[item_id]
    if item_id in reusable_ids:
        return RefinedItem.model_validate(previous_refined[item_id])
    return RefinedItem(item_id=item_id, refined_text=None)


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
    """정제 결과를 원문에 대조하고 잘못된 item은 원문으로 안전하게 복구한다."""
    expected = {item["item_id"]: item["text"] for item in candidates}
    grouped: dict[str, list[RefinedItem]] = {}
    for item in output:
        if item.item_id in expected:
            grouped.setdefault(item.item_id, []).append(item)

    validated: list[RefinedItem] = []
    for item_id, source_text in expected.items():
        matches = grouped.get(item_id, [])
        refined = matches[0].refined_text if len(matches) == 1 else None
        fallback_reason: str | None = None
        if not isinstance(source_text, str):
            validated.append(RefinedItem(item_id=item_id, refined_text=None))
            continue
        if not refined or not refined.strip():
            fallback_reason = "정제 결과 누락·중복 또는 빈 문자열"
        elif len(refined.strip()) > MAX_CONTENT_LENGTH:
            fallback_reason = "최대 글자 수 초과"
        else:
            source_numbers = _NUMBER.findall(source_text)
            refined_numbers = _NUMBER.findall(refined)
            if not set(refined_numbers).issubset(source_numbers):
                fallback_reason = "원문에 없는 수치 추가"
            elif not set(source_numbers).issubset(refined_numbers):
                # 리뷰로 재현된 경우다 — "8분→3초로 단축, 2,400건 처리"의
                # 수치를 전부 지우고 "성능 개선"으로만 뭉뚱그려도 이전
                # 검사(추가 여부만 확인)로는 안 걸렸다. 정제는 없는 수치를
                # 만들면 안 되는 것만큼, 있는 수치를 지워서도 안 된다.
                fallback_reason = "원문 수치를 정제 결과에서 삭제"
        source_english_tokens = {token.casefold() for token in _ENGLISH_TOKEN.findall(source_text)}
        refined_english_tokens = {
            token.casefold() for token in _ENGLISH_TOKEN.findall(refined or "")
        }
        if fallback_reason is None and not refined_english_tokens.issubset(source_english_tokens):
            fallback_reason = "원문에 없는 영문 고유명사 추가"
        if (
            fallback_reason is None
            and refined
            and _bigram_overlap_ratio(source_text, refined) < _MIN_BIGRAM_OVERLAP
        ):
            # 숫자·영문 검사를 통과해도 한국어 핵심 단어 자체가 다른 내용으로
            # 바뀌었을 수 있다 — 리뷰로 재현된 경우다: "로그 분석을 통해
            # 결제 오류를 해결했다" → "고객 인터뷰를 통해 결제 오류 해결"
            # 처럼 원문에 없는 방법을 지어내도 숫자·영문 검사만으로는 못
            # 잡았다.
            fallback_reason = "원문과 핵심 단어가 다른 정제 결과"
        if fallback_reason is not None:
            logger.warning("refine: %s은 원문을 유지합니다 (%s)", item_id, fallback_reason)
            refined = source_text
        validated.append(RefinedItem(item_id=item_id, refined_text=refined))
    return validated


def next_node(state: ExperienceMapState) -> str:
    """정제 결과가 있으면 validate, 없으면 fallback으로 보낸다."""
    return "validate" if state.get("refined_items") else "fallback"
