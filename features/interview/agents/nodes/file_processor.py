"""FileProcessor 노드 - 업로드된 파일에서 텍스트 추출"""

from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    업로드된 파일에서 텍스트 추출 (PDF, 이미지 OCR)

    TODO: 실제 파일 처리 로직은 후속 이슈에서 구현
    - 멀티모달 LLM 사용
    """

    # 파일 처리 후 Supervisor 노드로 전환 (임시 값)
    return {
        **state,
        "file_context": [],
        "next_node": "supervisor",
    }
