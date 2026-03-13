"""Router 노드 - 입력 라우팅 및 초기 상태 감지"""

from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    초기 상태 감지 및 사용자 입력 라우팅
    - 첫 턴: 바로 QuestionGenerator로 (초기 질문 생성)
    - 이후 턴: 파일 첨부 여부 확인 후 retriever 경유 라우팅
    """

    # 초기 상태 확인 (messages가 비어있는지 여부로 판단)
    messages = state.get("messages", [])
    is_first_turn = len(messages) == 0

    if is_first_turn:
        # 첫 턴: 바로 초기 질문 생성
        next_node = "question_generator"
    else:
        current_turn_files = state.get("current_turn_files") or []
        has_file_attachment = len(current_turn_files) > 0

        if has_file_attachment:
            next_node = "file_processor"
        else:
            next_node = "retriever"

    return {
        **state,
        "is_first_turn": is_first_turn,
        "next_node": next_node,
    }
