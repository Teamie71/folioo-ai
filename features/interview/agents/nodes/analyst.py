"""Analyst 노드 - 대화 분석 및 정보 추출"""

import copy
import logging

from common.llm.client import get_llm
from features.interview.agents.prompts.analyst import AnalystResponse, analyst_prompt
from features.interview.config.loader import get_global_config, load_stage_config

from ..state import CollectedField, InsightLog, InterviewState
from .utils import _get_conversation_context

logger = logging.getLogger(__name__)


def run(state: InterviewState) -> InterviewState:
    """
    대화 컨텍스트를 LLM으로 분석하여 collected_data 업데이트

    - 현재 단계의 required_fields에 대해 정보를 추출
    - 기존 데이터보다 completeness가 높은 경우에만 갱신
    - LLM 호출 실패 시 기존 데이터 유지

    TODO: 실제 분석 로직은 후속 이슈에서 구현
    - 완료율 계산
    - 단계 전환 로직
    """

    current_stage = state["current_stage"]
    stage_key = f"stage_{current_stage}"

    # 1. 설정 로드
    stage_config = load_stage_config(current_stage)
    global_config = get_global_config()

    # 2. 프롬프트 입력 변수 준비
    conversation_context = _get_conversation_context(
        state, max_messages=global_config.context_window_size
    )
    required_fields_str = _format_required_fields(stage_config.required_fields)
    existing_collected_str = _format_existing_collected_data(
        state["collected_data"].get(stage_key, {})
    )
    retrieved_insights_str = _format_retrieved_insights(state["retrieved_insights"])
    file_contexts_str = _format_file_contexts(state["file_contexts"])

    prompt_variables = {
        "experience_name": state["experience_name"],
        "current_stage": current_stage,
        "stage_name": stage_config.name,
        "conversation_context": conversation_context,
        "required_fields": required_fields_str,
        "existing_collected_data": existing_collected_str,
        "retrieved_insights": retrieved_insights_str,
        "file_contexts": file_contexts_str,
    }

    # 3. LLM 호출 (structured output)
    try:
        llm = get_llm(temperature=0.3)
        structured_llm = llm.with_structured_output(AnalystResponse)
        chain = analyst_prompt | structured_llm

        response: AnalystResponse = chain.invoke(prompt_variables)

        # 4. collected_data 업데이트 (병합)
        updated_collected_data = copy.deepcopy(state["collected_data"])
        stage_collected = updated_collected_data.get(stage_key, {})

        for field_result in response.fields:
            field_name = field_result.field_name

            # required_fields에 없는 필드는 무시 (LLM hallucination 방어)
            if field_name not in stage_config.required_fields:
                logger.warning("LLM이 required_fields에 없는 필드를 반환: %s", field_name)
                continue

            existing_field: CollectedField | None = stage_collected.get(field_name)

            if existing_field is None:
                # 기존 데이터 없음 → 신규 추가
                stage_collected[field_name] = CollectedField(
                    field_name=field_name,
                    description=stage_config.required_fields[field_name]["description"],
                    value=field_result.value,
                    completeness=field_result.completeness,
                )
            elif field_result.completeness > existing_field["completeness"]:
                # 새 completeness가 더 높음 → 갱신
                stage_collected[field_name] = CollectedField(
                    field_name=field_name,
                    description=existing_field["description"],
                    value=field_result.value,
                    completeness=field_result.completeness,
                )
            # else: 기존이 더 높거나 같음 → 유지

        updated_collected_data[stage_key] = stage_collected
        llm_error = None

    except Exception as e:
        logger.exception("Analyst LLM 호출 실패")
        updated_collected_data = state["collected_data"]
        llm_error = str(e)

    # 5. 상태 반환 (변경 필드)
    return {
        **state,
        "collected_data": updated_collected_data,
        "next_node": "question_generator",
        "llm_error": llm_error,
    }


def _format_required_fields(required_fields: dict[str, dict[str, str]]) -> str:
    """
    stages.yaml의 required_fields를 프롬프트용 문자열로 변환

    Args:
        required_fields: {"field_name": {"description": "..."}, ...}

    Returns:
        포맷팅된 문자열
    """
    lines = []
    for field_name, field_info in required_fields.items():
        lines.append(f"- {field_name}: {field_info['description']}")
    return "\n".join(lines)


def _format_existing_collected_data(collected: dict[str, CollectedField]) -> str:
    """
    기존 수집 데이터를 프롬프트용 문자열로 변환

    Args:
        collected: {"field_name": CollectedField, ...}

    Returns:
        포맷팅된 문자열
    """
    if not collected:
        return "수집된 데이터 없음"

    lines = []
    for field_name, field_data in collected.items():
        completeness = field_data["completeness"]
        value = field_data["value"]
        lines.append(f"- {field_name} (완성도: {completeness}): {value}")
    return "\n".join(lines)


def _format_retrieved_insights(insights: list[InsightLog]) -> str:
    """
    인사이트 로그를 프롬프트용 문자열로 변환

    Args:
        insights: InsightLog 리스트

    Returns:
        포맷팅된 문자열
    """
    if not insights:
        return "검색된 인사이트 없음"

    lines = []
    for insight in insights:
        lines.append(f"- [{insight['category']}] {insight['title']}: {insight['content']}")
    return "\n".join(lines)


def _format_file_contexts(file_contexts: list[str]) -> str:
    """
    파일 컨텍스트를 프롬프트용 문자열로 변환

    Args:
        file_contexts: 파일에서 추출된 텍스트 리스트

    Returns:
        포맷팅된 문자열
    """
    if not file_contexts:
        return "첨부 파일 없음"

    return "\n---\n".join(file_contexts)
