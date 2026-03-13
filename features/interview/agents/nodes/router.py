"""Router 노드 - 입력 라우팅 및 초기 상태 감지"""

from ..state import InterviewState, ensure_interview_state_defaults, get_turn_number_from_messages


def run(state: InterviewState) -> InterviewState:
    """
    초기 상태 감지 및 사용자 입력 라우팅
    - 첫 턴: 바로 QuestionGenerator로 (초기 질문 생성)
    - 이후 턴: 파일 첨부 여부 확인 후 retriever 경유 라우팅
    """

    normalized_state = ensure_interview_state_defaults(state)
    current_turn_number = get_turn_number_from_messages(normalized_state.get("messages", []))
    has_new_user_message = current_turn_number > normalized_state["turn_number"]
    is_session_bootstrap = normalized_state["turn_number"] == 0 and current_turn_number == 0
    turn_number = normalized_state["turn_number"]

    if (
        is_session_bootstrap
        or normalized_state.get("is_extended_mode", False)
        and not has_new_user_message
    ):
        # 세션 생성 직후/연장 시작 직후에는 이번 실행의 사용자 답변이 없어 turn_number를 유지한다.
        next_node = "question_generator"
    else:
        if has_new_user_message:
            turn_number += 1

        current_turn_files = normalized_state.get("current_turn_files") or []
        has_file_attachment = len(current_turn_files) > 0

        if has_file_attachment:
            next_node = "file_processor"
        else:
            next_node = "retriever"

    return {
        **normalized_state,
        "turn_number": turn_number,
        "next_node": next_node,
    }
