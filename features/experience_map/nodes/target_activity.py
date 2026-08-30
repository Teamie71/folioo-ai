"""대상 활동 선택 노드 (에이전트 문서 5-4).

선택 우선순위는 활성 gap의 anchor, 화면 context, 사용자 메시지·파일 추출문
순이다. anchor와 context는 LLM 판단이 아닌 서버가 보유한 별칭 소유권 매핑으로
검증한다. 그 어느 경우에도 한 활동으로 특정할 수 없으면 커밋 경로로 보내지
않는다.

**gap anchor가 화면 context보다 먼저다.** 실제로 재현된 경우다 — 사용자가
gap 질문에 답할 때 화면은 이미 다른 활동으로 넘어가 있을 수 있는데(예:
질문을 받은 뒤 다른 활동을 둘러보다가 답변), 화면 context를 먼저 적용하면
gap 답변이 엉뚱한 활동으로 배정된다. `extend_block`이면 그 활동의
`alias_to_block_id`에 gap anchor의 실제 block_id가 없어 이후 `refine`이
"gap 기준 블록의 기존 내용을 확인하지 못했습니다"로 실패하고, `new_child_block`
이면 조용히 다른 활동 아래 새 블록이 생긴다.
"""

import logging

from common.llm import get_experience_map_llm
from features.experience_map.config import get_settings
from features.experience_map.errors import LlmError
from features.experience_map.prompts.target_activity import (
    render_activity_outline,
    target_activity_prompt,
)
from features.experience_map.schemas import TargetActivityOutput
from features.experience_map.state import ExperienceMapState, OutlineNode

logger = logging.getLogger(__name__)


def _activity_candidates(outline: list[OutlineNode]) -> dict[str, str]:
    """outline에서 level 2 활동의 별칭과 제목만 추린다."""
    candidates: dict[str, str] = {}

    def _visit(nodes: list[OutlineNode]) -> None:
        for node in nodes:
            if node.get("level") == 2:
                alias = node.get("alias", "")
                title = node.get("title", "").strip()
                if alias.startswith("exp_") and title:
                    candidates[alias] = title
            children = node.get("children")
            if children:
                _visit(children)

    _visit(outline)
    return candidates


def _context_alias(
    context_experience_id: str | None,
    *,
    candidates: dict[str, str],
    alias_to_block_id: dict[str, str],
) -> str | None:
    """유효한 화면 context ID를 활동 별칭으로 변환한다."""
    if not context_experience_id:
        return None
    for alias in candidates:
        if alias_to_block_id.get(alias) == context_experience_id:
            return alias
    return None


def _gap_anchor_alias(
    state: ExperienceMapState,
    *,
    candidates: dict[str, str],
) -> str | None:
    """활성 gap의 anchor가 속한 활동을 검증해 반환한다."""
    active_gap = state.get("active_gap") or {}
    anchor_block_id = active_gap.get("anchor_block_id")
    if not isinstance(anchor_block_id, str):
        return None

    alias = state.get("block_id_to_experience_alias", {}).get(anchor_block_id)
    return alias if alias in candidates else None


def _has_gap_answer(state: ExperienceMapState) -> bool:
    """이번 요청에 활성 gap에 대한 답변이 실제로 분류됐는지 확인한다."""
    return bool(state.get("active_gap") and state.get("gap_answer_items"))


def _fallback(updated: dict, reason: str) -> ExperienceMapState:
    """대상을 못 정했을 때 커밋 경로를 끊는다."""
    updated["target_experience_alias"] = None
    updated["fallback_reason"] = reason
    return updated  # type: ignore[return-value]


async def select_target_activity(state: ExperienceMapState) -> ExperienceMapState:
    """이번 요청이 수정할 하나의 활동을 선택한다.

    활성 gap 답변이 있으면 anchor가 속한 활동으로 고정한다 — 화면 context보다
    우선이다(모듈 docstring 참고). 그다음이 유효한 화면 context다. 나머지는
    LLM이 메시지와 파일 추출문을 함께 보고 전체 outline의 활동 별칭
    화이트리스트 중 하나를 고르게 하되, 화이트리스트 밖 결과와 불명확한
    결과는 fallback 처리한다.

    Raises:
        LlmError: 메시지 기반 선택을 위한 LLM 호출에 실패한 경우
    """
    updated = dict(state)
    updated["current_node"] = "target_activity"
    candidates = _activity_candidates(state.get("outline", []))

    if _has_gap_answer(state):
        anchor_alias = _gap_anchor_alias(state, candidates=candidates)
        if anchor_alias:
            _select_with_context(updated, anchor_alias)
            logger.info("target_activity: gap anchor로 선택")
            return updated  # type: ignore[return-value]
        logger.warning("target_activity: gap anchor의 활동 소유권을 확인하지 못했습니다")
        return _fallback(updated, "ambiguous_target")

    context_alias = _context_alias(
        state.get("context_experience_id"),
        candidates=candidates,
        alias_to_block_id=state.get("alias_to_block_id", {}),
    )
    if context_alias:
        _select_with_context(updated, context_alias)
        logger.info("target_activity: 화면 context로 선택")
        return updated  # type: ignore[return-value]

    input_text = _selection_input(state)
    if not input_text or not candidates:
        return _fallback(updated, "ambiguous_target")

    try:
        llm = get_experience_map_llm(timeout=get_settings().timeouts.llm)
        chain = target_activity_prompt | llm.with_structured_output(TargetActivityOutput)
        result: TargetActivityOutput = await chain.ainvoke(
            {
                "outline": render_activity_outline(candidates.items()),
                "user_message": input_text,
            }
        )
    except Exception as exc:
        logger.exception("target_activity: LLM 호출 실패")
        raise LlmError("대상 활동을 선택하지 못했습니다.", failed_node="target_activity") from exc

    if result.activity_alias not in candidates:
        logger.info("target_activity: 대상 불명확 또는 허용되지 않은 별칭 (%s)", result.reason)
        return _fallback(updated, "ambiguous_target")

    _select_with_context(updated, result.activity_alias)
    logger.info("target_activity: 메시지와 outline으로 선택 (%s)", result.reason)
    return updated  # type: ignore[return-value]


def _selection_input(state: ExperienceMapState) -> str:
    """채팅과 첨부 파일 추출문을 함께 대상 활동 선택 근거로 만든다.

    파일만 업로드한 요청에서도 문서 내용으로 활동을 고를 수 있어야 한다. 출처
    표시는 서로 다른 입력을 한 문장처럼 잘못 이어 읽지 않게 한다.
    """
    sections: list[str] = []
    user_message = (state.get("user_message") or "").strip()
    extracted_text = (state.get("extracted_text") or "").strip()
    if user_message:
        sections.append(f"[사용자 메시지]\n{user_message}")
    if extracted_text:
        sections.append(f"[첨부 파일 추출문]\n{extracted_text}")
    return "\n\n".join(sections)


def _select_with_context(updated: dict, activity_alias: str) -> None:
    """활동 선택과 함께 service가 미리 읽은 activity 전용 컨텍스트를 적용한다."""
    updated["target_experience_alias"] = activity_alias
    context = updated.get("activity_contexts", {}).get(activity_alias)
    if context:
        updated["activity_tree_text"] = context["tree_text"]
        updated["alias_to_block_id"] = context["alias_to_block_id"]
        updated["alias_metadata"] = context["alias_metadata"]


def next_node(state: ExperienceMapState) -> str:
    """선택 성공 시 gap 분석, 실패 시 fallback으로 보낸다 (graph 배선은 3.17)."""
    return "gap_analysis" if state.get("target_experience_alias") else "fallback"
