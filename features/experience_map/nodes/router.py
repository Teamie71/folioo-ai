"""Router 노드 (에이전트 문서 5-1)

사용자 입력의 의도를 파악해 다음 노드를 정한다.

| intent | 다음 | 판정 |
| --- | --- | --- |
| `file_input` | 파일처리 | **코드** — 파일이 있으면 무조건 |
| `chat_input` | 반영 내용 필터링 | LLM |
| `out_of_scope` | Fallback | LLM |

**gap 답변 여부는 여기서 판정하지 않는다.** 활성 gap이 있어도 일단
`content_filter` 로 보낸다. 분류는 그쪽이 원문과 함께 본다.
"""

import logging

from common.llm import get_experience_map_llm
from features.experience_map.config import get_settings
from features.experience_map.errors import LlmError
from features.experience_map.prompts.router import build_gap_context, router_prompt
from features.experience_map.schemas import RouterOutput
from features.experience_map.state import ExperienceMapState

logger = logging.getLogger(__name__)


async def route(state: ExperienceMapState) -> ExperienceMapState:
    """입력 의도를 판정해 `intent` 를 채운다.

    LLM 실패는 타입 있는 노드 오류로 올린다. 그래프의 공통 RetryPolicy가 정확히
    한 번 자동 재시도하고, 소진 뒤에는 사용자가 router부터 재개할 수 있다.
    """
    updated = dict(state)
    updated["current_node"] = "router"

    # 파일이 있으면 LLM 에게 묻지 않는다. 코드가 아는 사실이다.
    if state.get("file_references"):
        updated["intent"] = "file_input"
        logger.info("router: file_input (파일 %d개)", len(state["file_references"]))
        return updated  # type: ignore[return-value]

    user_message = (state.get("user_message") or "").strip()
    if not user_message:
        # 파일도 메시지도 없는 요청은 API 계층이 이미 막는다. 여기까지 왔다면
        # 반영할 것이 없다는 뜻이므로 fallback 으로 보낸다.
        updated["intent"] = "out_of_scope"
        updated["fallback_reason"] = "nothing_to_apply"
        logger.info("router: 입력이 비어 out_of_scope")
        return updated  # type: ignore[return-value]

    try:
        llm = get_experience_map_llm(timeout=get_settings().timeouts.llm)
        chain = router_prompt | llm.with_structured_output(RouterOutput)
        result: RouterOutput = await chain.ainvoke(
            {
                "user_message": user_message,
                "gap_context": build_gap_context(state.get("active_gap")),
            }
        )
    except Exception as exc:
        # 입력 원문과 upstream 예외 문자열은 로그에 남기지 않는다.
        logger.warning("router: LLM 분류 실패")
        raise LlmError("입력 의도를 분류하지 못했습니다.", failed_node="router") from exc

    updated["intent"] = result.intent
    if result.intent == "out_of_scope":
        updated["fallback_reason"] = "out_of_scope"

    logger.info("router: %s (%s)", result.intent, result.reason)
    return updated  # type: ignore[return-value]


def next_node(state: ExperienceMapState) -> str:
    """`intent` 로 다음 노드를 고른다. graph 배선(3.17)이 쓴다."""
    match state.get("intent"):
        case "file_input":
            return "file_processor"
        case "chat_input":
            return "content_filter"
        case _:
            return "fallback"
