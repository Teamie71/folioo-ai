"""에이전트 노드 공통 유틸리티"""

from typing import TypedDict

from langchain_core.messages import AIMessage, HumanMessage

from features.interview.config.loader import GlobalConfig, StageConfig

from ..state import AdditionalQuestionTargetStatus, CollectedField, InsightLog, InterviewState


class AdditionalQuestionTargetWithPriority(TypedDict):
    """우선순위 정보를 포함한 추가 질문 target"""

    target: str
    priority: int
    stage: int
    field_name: str
    label: str
    question_hint: str
    field_description: str
    stage_name: str


def _flatten_additional_question_targets(
    global_config: GlobalConfig,
    stages: dict[int, StageConfig],
) -> list[AdditionalQuestionTargetWithPriority]:
    """추가 질문 target을 priority/YAML 순서대로 평탄화한다."""
    targets: list[AdditionalQuestionTargetWithPriority] = []
    for group in global_config.additional_question_priorities:
        for target in group.targets:
            stage_config = stages[target.stage]
            targets.append(
                {
                    "target": target.target,
                    "priority": group.priority,
                    "stage": target.stage,
                    "field_name": target.field_name,
                    "label": target.label,
                    "question_hint": target.question_hint,
                    "field_description": target.field_description,
                    "stage_name": stage_config.name,
                }
            )
    return targets


def _ensure_additional_question_target_statuses(
    state: InterviewState,
    targets: list[AdditionalQuestionTargetWithPriority],
) -> dict[str, AdditionalQuestionTargetStatus]:
    """설정된 모든 target의 상태 기본값을 보강한다."""
    statuses: dict[str, AdditionalQuestionTargetStatus] = {
        key: {
            "asked_count": int(value.get("asked_count", 0)),
            "is_satisfied": bool(value.get("is_satisfied", False)),
        }
        for key, value in state.get("additional_question_target_statuses", {}).items()
    }

    for target in targets:
        statuses.setdefault(
            target["target"],
            {
                "asked_count": 0,
                "is_satisfied": False,
            },
        )

    return statuses


def _select_next_additional_question_target(
    state: InterviewState,
    targets: list[AdditionalQuestionTargetWithPriority],
) -> AdditionalQuestionTargetWithPriority | None:
    """1차/2차 패스 규칙에 따라 다음 추가 질문 target을 선택한다."""
    statuses = state.get("additional_question_target_statuses", {})

    for pass_asked_count in (0, 1):
        for target in targets:
            status = statuses.get(target["target"])
            if status is None:
                continue
            if status["is_satisfied"]:
                continue
            if status["asked_count"] == pass_asked_count:
                return target

    return None


def _all_additional_question_targets_satisfied(
    state: InterviewState,
    targets: list[AdditionalQuestionTargetWithPriority],
) -> bool:
    """모든 추가 질문 target이 충분한지 확인한다."""
    if not targets:
        return True

    statuses = state.get("additional_question_target_statuses", {})
    return all(statuses.get(target["target"], {}).get("is_satisfied", False) for target in targets)


def _has_askable_additional_question_target(
    state: InterviewState,
    targets: list[AdditionalQuestionTargetWithPriority],
) -> bool:
    """질문 가능한 추가 질문 target이 남아 있는지 확인한다."""
    return _select_next_additional_question_target(state, targets) is not None


def _increment_additional_question_target_asked_count(
    statuses: dict[str, AdditionalQuestionTargetStatus],
    target_id: str,
) -> dict[str, AdditionalQuestionTargetStatus]:
    """질문 생성 직후 해당 target의 질문 횟수를 증가한다."""
    updated_statuses = {key: {**value} for key, value in statuses.items()}
    status = updated_statuses.setdefault(
        target_id,
        {
            "asked_count": 0,
            "is_satisfied": False,
        },
    )
    status["asked_count"] = status["asked_count"] + 1
    return updated_statuses


def _format_selected_additional_question_target(
    target: AdditionalQuestionTargetWithPriority,
) -> str:
    """선택된 추가 질문 target을 사용자 노출 가능한 prompt 문자열로 변환한다."""
    return (
        f"- 우선순위: {target['priority']}\n"
        f"- 단계: {target['stage']}단계 {target['stage_name']}\n"
        f"- 라벨: {target['label']}\n"
        f"- 질문 힌트: {target['question_hint']}\n"
        f"- 충분성 기준: {target['field_description']}"
    )


def _format_additional_target_sufficiency_inputs(
    targets: list[AdditionalQuestionTargetWithPriority],
    collected_data: dict[str, dict[str, CollectedField]],
) -> str:
    """사전 판정용 target 목록과 현재 수집 데이터를 prompt 문자열로 변환한다."""
    if not targets:
        return "판정할 추가 질문 target 없음"

    lines: list[str] = []
    for target in targets:
        stage_key = f"stage_{target['stage']}"
        collected_field = collected_data.get(stage_key, {}).get(target["field_name"])
        if collected_field is None:
            collected_text = "수집된 값 없음"
            completeness = 0.0
        else:
            collected_text = str(collected_field.get("value"))
            completeness = float(collected_field.get("completeness", 0.0))

        lines.append(f"[target: {target['target']}]")
        lines.append(f"- 우선순위: {target['priority']}")
        lines.append(f"- 단계: {target['stage']}단계 {target['stage_name']}")
        lines.append(f"- 라벨: {target['label']}")
        lines.append(f"- 질문 힌트: {target['question_hint']}")
        lines.append(f"- 충분성 기준: {target['field_description']}")
        lines.append(f"- 현재 수집값 완성도: {completeness:.2f}")
        lines.append(f"- 현재 수집값: {collected_text}")
        lines.append("")

    return "\n".join(lines).strip()


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


def _get_incomplete_fields(
    state: InterviewState,
    stage_config: StageConfig,
    completeness_threshold: float = 0.7,
    collected_data: dict[str, dict[str, CollectedField]] | None = None,
) -> list[dict[str, str]]:
    """
    현재 단계에서 미수집 또는 불완전한 필드 목록 반환

    Args:
        state: 현재 상태
        stage_config: 단계 설정
        completeness_threshold: 완성도 임계값 (이하면 불완전으로 간주)
        collected_data: completeness 계산에 사용할 수집 데이터 (없으면 state 사용)

    Returns:
        미수집/불완전 필드 목록 [{"field_name": ..., "description": ...}, ...]
    """
    current_stage = state["current_stage"]
    stage_key = f"stage_{current_stage}"
    source_collected_data = (
        collected_data if collected_data is not None else state["collected_data"]
    )
    collected = source_collected_data.get(stage_key, {})
    required = stage_config.required_fields

    incomplete = []
    for field_name, field_info in required.items():
        collected_field: CollectedField | None = collected.get(field_name)
        if collected_field is None:
            incomplete.append(
                {
                    "field_name": field_name,
                    "description": field_info.get("description", ""),
                }
            )
        elif collected_field["completeness"] < completeness_threshold:
            incomplete.append(
                {
                    "field_name": field_name,
                    "description": field_info.get("description", ""),
                }
            )

    return incomplete


def _get_all_stage_incomplete_fields(
    state: InterviewState,
    stages: dict[int, StageConfig],
    completeness_threshold: float = 0.7,
    collected_data: dict[str, dict[str, CollectedField]] | None = None,
) -> list[dict[str, str | int | float]]:
    """전체 4단계에서 미수집 또는 불완전한 필드를 완성도 기준으로 반환"""
    source_collected_data = (
        collected_data if collected_data is not None else state["collected_data"]
    )

    incomplete_fields: list[dict[str, str | int | float]] = []
    for stage_number in sorted(stages):
        stage_config = stages[stage_number]
        stage_key = f"stage_{stage_number}"
        stage_collected = source_collected_data.get(stage_key, {})

        for field_name, field_info in stage_config.required_fields.items():
            collected_field = stage_collected.get(field_name)
            completeness = 0.0 if collected_field is None else collected_field["completeness"]
            if completeness >= completeness_threshold:
                continue

            incomplete_fields.append(
                {
                    "stage": stage_number,
                    "stage_name": stage_config.name,
                    "field_name": field_name,
                    "description": field_info.get("description", ""),
                    "completeness": completeness,
                }
            )

    incomplete_fields.sort(key=lambda field: float(field["completeness"]))
    return incomplete_fields


def _format_incomplete_fields(incomplete_fields: list[dict[str, str]]) -> str:
    """
    미수집 필드를 프롬프트용 문자열로 포맷팅

    Args:
        incomplete_fields: 미수집 필드 목록

    Returns:
        포맷팅된 문자열
    """
    if not incomplete_fields:
        return "모든 필드가 충분히 수집되었습니다."

    lines = []
    for field in incomplete_fields:
        lines.append(f"- {field['field_name']}: {field['description']}")
    return "\n".join(lines)


def _format_global_incomplete_fields(
    incomplete_fields: list[dict[str, str | int | float]],
) -> str:
    """전체 단계 미수집 필드를 프롬프트용 문자열로 포맷팅"""
    if not incomplete_fields:
        return "모든 단계의 필드가 충분히 수집되었습니다."

    lines: list[str] = []
    for field in incomplete_fields:
        lines.append(
            "- stage_{stage} ({stage_name}) / {field_name} (완성도: {completeness:.2f}): "
            "{description}".format(
                stage=field["stage"],
                stage_name=field["stage_name"],
                field_name=field["field_name"],
                completeness=float(field["completeness"]),
                description=field["description"],
            )
        )

    return "\n".join(lines)


def _format_retrieved_insights(insights: list[InsightLog]) -> str:
    """인사이트 로그를 프롬프트용 문자열로 변환"""
    if not insights:
        return "검색된 인사이트 없음"

    lines: list[str] = []
    for insight in insights:
        similarity_score = insight.get("similarity_score")
        similarity_text = (
            f"{similarity_score:.2f}" if isinstance(similarity_score, int | float) else "없음"
        )
        source = insight.get("source") or ("search" if similarity_score is not None else "mention")
        lines.append(f"- [{insight['category']}] {insight['title']}")
        lines.append(f"  - 활동명: {insight.get('activity_name') or '없음'}")
        lines.append(f"  - 출처: {source}")
        lines.append(f"  - 유사도: {similarity_text}")
        lines.append(f"  - 내용: {insight['content']}")

    return "\n".join(lines)


def _format_file_contexts(file_contexts: list[str]) -> str:
    """파일 컨텍스트를 프롬프트용 문자열로 변환"""
    if not file_contexts:
        return "첨부 파일 없음"

    return "\n---\n".join(file_contexts)
