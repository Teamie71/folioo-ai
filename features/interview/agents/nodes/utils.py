"""에이전트 노드 공통 유틸리티"""

from langchain_core.messages import AIMessage, HumanMessage

from features.interview.config.loader import StageConfig

from ..state import CollectedField, InterviewState


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
