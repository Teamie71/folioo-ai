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


_MIN_MEANINGFUL_SENTENCE_CHARS = 8
# 여러 item을 합쳐도 문장 길이 대비 이 비율 이상을 담아야 "커버됐다"고
# 본다. "성과: " 같은 짧은 라벨을 떼고 알맹이만 남기는 정상적인 경우도
# 있어 1.0에 가깝게 두지는 않는다 — 그래도 문장 대부분이 실제로 빠진
# 경우(원래 이 함수가 잡으려던 사고)는 이 문턱보다 한참 낮게 나온다.
_MIN_SENTENCE_COVERAGE_RATIO = 0.6


def _split_into_sentences(text: str) -> list[str]:
    """문장·불릿 경계로 원문을 나눈다. 누락 감지 전용이라 내용은 안 건드린다."""
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        # "1. 담당 업무" 같은 번호 목록의 점은 문장 끝이 아니다 — 여기서 자르면
        # "1."·"2."처럼 번호만 남은 무의미한 조각이 생겨 오탐을 낸다.
        if text[match.start()] == "." and re.search(r"(?:^|\s)\d+$", text[: match.start()]):
            continue
        chunk = text[start : match.end()].strip()
        if chunk:
            sentences.append(chunk)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _uncovered_sentences(raw_input: str, accepted: list[FilteredItem]) -> list[str]:
    """분류된 어느 item에도 안 담긴, 의미 있는 길이의 원문 문장을 찾는다.

    LLM이 반환한 조각이 원문에 있는지(`_traceable`)만 봐서는, 원문 일부를
    통째로 빠뜨려도 걸리지 않는다 — 실제로 재현된 경우다: "첫 문장입니다.
    둘째 문장입니다."를 입력했는데 LLM이 첫 문장만 반환하고
    `excluded_reasons`도 비운 채 `dropped=0`으로 정상 통과했다. 이 함수는
    원문을 문장 단위로 쪼개, 분류된 item(제목 등 이후 단계에서 걸러질 것
    포함) 어디에도 없는 문장을 찾는다. 너무 짧은 조각(공백·단독 기호 등)은
    노이즈로 보고 무시한다.
    """
    accepted_texts = [_normalize(item.text) for item in accepted if item.text.strip()]
    missing: list[str] = []
    for sentence in _split_into_sentences(raw_input):
        normalized = _normalize(sentence)
        if len(normalized) < _MIN_MEANINGFUL_SENTENCE_CHARS:
            continue
        if _is_sentence_covered(normalized, accepted_texts):
            continue
        missing.append(sentence)
    return missing


def _is_sentence_covered(normalized: str, accepted_texts: list[str]) -> bool:
    """문장이 분류된 item들로 충분히 커버됐는지 판단한다.

    문장 전체가 item 하나에 포함되면 완전 커버다. 그렇지 않으면, 문장 안에서
    발견되는 모든 item(fragment)의 위치를 표시해 합쳐서 커버 비율을 잰다 —
    글자수 상한(`_split_long_item`)이나 제목별로 쪼개진 여러 item이 한 문장을
    나눠 담는 게 정상 경로이기 때문이다(구두점 없는 긴 원문 한 덩어리를
    `_split_into_sentences`가 하나의 "문장"으로 묶어 낸다).

    이렇게 여러 item을 합쳐도, 아주 짧은 조각 하나가 우연히 부분 문자열이란
    이유만으로 훨씬 긴 문장 전체가 커버됐다고 오판하지는 않는다 — 합친 커버
    비율이 `_MIN_SENTENCE_COVERAGE_RATIO`를 넘어야 한다.
    """
    if not normalized:
        return True

    covered = bytearray(len(normalized))
    for candidate in accepted_texts:
        if not candidate:
            continue
        if normalized in candidate:
            return True
        position = normalized.find(candidate)
        while position != -1:
            for index in range(position, position + len(candidate)):
                covered[index] = 1
            position = normalized.find(candidate, position + 1)

    # 공백은 분모·분자 모두에서 뺀다. 여러 item을 이어 붙이면 item 사이
    # 경계였던 공백(원래는 줄바꿈)은 어느 item 텍스트에도 안 담겨 있어
    # 항상 커버 실패로 잡히는데, 이건 진짜 누락이 아니라 이어붙이기의
    # 부산물이다.
    content_positions = [index for index, char in enumerate(normalized) if not char.isspace()]
    if not content_positions:
        return True
    covered_content = sum(covered[index] for index in content_positions)
    coverage_ratio = covered_content / len(content_positions)
    return coverage_ratio >= _MIN_SENTENCE_COVERAGE_RATIO


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
        window = text[start : start + MAX_SOURCE_ITEM_CHARS]
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
    """제한 안에서 의미가 가장 덜 끊기는 마지막 경계를 반환한다.

    반환값은 항상 `limit` 이하여야 한다. `candidate`를 `limit`보다 길게 잡으면
    구분자·문장 경계가 그 여유분 끝에 걸렸을 때 `limit`을 넘는 위치를 돌려주게
    된다 — 실제로 있었던 버그다.
    """
    limit = min(MAX_SOURCE_ITEM_CHARS, len(window))
    candidate = window[:limit]

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

    if not result.excluded_reasons:
        # LLM이 "제외한 게 없다"고 자체 신고했는데도 원문에 분류되지 않은
        # 의미 있는 문장이 남아 있으면, 그건 제외가 아니라 누락이다 —
        # `_traceable`은 반환된 조각이 원문에 있는지만 보고 원문 전체가
        # 빠짐없이 분류됐는지는 보지 않아서 여태 못 잡았다. 헤딩 제거
        # 전(아직 원문 그대로인 상태) item 기준으로 검사해야 나중에
        # 코드가 걸러낼 문서 제목까지 오탐으로 잡지 않는다.
        missing = _uncovered_sentences(
            f"{user_message or ''}\n{extracted_text or ''}", gap_items + new_items
        )
        if missing:
            logger.warning(
                "content_filter: 원문 일부가 분류되지 않았습니다 (%d개 문장, 예: %r)",
                len(missing),
                missing[0][:80],
            )
            raise LlmError("입력 내용 일부를 분류하지 못했습니다.", failed_node="content_filter")

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
