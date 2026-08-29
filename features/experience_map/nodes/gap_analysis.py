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
    anchors = _anchor_aliases(items, state.get("alias_to_block_id", {}))
    if not items or not anchors:
        updated["gap_candidate"] = None
        updated["gap_message"] = NO_GAP_MESSAGE
        return updated  # type: ignore[return-value]

    try:
        llm = get_experience_map_llm(timeout=get_settings().timeouts.llm)
        chain = gap_analysis_prompt | llm.with_structured_output(GapOutput)
        result: GapOutput = await chain.ainvoke(
            {
                "commit_items": render_commit_items(items),
                "anchor_aliases": render_anchor_aliases(anchors),
            }
        )
        result = _reuse_committed_item_anchor(result, items, anchors)
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


def _reuse_committed_item_anchor(
    result: GapOutput, items: list[dict], anchors: list[str]
) -> GapOutput:
    """`anchor_ref`가 이번 커밋 item 자신의 item_id면, 그 item이 실제로 붙은 별칭으로 바꾼다.

    실제로 재현된 경우다. 이번 턴에 기존 앵커의 빈 슬롯을 막 채운 item을
    gap 질문 기준으로 삼고 싶어 하면서, 그 item의 `item_id`(예: `blk_1`)를
    `anchor_ref`에 썼다 — `anchor_ref`는 활동 트리의 기존 블록 별칭만
    가리킬 수 있는데, 방금 만든 item은 그런 별칭이 없다. 그 item이 실제로
    `parent_ref`/`target_ref`로 가리키는 기존 블록이 있으면, 물어보려던
    맥락은 명백하므로 코드가 그 별칭으로 바꿔 재시도 없이 통과시킨다.
    """
    if result.gap is None or result.gap.anchor_ref in anchors:
        return result

    by_item_id = {item.get("item_id"): item for item in items}
    committed = by_item_id.get(result.gap.anchor_ref)
    if committed is None:
        return result
    for field in ("parent_ref", "target_ref"):
        alias = committed.get(field)
        if isinstance(alias, str) and alias in anchors:
            return result.model_copy(
                update={"gap": result.gap.model_copy(update={"anchor_ref": alias})}
            )
    return result


def _anchor_aliases(items: list[dict], aliases: dict[str, str]) -> list[str]:
    """이번 operation이 직접 참조한 기존 블록만 질문 기준 후보로 남긴다."""
    candidates: list[str] = []
    for item in items:
        for field in ("parent_ref", "target_ref", "after_ref"):
            alias = item.get(field)
            if isinstance(alias, str) and alias in aliases and alias not in candidates:
                candidates.append(alias)
    return candidates


def _validate_output(result: GapOutput, anchors: list[str]) -> None:
    """gap 하나·허용 별칭·질문 형식을 강제한다."""
    if result.gap is None:
        return
    if result.gap.anchor_ref not in anchors:
        raise ValueError("이번 커밋과 직접 연결되지 않은 gap 기준 블록입니다.")
    message = result.message.strip()
    if not message or "\n" in message or message.count("?") != 1 or not message.endswith("?"):
        raise ValueError("gap 제안은 물음표로 끝나는 한 문장이어야 합니다.")


__all__ = ["NO_GAP_MESSAGE", "analyze_gap"]
