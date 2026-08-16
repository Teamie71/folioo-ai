"""경험정리 에이전트의 LangGraph 공유 state

`thread_id`는 `session_id`, `checkpoint_ns`는 `experience_map`을 사용한다
(에이전트 문서 7-1).

state는 두 종류의 필드로 나뉜다.

- **세션 필드**: 턴을 넘어 유지된다 (`session_id`, `active_gap`, `turn_number`)
- **턴 필드**: 요청마다 초기화된다 (입력·중간 산출·검증·실패)

턴 필드를 초기화하지 않으면 이전 요청의 구조화 결과가 다음 요청에 섞여 커밋된다.
`reset_turn_fields()`가 이 경계를 담당한다.

checkpoint에는 **직렬화 가능한 값만** 저장한다. Pydantic 모델·파일 핸들·커넥션을
넣지 않고 dict과 원시 타입만 쓴다.
"""

from typing import Any, Literal, NotRequired, TypedDict

from features.experience_map.config import CHECKPOINT_NAMESPACE
from features.experience_map.schemas import FallbackReason, Intent, NodeName

# ===== 하위 타입 =====


class FileReference(TypedDict):
    """업로드 파일의 임시 저장 참조.

    파일 본문이 아니라 GCS object 참조만 담는다. 추출 성공 시 원본은 즉시 삭제되고
    `ExtractedFile.text`만 남는다.
    """

    file_id: str
    filename: str
    content_type: str
    file_size: int
    sha256: str
    gcs_object: str


class ExtractedFile(TypedDict):
    """파일별 텍스트 추출 결과"""

    file_id: str
    text: str
    source_hash: str
    extractor: Literal["parser", "ocr"]


class OutlineNode(TypedDict):
    """그룹·활동 outline의 한 항목. 별칭 기준이며 실제 ID를 담지 않는다."""

    alias: str
    level: int
    title: str
    children: NotRequired[list["OutlineNode"]]


class ValidationError(TypedDict):
    """validate 노드가 만든 위반 항목"""

    item_id: str
    code: str
    message: str
    repair_target: Literal["structure", "refine"]


class ExperienceMapState(TypedDict, total=False):
    """경험정리 그래프의 공유 state"""

    # ===== 세션 (턴을 넘어 유지) =====
    user_id: str
    session_id: str
    turn_number: int
    active_gap: dict[str, Any] | None  # ActiveGap.model_dump()

    # ===== 요청 =====
    request_id: str
    request_hash: str

    # ===== 입력 =====
    user_message: str | None
    context_experience_id: str | None
    view: str | None

    # ===== 파일 =====
    file_references: list[FileReference]
    extracted_files: list[ExtractedFile]
    extracted_text: str | None  # 입력 순서대로 이어 붙인 전체 텍스트

    # ===== 라우팅 =====
    intent: Intent | None
    current_node: NodeName | None
    fallback_reason: FallbackReason | None

    # ===== 맵 =====
    map_version: int | None
    outline: list[OutlineNode]
    target_experience_alias: str | None
    alias_to_block_id: dict[str, str]
    activity_tree_text: str | None
    block_id_to_experience_alias: dict[str, str]
    block_id_to_content: dict[str, str]
    activity_contexts: dict[str, dict[str, Any]]

    # ===== 필터링 분류 결과 =====
    gap_answer_items: list[dict[str, Any]]
    new_items: list[dict[str, Any]]
    excluded_reasons: list[str]

    # ===== 중간 산출 =====
    structured_items: list[dict[str, Any]]
    refined_items: list[dict[str, Any]]
    gap_update_item: dict[str, Any] | None
    commit_items: list[dict[str, Any]]
    dropped_items: list[dict[str, Any]]
    commit_result: dict[str, Any] | None
    commit_conflict_count: int
    commit_recovery_node: Literal["validate", "structure"] | None
    gap_candidate: dict[str, Any] | None
    gap_message: str | None
    suggestion: dict[str, Any] | None

    # ===== 검증 =====
    validation_errors: list[ValidationError]
    repair_count: int

    # ===== 실패 =====
    failed_node: NodeName | None
    node_retry_count: dict[str, int]


# 요청마다 초기화되는 턴 전용 필드와 기본값.
# 세션 필드(user_id·session_id·turn_number·active_gap)는 여기에 넣지 않는다.
TURN_FIELD_DEFAULTS: dict[str, Any] = {
    "request_id": "",
    "request_hash": "",
    "user_message": None,
    "context_experience_id": None,
    "view": None,
    "file_references": [],
    "extracted_files": [],
    "extracted_text": None,
    "intent": None,
    "current_node": None,
    "fallback_reason": None,
    "map_version": None,
    "outline": [],
    "target_experience_alias": None,
    "alias_to_block_id": {},
    "activity_tree_text": None,
    "block_id_to_experience_alias": {},
    "block_id_to_content": {},
    "activity_contexts": {},
    "gap_answer_items": [],
    "new_items": [],
    "excluded_reasons": [],
    "structured_items": [],
    "refined_items": [],
    "gap_update_item": None,
    "commit_items": [],
    "dropped_items": [],
    "commit_result": None,
    "commit_conflict_count": 0,
    "commit_recovery_node": None,
    "gap_candidate": None,
    "gap_message": None,
    "suggestion": None,
    "validation_errors": [],
    "repair_count": 0,
    "failed_node": None,
    "node_retry_count": {},
}

SESSION_FIELDS: frozenset[str] = frozenset({"user_id", "session_id", "turn_number", "active_gap"})

# 성공 뒤 정리하는 큰 필드. checkpoint 크기를 줄인다.
CLEANUP_FIELDS: tuple[str, ...] = (
    "file_references",
    "extracted_files",
    "extracted_text",
    "outline",
    "structured_items",
    "refined_items",
)


def build_thread_config(session_id: str) -> dict[str, Any]:
    """LangGraph 실행 config 생성

    Args:
        session_id: 세션 UUID 문자열. `thread_id`로 사용한다

    Returns:
        dict: `ainvoke`·`astream`에 넘길 config
    """
    return {
        "configurable": {
            "thread_id": session_id,
            "checkpoint_ns": CHECKPOINT_NAMESPACE,
        }
    }


def reset_turn_fields(state: ExperienceMapState) -> ExperienceMapState:
    """턴 전용 필드를 기본값으로 되돌린 state를 반환한다.

    세션 필드는 보존한다. 새 요청을 시작할 때 호출해 이전 요청의 중간 산출물이
    섞이지 않게 한다.

    Args:
        state: 직전 턴까지의 state

    Returns:
        ExperienceMapState: 턴 필드가 초기화된 새 state
    """
    reset: ExperienceMapState = {
        key: value for key, value in state.items() if key in SESSION_FIELDS
    }  # type: ignore[assignment]
    for key, default in TURN_FIELD_DEFAULTS.items():
        reset[key] = _copy_default(default)  # type: ignore[literal-required]
    return reset


def start_turn(
    state: ExperienceMapState,
    *,
    request_id: str,
    request_hash: str,
    user_message: str | None = None,
    context_experience_id: str | None = None,
    view: str | None = None,
) -> ExperienceMapState:
    """새 요청을 시작한다. 턴 필드를 초기화하고 입력을 채운다.

    Args:
        state: 직전 턴까지의 state
        request_id: 메인 서버가 발급한 UUID
        request_hash: 멱등성 판정용 해시
        user_message: 사용자 메시지 (파일만 있으면 None)
        context_experience_id: 화면에서 보고 있던 level 2 활동 block ID
        view: `map`, `list` 또는 None

    Returns:
        ExperienceMapState: 이번 턴을 실행할 state
    """
    turn = reset_turn_fields(state)
    turn["request_id"] = request_id
    turn["request_hash"] = request_hash
    turn["user_message"] = user_message
    turn["context_experience_id"] = context_experience_id
    turn["view"] = view
    turn["turn_number"] = state.get("turn_number", 0) + 1
    return turn


def cleanup_after_success(state: ExperienceMapState) -> ExperienceMapState:
    """성공한 턴의 파일 참조와 큰 중간 산출물을 비운다.

    커밋 결과와 gap은 남긴다. 재시도는 실패 시점 checkpoint에서 재개하므로
    성공한 턴의 중간 산출물은 더 필요하지 않다.

    대상 필드는 원래 state에 없었더라도 기본값으로 채운다. 정리 뒤에는 항상
    "비어 있음"이 명시되어야 읽는 쪽이 존재 여부를 확인하지 않아도 된다.
    """
    cleaned = dict(state)
    for key in CLEANUP_FIELDS:
        cleaned[key] = _copy_default(TURN_FIELD_DEFAULTS[key])
    return cleaned  # type: ignore[return-value]


def record_node_failure(state: ExperienceMapState, node: NodeName) -> ExperienceMapState:
    """노드 실패를 기록한다. 재시도 횟수는 노드별로 센다."""
    counts = dict(state.get("node_retry_count") or {})
    counts[node] = counts.get(node, 0) + 1
    updated = dict(state)
    updated["failed_node"] = node
    updated["node_retry_count"] = counts
    return updated  # type: ignore[return-value]


def _copy_default(value: Any) -> Any:
    """가변 기본값이 여러 state에 공유되지 않도록 복사한다."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value
