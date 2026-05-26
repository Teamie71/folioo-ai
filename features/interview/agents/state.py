"""인터뷰 에이전트의 공유 state 정의"""

from typing import Annotated, Literal, NotRequired, TypedDict

from langgraph.graph.message import add_messages


# 하위 타입 정의
# TODO: 실제 구조에 맞게 수정 필요
class InsightLog(TypedDict):
    """인사이트 로그 구조"""

    id: str
    title: str
    activity_name: str
    category: Literal["대인관계", "문제해결", "학습", "레퍼런스", "기타"]
    content: str
    similarity_score: float | None  # Retriever가 검색 시 계산한 유사도
    source: NotRequired[Literal["mention", "search"]]


class InsightTurnRecord(TypedDict):
    """사용자 턴별 인사이트 복원 기록"""

    turn_number: int
    user_message: str
    mentioned_insight: str | None
    insights: list[InsightLog]


class FileMetadata(TypedDict):
    """턴 히스토리 복원을 위한 파일 메타데이터"""

    filename: str
    content_type: str
    file_size: int


class FileTurnRecord(TypedDict):
    """사용자 턴별 파일 메타데이터 복원 기록"""

    turn_number: int
    files: list[FileMetadata]


class FilePayload(TypedDict):
    """현재 턴 업로드 파일의 임시 저장 참조"""

    filename: str
    content_type: str
    temp_path: str
    file_size: int


class StageProgress(TypedDict):
    """현재 단계의 진행 상황"""

    fixed_q_used: int  # 사용자가 답변 완료한 고정 질문 수
    fixed_q_total: int  # 전체 고정 질문 수 (stages.yaml에서 로드)
    generated_q_used: int  # 사용자가 답변 완료한 생성 질문 수
    generated_q_max: int  # 최대 생성 질문 수 (stages.yaml에서 로드)
    force_all_generated_q: bool  # 생성 질문 강제 소진 여부 (단계별 설정 stages.yaml에서 로드)
    is_complete: bool  # 질문 소진으로 인한 단계 완료 여부


class AdditionalQuestionTargetStatus(TypedDict):
    """추가 질문 target별 내부 진행 상태"""

    asked_count: int
    is_satisfied: bool


class CollectedField(TypedDict):
    """수집된 필드 정보"""

    field_name: str  # required_fields의 키 이름
    description: str  # 필드 설명 (stages.yaml에서)
    value: str | list[str] | None  # 수집된 값 (리스트 타입 필드 지원)
    completeness: float  # 0.0 ~ 1.0 (Analyst가 LLM으로 판단한 완성도)
    # last_updated_turn: int  # 마지막으로 업데이트된 턴 번호


# 메인 State 정의
InterviewSessionStatus = Literal["generating", "completed", "failed"]


class InterviewState(TypedDict):
    """
    인터뷰 에이전트의 공유 상태
    모든 노드가 읽고 쓸 수 있으며, LangGraph의 리듀서를 통해 자동으로 병합됩니다.
    각 노드는 필요한 필드만 반환하면 되며, 반환하지 않은 필드는 기존 값을 유지합니다.
    """

    # ===== 세션 정보 =====
    user_id: str  # 사용자 고유 ID
    session_id: str  # 세션 고유 ID
    experience_name: str  # 사용자가 정리하려는 경험/프로젝트명 (세션 생성 시 입력)
    status: InterviewSessionStatus  # 세션 생성 상태
    turn_number: int  # 현재 사용자 턴 번호 (세션 생성 직후 0, 사용자 메시지 처리 시 1부터 증가)

    # ===== 대화 기록 (LangGraph 메시지 리듀서 사용) =====
    messages: Annotated[list, add_messages]
    # AI 질문, 사용자 답변, 시스템 알림 등 모든 대화 메시지를 포함
    # 전체 대화 내역 유지 (UI에서 전체 스크롤 가능)

    # ===== 단계 관리 =====
    current_stage: Literal[1, 2, 3, 4]  # 현재 단계 (Analyst가 전환)
    stage_progress: StageProgress  # 현재 단계의 진행 상황

    # ===== 질문 관리 =====
    # last_question: str | None  # 마지막으로 생성된 질문 (QuestionGenerator가 생성)
    # is_first_turn: bool  # 첫 턴 여부 (Router가 판단)

    # ===== 수집된 포트폴리오 정보 =====
    collected_data: dict[str, dict[str, CollectedField]]
    # 구조: {
    #   "stage_1": {
    #     "project_background": CollectedField(...),
    #     "problem_definition": CollectedField(...),
    #     ...
    #   },
    #   "stage_2": {...},
    #   ...
    # }
    # 단계 전환 시 누적 저장 (이전 단계 데이터 유지)
    # Analyst가 현재 턴의 사용자 답변을 분석하여 업데이트

    # ===== 인사이트 로그 =====
    mentioned_insight: str | None
    # 현재 턴에서 사용자가 @로 명시한 insight ID

    retrieved_insights: list[InsightLog]
    # 해당 턴에서 검색/멘션된 인사이트 로그 목록 (매 턴 갱신, 누적하지 않음)
    # Retriever가 다음을 수행:
    #   1. 현재 턴 사용자 메시지로 유사도 검색 (pgvector → 향후 API)
    #   2. @멘션된 인사이트 강제 포함
    #   3. 병합 & 중복 제거 후 해당 턴의 결과만 반환
    # 이전 턴의 인사이트는 messages 히스토리를 통해 자연스럽게 context에 포함됨

    insight_turn_history: list[InsightTurnRecord]
    # 사용자 턴별 인사이트 카드 복원 이력
    # retrieved_insights와 별개로 과거 턴 전체를 누적 저장

    file_turn_history: list[FileTurnRecord]
    # 사용자 턴별 파일 메타데이터 복원 이력
    # current_turn_files와 별개로 과거 턴 전체를 누적 저장

    # ===== 파일 업로드 =====
    current_turn_files: list[FilePayload]
    # 현재 턴에서 새로 업로드된 파일 참조들
    # API 레이어가 state에 주입
    # Router가 이를 확인하여 FileProcessor로 라우팅 결정

    file_contexts: list[str]
    # 현재 턴 업로드 파일에서 추출된 텍스트들 (매 턴 초기화)
    # FileProcessor가 OCR/캡셔닝/문서 텍스트 추출 결과를 저장

    # ===== 라우팅 =====
    next_node: Literal["file_processor", "retriever", "analyst", "question_generator", "end"]
    # 다음에 실행할 노드 지정 (Router가 결정)

    # ===== 완료 상태 =====
    all_stages_complete: bool
    # 4단계가 모두 완료되었는지 여부
    # Analyst가 stage 4의 질문이 모두 소진되면 True로 설정

    overall_completion_percentage: float
    # 전체 완료율 (0.0 ~ 100.0)
    # Analyst가 4단계 완료 감지 시 LLM을 호출하여 계산

    # ============ 결제 후 추가 대화 ============
    is_extended_mode: bool
    # 결제하여 추가 대화 모드로 진입했는지 여부
    # all_stages_complete=True 이후 사용자가 결제하면 True로 설정
    # state 구조는 동일하게 유지 (단계 구조 재사용 안 함)

    extension_count: int
    # 연장 횟수 (최대 2회)

    extension_turns_used: int
    # 현재 연장에서 사용한 질문 횟수

    extension_turns_max: int
    # 연장 1회당 최대 질문 횟수

    additional_question_target_statuses: dict[str, AdditionalQuestionTargetStatus]
    # 추가 질문 target별 내부 상태 (API 응답에는 노출하지 않음)

    additional_question_pre_evaluated: bool
    # 추가 대화 시작 시 기존 11개 답변 기준 target 사전 판정 완료 여부

    current_additional_question_target_id: str | None
    # 마지막으로 생성한 추가 질문이 다룬 target id

    # ===== 에러 추적 =====
    llm_error: str | None
    # LLM 호출 실패 시 에러 메시지 기록
    # QuestionGenerator 등이 LLM 호출 실패 시 기록


def get_initial_interview_state(
    user_id: str,
    session_id: str,
    experience_name: str,
) -> InterviewState:
    """
    새 세션 시작 시 초기 state 생성
    API 레이어가 첫 호출 시 이 함수를 사용하여 state 초기화
    """

    from features.interview.config.loader import get_global_config, load_stage_config

    stage_1_config = load_stage_config(1)
    global_config = get_global_config()

    return {
        # 세션 정보
        "user_id": user_id,
        "session_id": session_id,
        "experience_name": experience_name,
        "status": "completed",
        "turn_number": 0,
        # 대화 기록
        "messages": [],
        # 단계 관리
        "current_stage": 1,
        "stage_progress": {
            "fixed_q_used": 0,
            "fixed_q_total": len(stage_1_config.fixed_questions),
            "generated_q_used": 0,
            "generated_q_max": stage_1_config.max_generated_questions,
            "force_all_generated_q": stage_1_config.force_all_generated_questions,
            "is_complete": False,
        },
        # 질문 관리
        # "last_question": None,
        # "is_first_turn": True,
        # 수집 데이터
        "collected_data": {f"stage_{i}": {} for i in range(1, 5)},
        # 인사이트
        "mentioned_insight": None,
        "retrieved_insights": [],
        "insight_turn_history": [],
        "file_turn_history": [],
        # 파일 업로드
        "current_turn_files": [],
        "file_contexts": [],
        # 라우팅
        "next_node": "question_generator",
        # 완료 상태
        "all_stages_complete": False,
        "overall_completion_percentage": 0.0,
        # 결제 후 추가 대화
        "is_extended_mode": False,
        "extension_count": 0,
        "extension_turns_used": 0,
        "extension_turns_max": global_config.extension_turns_per_session,
        "additional_question_target_statuses": {},
        "additional_question_pre_evaluated": False,
        "current_additional_question_target_id": None,
        # 에러 추적
        "llm_error": None,
    }


def get_turn_number_from_messages(messages: list) -> int:
    """메시지 목록에서 사용자 턴 수를 계산"""

    return sum(1 for message in messages if getattr(message, "type", None) == "human")


def ensure_interview_state_defaults(state: InterviewState | dict) -> InterviewState:
    """구버전 세션 state에도 신규 기본값을 보강"""

    messages = list(state.get("messages") or [])

    return {
        **state,
        "status": state.get("status", "completed"),
        "turn_number": state.get("turn_number", get_turn_number_from_messages(messages)),
        "mentioned_insight": state.get("mentioned_insight"),
        "retrieved_insights": list(state.get("retrieved_insights") or []),
        "insight_turn_history": list(state.get("insight_turn_history") or []),
        "file_turn_history": list(state.get("file_turn_history") or []),
        "current_turn_files": list(state.get("current_turn_files") or []),
        "file_contexts": list(state.get("file_contexts") or []),
        "additional_question_target_statuses": dict(
            state.get("additional_question_target_statuses") or {}
        ),
        "additional_question_pre_evaluated": bool(
            state.get("additional_question_pre_evaluated", False)
        ),
        "current_additional_question_target_id": state.get("current_additional_question_target_id"),
    }
