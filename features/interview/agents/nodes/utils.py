"""에이전트 노드 공통 유틸리티"""

from langchain_core.messages import AIMessage, HumanMessage

from ..state import InterviewState


def _get_conversation_context(
    state: InterviewState,
    max_messages: int = 5,
) -> str:
    """
    최근 N개 메시지를 문자열로 포맷팅하여 반환

    Args:
        state: 현재 상태
        max_messages: 최대 메시지 수 (global_config.context_window_size)

    Returns:
        포맷팅된 대화 컨텍스트
    """
    messages = state["messages"]
    recent_messages = messages[-max_messages:] if len(messages) > max_messages else messages

    formatted = []
    for msg in recent_messages:
        if isinstance(msg, AIMessage):
            formatted.append(f"AI: {msg.content}")
        elif isinstance(msg, HumanMessage):
            formatted.append(f"사용자: {msg.content}")

    return "\n".join(formatted)
