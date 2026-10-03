"""이번 턴 커밋 내용을 기준으로 후속 보완 질문 후보를 고른다."""

import logging

from common.llm import get_experience_map_llm
from features.experience_map.config import get_settings
from features.experience_map.errors import LlmError
from features.experience_map.prompts.gap_analysis import (
    gap_analysis_prompt,
    render_anchor_aliases,
    render_commit_items,
)
from features.experience_map.schemas import GapOutput
from features.experience_map.state import ExperienceMapState

logger = logging.getLogger(__name__)

NO_GAP_MESSAGE = "더 정리하고 싶으신 내용이 있나요?"


async def analyze_gap(state: ExperienceMapState) -> ExperienceMapState:
    """이번 턴의 확정 commit item만으로 최대 하나의 gap 후보를 고른다.

    분석 실패는 `LlmError`로 올려 coordinator가 suggestion 이벤트만 생략할 수 있게
    한다. 이 노드는 graph의 자동 재시도 대상이 아니다.
    """
    updated = dict(state)
    updated["current_node"] = "gap_analysis"
    items = list(state.get("commit_items", []))
    anchors = _anchor_refs(items)
    if not items or not anchors:
        # LLM을 호출하지 않고 조기 리턴한다 — "LLM 실패로 gap이 없음"과
        # 구분되는 로그를 남겨야 QA에서 원인을 판별할 수 있다 (QA 2026-09-22 #1-c).
        logger.info(
            "gap_analysis: anchor 없어 스킵 (request_id=%s, commit_items=%d개, item_id 목록=%s)",
            state.get("request_id"),
            len(items),
            [item.get("item_id") for item in items],
        )
        updated["gap_candidate"] = None
        updated["gap_message"] = NO_GAP_MESSAGE
        return updated  # type: ignore[return-value]

    try:
        # gap 분석은 결과 응답과 병렬로 돌지만 결과 응답을 먼저 내보내야
        # 하므로, 일반 LLM 노드(60초)가 아니라 전용 30초 제한(3-9, 2-4)을
        # 써야 한다 — 그래야 늦어도 제안이 결과보다 너무 오래 안 늦어진다.
        llm = get_experience_map_llm(timeout=get_settings().timeouts.gap)
        chain = gap_analysis_prompt | llm.with_structured_output(GapOutput)
        result: GapOutput = await chain.ainvoke(
            {
                "commit_items": render_commit_items(items),
                "anchor_aliases": render_anchor_aliases(anchors),
            }
        )
        result = _normalize_message(result)
        _validate_output(result, anchors)
    except LlmError:
        raise
    except Exception as exc:
        logger.exception("gap_analysis: gap 분석 실패")
        raise LlmError("후속 보완 질문을 만들지 못했습니다.", failed_node="gap_analysis") from exc

    if result.gap is None:
        updated["gap_candidate"] = None
        updated["gap_message"] = NO_GAP_MESSAGE
    else:
        updated["gap_candidate"] = result.gap.model_dump()
        updated["gap_message"] = result.message.strip()
    return updated  # type: ignore[return-value]


def _anchor_refs(items: list[dict]) -> list[str]:
    """이번에 실제 내용이 커밋되는 operation의 item_id만 anchor 후보로 남긴다.

    add와 update 모두 메인 서버의 ``applied`` 결과에서 실제 block_id로 바뀐다.
    부모 별칭을 대신 쓰면 새 블록의 부족한 정보를 부모 블록에 덧붙이는 잘못된
    gap이 생기므로, 방금 반영된 내용 자체를 기준으로 제한한다.
    """
    candidates: list[str] = []
    for item in items:
        item_id = item.get("item_id")
        text = item.get("text")
        if (
            isinstance(item_id, str)
            and item_id
            and isinstance(text, str)
            and text.strip()
            and item_id not in candidates
        ):
            candidates.append(item_id)
    return candidates


def _normalize_message(result: GapOutput) -> GapOutput:
    """질문 앞뒤 군더더기를 떼고 첫 번째 질문 한 문장만 남긴다.

    모델이 gap은 맞게 골라 놓고 "…했네요. 어떤 방법을 썼나요?"처럼 설명 문장을
    붙이거나 질문을 두 개 이어 쓰면, 형식 검사 하나 때문에 제안 전체가 버려져
    사용자에게 보완 질문이 아예 나가지 않았다(로컬 재현). 첫 질문 문장이 있으면
    그 문장만 쓴다.
    """
    if result.gap is None:
        return result
    message = " ".join(result.message.split())
    end = message.find("?")
    if end < 0:
        if not _is_single_request_sentence(message):
            logger.warning("gap_analysis: 질문 형식이 아닌 제안 문구 (message=%r)", result.message)
        return result.model_copy(update={"message": message})
    start = max(message.rfind(mark, 0, end) for mark in (". ", "! "))
    question = message[start + 2 if start >= 0 else 0 : end + 1].strip()
    if question != result.message.strip():
        logger.info(
            "gap_analysis: 제안 문구를 질문 한 문장으로 줄입니다 (%r -> %r)",
            result.message,
            question,
        )
    return result.model_copy(update={"message": question})


def _validate_output(result: GapOutput, anchors: list[str]) -> None:
    """gap 하나·허용 별칭·질문 형식을 강제한다."""
    if result.gap is None:
        return
    if result.gap.anchor_ref not in anchors:
        raise ValueError("이번에 내용이 커밋된 블록이 아닌 gap 기준입니다.")
    message = result.message.strip()
    if not message or "\n" in message:
        raise ValueError("gap 제안은 물음표로 끝나는 한 문장이어야 합니다.")
    is_question = message.count("?") == 1 and message.endswith("?")
    # "…어떤 기준을 사용했는지 설명해 주세요."처럼 한 문장 요청도 사용자가 바로 답할 수
    # 있는 질문이다. 이것까지 거부하면 제안 전체가 사라졌다(로컬 재현).
    is_request = "?" not in message and _is_single_request_sentence(message)
    if not (is_question or is_request):
        raise ValueError("gap 제안은 물음표로 끝나는 한 문장이어야 합니다.")


def _is_single_request_sentence(message: str) -> bool:
    """ "…해 주세요." 같은 한 문장 요청인지 본다."""
    body = message.rstrip(".").rstrip()
    return body.endswith(("주세요", "주시겠어요", "주실 수 있나요")) and not any(
        mark in body for mark in (". ", "! ")
    )


__all__ = ["NO_GAP_MESSAGE", "analyze_gap"]
