"""Router 노드 - 입력 라우팅 및 초기 상태 감지"""

from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    초기 상태 감지 및 사용자 입력 라우팅
    - 첫 턴: 바로 QuestionGenerator로 (초기 질문 생성)
    - 이후 턴: 파일 첨부 여부 확인 후 라우팅

    TODO: 실제 라우팅 로직은 후속 이슈에서 구현
    - 초기 상태 감지
    - 파일 첨부 여부 확인
    - 조건부 라우팅
    """

    # 초기 상태 확인 (messages가 비어있는지 여부로 판단)
    messages = state.get("messages", [])
    is_first_turn = len(messages) == 0

    if is_first_turn:
        # 첫 턴: 바로 초기 질문 생성
        next_node = "question_generator"
    else:
        # 파일 첨부 여부 확인 (임시로 항상 False로 설정)
        has_file_attachment = False  # TODO: 실제 파일 첨부 확인 로직 구현

        if has_file_attachment:
            next_node = "file_processor"
        else:
            next_node = "retriever"

    return {
        **state,
        "is_first_turn": is_first_turn,
        "next_node": next_node,
    }
