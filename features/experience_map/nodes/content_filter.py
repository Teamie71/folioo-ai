"""반영 내용 필터링 노드 (에이전트 문서 5-3)

입력 전체를 세 가지로 나누고 후속 노드를 정한다.

- 활성 gap 답변
- 새로 반영할 내용
- 반영 제외 (폐기)

**LLM 출력을 그대로 믿지 않는다.** 두 가지를 코드로 강제한다.

1. **원문 역추적** — 모든 조각이 입력에 실제로 있는 문장이어야 한다. 없으면
   그 조각만 버린다. 여기서 지어낸 수치가 통과하면 이후 노드는 그것을 사실로
   다룬다.
2. **gap 없으면 gap 답변도 없다** — 활성 gap이 없는데 gap 답변이 오면 새 내용으로
   옮긴다. 사용자 입력이라는 사실은 변하지 않으므로 버리지는 않는다.
"""

import logging
import re

from common.llm import get_experience_map_llm
from features.experience_map.config import get_settings
from features.experience_map.errors import LlmError
from features.experience_map.prompts.content_filter import (
    build_existing_map_section,
    build_file_section,
    build_gap_section,
    build_message_section,
    content_filter_prompt,
)
from features.experience_map.schemas import ContentFilterOutput, FilteredItem
from features.experience_map.state import ExperienceMapState

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """공백 차이를 흡수한다. LLM이 줄바꿈·들여쓰기를 다듬는 것까지 막지는 않는다."""
    return _WHITESPACE.sub(" ", text).strip()


def _traceable(item: FilteredItem, haystack: str) -> bool:
    """조각이 원문에 실제로 있는지 확인한다."""
    text = _normalize(item.text)
    return bool(text) and text in haystack


async def filter_content(state: ExperienceMapState) -> ExperienceMapState:
    """입력을 분류하고 `gap_answer_items`·`new_items`·`excluded_reasons` 를 채운다.

    Raises:
        LlmError: LLM 호출 실패 (자동 재시도 대상)
    """
    updated = dict(state)
    updated["current_node"] = "content_filter"

    active_gap = state.get("active_gap")
    user_message = state.get("user_message")
    extracted_text = state.get("extracted_text")

    try:
        llm = get_experience_map_llm(timeout=get_settings().timeouts.llm)
        chain = content_filter_prompt | llm.with_structured_output(ContentFilterOutput)
        result: ContentFilterOutput = await chain.ainvoke(
            {
                "gap_section": build_gap_section(active_gap),
                "message_section": build_message_section(user_message),
                "file_section": build_file_section(extracted_text),
                "existing_map_section": build_existing_map_section(
                    _comparison_tree(state, user_message, extracted_text)
                ),
            }
        )
    except Exception as exc:
        logger.exception("content_filter: LLM 호출 실패")
        raise LlmError("입력을 분류하지 못했습니다.", failed_node="content_filter") from exc

    gap_items, new_items, dropped = _sanitize(
        result, active_gap=active_gap, user_message=user_message, extracted_text=extracted_text
    )

    updated["gap_answer_items"] = [item.model_dump() for item in gap_items]
    updated["new_items"] = [item.model_dump() for item in new_items]
    updated["excluded_reasons"] = list(result.excluded_reasons)

    if not gap_items and not new_items:
        # 반영할 것이 없다. fallback 으로 간다 (5-3).
        updated["fallback_reason"] = "nothing_to_apply"

    logger.info(
        "content_filter: gap %d · 새 내용 %d · 제외 %d · 역추적 실패 %d",
        len(gap_items),
        len(new_items),
        len(result.excluded_reasons),
        dropped,
    )
    return updated  # type: ignore[return-value]


def _comparison_tree(
    state: ExperienceMapState,
    user_message: str | None,
    extracted_text: str | None,
) -> str | None:
    """기존 맵 비교가 필요한 요청에 한해 관련 활동 트리를 반환한다.

    화면 context가 있으면 준비 단계에서 이미 해당 활동의 tree가 적용돼 있다.
    context가 없지만 기존 내용 제외를 명시한 요청은 모든 활동을 비교 대상으로
    제공한다. 평범한 입력에는 큰 맵 전체를 프롬프트에 싣지 않는다.
    """
    request_text = f"{user_message or ''}\n{extracted_text or ''}"
    comparison_requested = any(
        marker in request_text
        for marker in ("이미", "기존", "중복", "현재 활동", "해당 활동", "제외")
    )
    if not comparison_requested:
        return None

    current_tree = (state.get("activity_tree_text") or "").strip()
    if current_tree:
        return current_tree

    contexts = state.get("activity_contexts", {})
    trees = [
        str(context.get("tree_text") or "").strip()
        for context in contexts.values()
        if isinstance(context, dict) and str(context.get("tree_text") or "").strip()
    ]
    return "\n\n".join(trees) or None


def _sanitize(
    result: ContentFilterOutput,
    *,
    active_gap: dict | None,
    user_message: str | None,
    extracted_text: str | None,
) -> tuple[list[FilteredItem], list[FilteredItem], int]:
    """LLM 출력을 원문과 대조해 정리한다.

    Returns:
        `(gap 답변, 새 내용, 버린 조각 수)`
    """
    haystack = _normalize(f"{user_message or ''}\n{extracted_text or ''}")

    gap_items: list[FilteredItem] = []
    new_items: list[FilteredItem] = []
    dropped = 0
    seen: set[str] = set()

    def _accept(item: FilteredItem, bucket: list[FilteredItem]) -> None:
        nonlocal dropped
        if not _traceable(item, haystack):
            # 원문에 없는 문장이다. 지어낸 것이므로 버린다.
            logger.warning("content_filter: 원문에 없는 조각을 버립니다 (item_id=%s)", item.item_id)
            dropped += 1
            return
        key = _normalize(item.text)
        if key in seen:
            # 같은 문장이 두 목록에 들어왔다. 먼저 온 쪽만 남긴다.
            logger.warning("content_filter: 중복 조각을 버립니다 (item_id=%s)", item.item_id)
            dropped += 1
            return
        seen.add(key)
        bucket.append(item)

    has_gap = bool(active_gap and (active_gap.get("message") or "").strip())

    for item in result.gap_answer_items:
        if has_gap:
            _accept(item, gap_items)
        else:
            # 활성 gap 이 없는데 gap 답변으로 왔다. 사용자 입력인 것은 맞으므로
            # 버리지 않고 새 내용으로 옮긴다.
            logger.warning("content_filter: 활성 gap 이 없어 새 내용으로 옮깁니다")
            _accept(item, new_items)

    for item in result.new_items:
        _accept(item, new_items)

    return gap_items, new_items, dropped


def next_node(state: ExperienceMapState) -> str:
    """분류 결과로 다음 노드를 고른다 (5-3 후속 노드 분기).

    | 분류 결과 | 다음 |
    | --- | --- |
    | gap 답변만 | `active_gap.gap_type` 이 정한 노드 |
    | 새 내용만 | 구조화 |
    | 구조화가 필요한 gap 답변 + 새 내용 | 둘 다 구조화 |
    | 정제가 필요한 gap 답변 + 새 내용 | 새 내용은 구조화 (뒤에 정제가 합침) |
    | 반영 제외만 | fallback |
    """
    has_gap_answer = bool(state.get("gap_answer_items"))
    has_new = bool(state.get("new_items"))

    if not has_gap_answer and not has_new:
        return "fallback"

    if has_new:
        # 새 내용은 반드시 구조화를 거친다. gap 답변이 정제 대상이면 구조화 결과와
        # 함께 정제 노드에서 합쳐진다.
        return "structure"

    # gap 답변만 있다. 유형이 다음 노드를 정한다.
    gap_type = (state.get("active_gap") or {}).get("gap_type")
    return "refine" if gap_type == "extend_block" else "structure"
