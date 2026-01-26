"""FileProcessor 노드 - 파일 처리"""

from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    첨부된 파일 처리
    - 파일 타입 확인 (이미지, 문서, 등)
    - 파일 내용 추출 및 분석
    - 처리된 정보를 state에 저장

    TODO: 실제 파일 처리 로직은 후속 이슈에서 구현
    - 파일 타입별 파서 구현
    - OCR vision LLM
    - 문서 텍스트 추출
    """

    # 파일 처리 후 Retriever 노드로 전환 (임시 값)
    return {
        **state,
        "processed_files": [],  # 처리된 파일 정보 저장
        "file_context": {},  # 추출된 텍스트 저장
        "next_node": "retriever",
    }
