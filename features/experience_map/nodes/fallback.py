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
    "out_of_scope": "아직 지원하지 않는 기능이에요.",
    "file_unreadable": (
        "파일에서 내용을 읽지 못했어요. 다른 파일로 올려 주시거나 내용을 직접 입력해 주세요."
    ),
    "nothing_to_apply": "정리에 반영할 내용을 찾지 못했어요. 어떤 경험을 하셨는지 알려주세요.",
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
