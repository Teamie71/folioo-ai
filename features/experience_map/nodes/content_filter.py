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
from features.experience_map.config import MAX_SOURCE_ITEM_CHARS, get_settings
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
_SENTENCE_END = re.compile(r"[.!?。！？][\"'”’)}\]]*\s+")
_HEADING_PREFIX = re.compile(r"^(?:[#>*_`-]+\s*)?(?:\d+[.)]\s*)?")
_STRUCTURAL_HEADINGS = frozenset(
    {
        "담당 업무",
        "문제 해결 경험",
        "상황",
        "상황 설명",
        "원인 분석",
        "해결 과정",
        "결과",
        "주요 성과",
        "배운 점",
    }
)


def _normalize(text: str) -> str:
    """공백 차이를 흡수한다. LLM이 줄바꿈·들여쓰기를 다듬는 것까지 막지는 않는다."""
    return _WHITESPACE.sub(" ", text).strip()


def _traceable(item: FilteredItem, haystack: str) -> bool:
    """조각이 원문에 실제로 있는지 확인한다."""
    text = _normalize(item.text)
    return bool(text) and text in haystack


def _is_structural_heading(item: FilteredItem) -> bool:
    """문서 구조를 알리는 제목만 있는 item인지 판별한다.

    제목은 뒤 내용의 슬롯을 판단하는 문맥이지, 사용자 경험 내용은
    아니다. 다만 "문제 해결 경험 — 결제 승인 API 응답 지연"처럼 제목 뒤에
    실제 에피소드 요약이 있으면 요약 블록으로 쓸 수 있으므로 남겨 둔다.
    """
    if item.source != "file":
        return False
    heading = _HEADING_PREFIX.sub("", _normalize(item.text)).strip(" *_`:：")
    if heading in _STRUCTURAL_HEADINGS:
        return True
    return bool(re.fullmatch(r"경력\s*정리\s*메모(?:\s*[—:\-]\s*.+)?", heading))


def _drop_structural_headings(items: list[FilteredItem]) -> tuple[list[FilteredItem], int]:
    """내용으로 오인된 PDF·문서의 단독 제목을 제외한다."""
    kept = [item for item in items if not _is_structural_heading(item)]
    return kept, len(items) - len(kept)


def _restore_meaningful_heading_summaries(
    items: list[FilteredItem], extracted_text: str | None
) -> list[FilteredItem]:
    """문서 제목에 포함된 구체적인 에피소드 요약을 복구한다.

    content filter LLM이 "문제 해결 경험 — 결제 승인 API 응답 지연"
    전체를 제목이라고 제외하면 문제해결 SUMMARY가 빈다. 구분자 뒤의
    요약은 원문에 실제로 있는 부분 문자열이므로 추가 생성 없이 복구할
    수 있다.
    """
    if not extracted_text:
        return items
    existing = [_normalize(item.text) for item in items]
    restored = list(items)
    counter = 0
    used_ids = {item.item_id for item in items}
    for raw_line in extracted_text.splitlines():
        line = _HEADING_PREFIX.sub("", _normalize(raw_line)).strip(" *_`:：")
        match = re.match(r"^문제\s*해결\s*경험\s*[—:\-]\s*(.+)$", line)
        if match is None:
            continue
        summary = match.group(1).strip()
        if not summary or any(summary in text for text in existing):
            continue
        counter += 1
        item_id = f"it_heading_{counter}"
        while item_id in used_ids:
            counter += 1
            item_id = f"it_heading_{counter}"
        used_ids.add(item_id)
        restored.append(FilteredItem(item_id=item_id, text=summary, source="file"))
        existing.append(_normalize(summary))
    return restored


def _split_long_item(item: FilteredItem) -> list[FilteredItem]:
    """긴 원문 item을 수정 없이 구조화 가능한 크기로 나눈다.

    PDF OCR은 한 페이지 전체를 한 문단으로 반환할 수 있다. item 개수만 제한하면
    그 한 항목이 그대로 구조화 프롬프트와 JSON 응답을 비대하게 만든다. 문단,
    줄바꿈, 문장, 공백 순서로 가까운 경계를 찾고, 경계가 전혀 없을 때만 글자 수로
    자른다. 경계의 공백 외에는 원문 문자를 추가하거나 고치지 않는다.
    """
    text = item.text.strip()
    sentence_ends = [
        match
        for match in _SENTENCE_END.finditer(text)
        # "1. 담당 업무" 같은 번호 목록의 점은 문장 끝이 아니다.
        if not (
            text[match.start()] == "."
            and re.search(r"(?:^|\s)\d+$", text[: match.start()]) is not None
        )
    ]
    if sentence_ends:
        sentence_chunks: list[str] = []
        start = 0
        for match in sentence_ends:
            chunk = text[start : match.end()].strip()
            if chunk:
                sentence_chunks.append(chunk)
            start = match.end()
        tail = text[start:].strip()
        if tail:
            sentence_chunks.append(tail)
        if len(sentence_chunks) > 1:
            split: list[FilteredItem] = []
            for index, chunk in enumerate(sentence_chunks, start=1):
                child = item.model_copy(
                    update={"item_id": f"{item.item_id}_{index}", "text": chunk}
                )
                split.extend(_split_long_item(child))
            return split

    if len(text) <= MAX_SOURCE_ITEM_CHARS:
        return [item]

    chunks: list[str] = []
    start = 0
    while len(text) - start > MAX_SOURCE_ITEM_CHARS:
        window = text[start : start + MAX_SOURCE_ITEM_CHARS + 1]
        cut = _preferred_split_position(window)
        if cut <= 0:
            cut = MAX_SOURCE_ITEM_CHARS

        chunk = text[start : start + cut].strip()
        if chunk:
            chunks.append(chunk)
        start += cut
        while start < len(text) and text[start].isspace():
            start += 1

    tail = text[start:].strip()
    if tail:
        chunks.append(tail)

    return [
        item.model_copy(update={"item_id": f"{item.item_id}_{index}", "text": chunk})
        for index, chunk in enumerate(chunks, start=1)
    ]


def _preferred_split_position(window: str) -> int:
    """제한 안에서 의미가 가장 덜 끊기는 마지막 경계를 반환한다."""
    limit = min(MAX_SOURCE_ITEM_CHARS, len(window))
    candidate = window[: limit + 1]

    for separator in ("\n\n", "\n"):
        position = candidate.rfind(separator)
        if position > 0:
            return position + len(separator)

    sentence_ends = list(_SENTENCE_END.finditer(candidate))
    if sentence_ends:
        return sentence_ends[-1].end()

    for index in range(limit, 0, -1):
        if candidate[index - 1].isspace():
            return index
    return limit


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

    gap_items, dropped_gap_headings = _drop_structural_headings(gap_items)
    new_items, dropped_new_headings = _drop_structural_headings(new_items)
    new_items = _restore_meaningful_heading_summaries(new_items, extracted_text)
    dropped_headings = dropped_gap_headings + dropped_new_headings
    excluded_reasons = list(result.excluded_reasons)
    if dropped_headings:
        excluded_reasons.append("문서 제목·구획 제목")

    updated["gap_answer_items"] = [item.model_dump() for item in gap_items]
    updated["new_items"] = [item.model_dump() for item in new_items]
    updated["excluded_reasons"] = excluded_reasons

    if not gap_items and not new_items:
        # 반영할 것이 없다. fallback 으로 간다 (5-3).
        updated["fallback_reason"] = "nothing_to_apply"

    logger.info(
        "content_filter: gap %d · 새 내용 %d · 제외 %d · 역추적 실패 %d",
        len(gap_items),
        len(new_items),
        len(result.excluded_reasons),
        dropped + dropped_headings,
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
        bucket.extend(_split_long_item(item))

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
