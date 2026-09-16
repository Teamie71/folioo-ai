"""Fallback 노드 (에이전트 문서 5-11)

**LLM을 호출하지 않는다.** 진입 경로별 고정 문구다.

DB를 수정하지 않고 `message_complete(committed=false)` 를 보낸 뒤 요청을
completed로 저장한다. **실패가 아니므로 재시도 버튼을 노출하지 않는다** — 손상된
PDF를 올린 사용자에게 재시도 버튼을 주면 몇 번을 눌러도 같은 결과다.

경로를 하나의 문구로 합치지 않는 이유는 사용자가 취할 다음 행동이 다르기
때문이다. 파일이 손상돼 실패한 사용자에게 "아직 지원하지 않는 기능이에요"라고
하면 다른 파일로 올려볼 생각을 하지 못한다.
"""

import logging

from features.experience_map.schemas import FallbackReason
from features.experience_map.state import ExperienceMapState

logger = logging.getLogger(__name__)

FALLBACK_MESSAGES: dict[str, str] = {
    # 에이전트 문서 3-10 응답 템플릿 1 — 선행 노드가 Router인 경우. 문구를 그대로 옮긴다.
    "out_of_scope": (
        "지금은 제공해주신 내용을 바탕으로 경험을 정리하는 것만 도와드릴 수 있어요.\n"
        "정리하고 싶은 경험의 상황, 맡은 역할, 진행 과정, 결과를 알려주시면 적절한 블록으로 "
        "정리해드릴게요."
    ),
    "file_unreadable": (
        "파일에서 내용을 읽지 못했어요. 다른 파일로 올려 주시거나 내용을 직접 입력해 주세요."
    ),
    # 에이전트 문서 3-10 응답 템플릿 2 — 선행 노드가 블록 반영 내용 필터링인 경우. 문구를
    # 그대로 옮긴다. structure·refine이 반영할 내용을 못 찾아 뒤늦게 fallback 하는 경우도
    # "반영할 내용을 찾지 못했다"는 같은 사용자 경험이라 이 문구를 그대로 쓴다.
    "nothing_to_apply": (
        "경험정리 블록에 반영할 수 있는 내용을 찾지 못했어요.\n"
        "어떤 활동에서 무엇을 했고, 어떤 방식으로 진행했으며, 결과가 어땠는지 알려주시면 "
        "블록으로 정리해드릴게요."
    ),
    "ambiguous_target": "어떤 경험에 정리할지 알려주세요.",
}

DEFAULT_REASON: FallbackReason = "out_of_scope"


def fallback_message(reason: str | None) -> str:
    """진입 경로에 맞는 문구를 돌려준다.

    모르는 사유면 가장 일반적인 `out_of_scope` 문구를 쓴다. 사용자에게 빈 응답을
    보내는 것보다 낫다.
    """
    if reason not in FALLBACK_MESSAGES:
        if reason is not None:
            logger.warning("알 수 없는 fallback 사유: %s", reason)
        reason = DEFAULT_REASON
    return FALLBACK_MESSAGES[reason]


async def fallback(state: ExperienceMapState) -> ExperienceMapState:
    """fallback 응답을 준비한다. DB는 건드리지 않는다."""
    updated = dict(state)
    updated["current_node"] = "fallback"

    reason = state.get("fallback_reason") or DEFAULT_REASON
    updated["fallback_reason"] = reason

    logger.info("fallback: %s", reason)
    return updated  # type: ignore[return-value]
