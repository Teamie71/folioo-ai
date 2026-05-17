"""Analyst 노드 - 대화 분석 및 정보 추출"""

import copy
import json
import logging
import re
import time

from httpx import ConnectError, ConnectTimeout, ReadTimeout, RemoteProtocolError

from common.llm.client import get_analyst_llm
from features.interview.agents.prompts.analyst import (
    AnalystResponse,
    analyst_prompt,
    extended_analyst_prompt,
    overall_completion_prompt,
)
from features.interview.config.loader import (
    StageConfig,
    get_all_stages,
    get_global_config,
    load_stage_config,
)

from ..state import CollectedField, InterviewState
from .utils import (
    _all_additional_question_targets_satisfied,
    _ensure_additional_question_target_statuses,
    _flatten_additional_question_targets,
    _format_file_contexts,
    _format_retrieved_insights,
    _get_conversation_context,
    _has_askable_additional_question_target,
)

logger = logging.getLogger(__name__)

_RETRYABLE_ANALYST_ERRORS = (
    RemoteProtocolError,
    ReadTimeout,
    ConnectTimeout,
    ConnectError,
)
_ANALYST_MAX_RETRIES = 2
_ANALYST_RETRY_DELAY_SECONDS = 1


def _is_retryable_analyst_error(error: Exception) -> bool:
    """직접 발생한 예외와 래핑된 전송 계층 예외를 모두 판별한다."""

    current_error: BaseException | None = error
    while current_error is not None:
        if isinstance(current_error, _RETRYABLE_ANALYST_ERRORS):
            return True
        current_error = current_error.__cause__ or current_error.__context__
    return False


def run(state: InterviewState) -> InterviewState:
    """
    대화 컨텍스트를 LLM으로 분석하여 collected_data 업데이트

    - 현재 단계의 required_fields에 대해 정보를 추출
    - 기존 데이터보다 completeness가 높은 경우에만 갱신
    - LLM 호출 실패 시 기존 데이터 유지

    TODO: 마무리 멘트가 필요하면 end 대신 question_generator로 라우팅하도록 수정
    """

    if state["is_extended_mode"]:
        return _run_extended_mode(state)

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
        llm = get_analyst_llm(temperature=0.3)
        structured_llm = llm.with_structured_output(AnalystResponse)
        chain = analyst_prompt | structured_llm

        response: AnalystResponse = _invoke_with_retry(chain, prompt_variables)

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
                    description=stage_config.required_fields[field_name].get("description", ""),
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
        updated_collected_data = copy.deepcopy(state["collected_data"])
        llm_error = str(e)

    # 5. 단계 완료 여부에 따른 라우팅 (Analyst가 직접 판단)
    progress = state["stage_progress"]
    is_stage_complete = _is_stage_complete(
        state=state,
        stage_config=stage_config,
        collected_data=updated_collected_data,
        enable_dynamic_followup=global_config.enable_dynamic_followup,
    )
    updated_progress = {
        **progress,
        "is_complete": is_stage_complete,
    }

    if is_stage_complete:
        if current_stage < 4:
            next_stage = current_stage + 1
            next_stage_config = load_stage_config(next_stage)
            return {
                **state,
                "current_stage": next_stage,
                "stage_progress": {
                    "fixed_q_used": 0,
                    "fixed_q_total": len(next_stage_config.fixed_questions),
                    "generated_q_used": 0,
                    "generated_q_max": next_stage_config.max_generated_questions,
                    "force_all_generated_q": next_stage_config.force_all_generated_questions,
                    "is_complete": False,
                },
                "collected_data": updated_collected_data,
                "next_node": "question_generator",
                "llm_error": llm_error,
            }

        overall_completion_percentage, completion_llm_error = (
            _calculate_overall_completion_percentage(
                state["experience_name"],
                updated_collected_data,
            )
        )
        merged_llm_error = llm_error
        if completion_llm_error:
            merged_llm_error = (
                f"{llm_error} | {completion_llm_error}" if llm_error else completion_llm_error
            )

        return {
            **state,
            "stage_progress": updated_progress,
            "collected_data": updated_collected_data,
            "all_stages_complete": True,
            "overall_completion_percentage": overall_completion_percentage,
            "next_node": "end",  # TODO: 마무리 멘트가 필요하면 question_generator로 변경
            "llm_error": merged_llm_error,
        }

    # 6. 상태 반환 (변경 필드)
    return {
        **state,
        "stage_progress": updated_progress,
        "collected_data": updated_collected_data,
        "next_node": "question_generator",
        "llm_error": llm_error,
    }


def _run_extended_mode(state: InterviewState) -> InterviewState:
    """연장 모드에서 전체 4단계 데이터를 분석하고 다음 라우팅을 결정"""
    global_config = get_global_config()
    all_stage_configs = get_all_stages()

    conversation_context = _get_conversation_context(
        state, max_messages=global_config.context_window_size
    )
    all_required_fields_str = _format_all_required_fields(all_stage_configs)
    existing_collected_str = _format_all_stages_collected_data(
        state["collected_data"],
        all_stage_configs,
    )
    retrieved_insights_str = _format_retrieved_insights(state["retrieved_insights"])
    file_contexts_str = _format_file_contexts(state["file_contexts"])

    prompt_variables = {
        "experience_name": state["experience_name"],
        "conversation_context": conversation_context,
        "all_required_fields": all_required_fields_str,
        "existing_collected_data": existing_collected_str,
        "retrieved_insights": retrieved_insights_str,
        "file_contexts": file_contexts_str,
    }

    try:
        llm = get_analyst_llm(temperature=0.3)
        structured_llm = llm.with_structured_output(AnalystResponse)
        chain = extended_analyst_prompt | structured_llm

        response: AnalystResponse = _invoke_with_retry(chain, prompt_variables)

        updated_collected_data = copy.deepcopy(state["collected_data"])
        for field_result in response.fields:
            field_name = field_result.field_name
            field_location = _find_stage_for_field(field_name, all_stage_configs)

            if field_location is None:
                logger.warning("LLM이 전체 required_fields에 없는 필드를 반환: %s", field_name)
                continue

            stage_number, stage_config = field_location
            stage_key = f"stage_{stage_number}"
            stage_collected = updated_collected_data.get(stage_key, {})
            existing_field: CollectedField | None = stage_collected.get(field_name)
            field_description = stage_config.required_fields[field_name].get("description", "")

            if existing_field is None:
                stage_collected[field_name] = CollectedField(
                    field_name=field_name,
                    description=field_description,
                    value=field_result.value,
                    completeness=field_result.completeness,
                )
            elif field_result.completeness > existing_field["completeness"]:
                stage_collected[field_name] = CollectedField(
                    field_name=field_name,
                    description=existing_field["description"] or field_description,
                    value=field_result.value,
                    completeness=field_result.completeness,
                )

            updated_collected_data[stage_key] = stage_collected

        llm_error = None

    except Exception as e:
        logger.exception("Analyst 연장 모드 LLM 호출 실패")
        updated_collected_data = copy.deepcopy(state["collected_data"])
        llm_error = str(e)

    targets = _flatten_additional_question_targets(global_config, all_stage_configs)
    statuses = _ensure_additional_question_target_statuses(state, targets)
    state_with_statuses: InterviewState = {
        **state,
        "additional_question_target_statuses": statuses,
    }

    should_end_extended_mode = (
        state["extension_turns_used"] >= state["extension_turns_max"]
        or _all_additional_question_targets_satisfied(state_with_statuses, targets)
        or not _has_askable_additional_question_target(state_with_statuses, targets)
    )

    if should_end_extended_mode:
        return {
            **state_with_statuses,
            "collected_data": updated_collected_data,
            "all_stages_complete": True,
            "is_extended_mode": False,
            "next_node": "end",
            "llm_error": llm_error,
        }

    return {
        **state_with_statuses,
        "collected_data": updated_collected_data,
        "all_stages_complete": False,
        "next_node": "question_generator",
        "llm_error": llm_error,
    }


def _is_stage_complete(
    state: InterviewState,
    stage_config: StageConfig,
    collected_data: dict[str, dict[str, CollectedField]],
    enable_dynamic_followup: bool,
) -> bool:
    """현재 단계 완료 여부 판단"""
    progress = state["stage_progress"]
    return progress["fixed_q_used"] >= progress["fixed_q_total"]


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
        lines.append(f"- {field_name}: {field_info.get('description', '')}")
    return "\n".join(lines)


def _format_all_required_fields(all_stage_configs: dict[int, StageConfig]) -> str:
    """전체 단계의 required_fields를 프롬프트용 문자열로 변환"""
    lines = []
    for stage_number in sorted(all_stage_configs):
        stage_config = all_stage_configs[stage_number]
        lines.append(f"[stage_{stage_number} - {stage_config.name}]")
        for field_name, field_info in stage_config.required_fields.items():
            lines.append(f"- {field_name}: {field_info.get('description', '')}")
        lines.append("")

    return "\n".join(lines).strip()


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


def _format_all_stages_collected_data(
    collected_data: dict[str, dict[str, CollectedField]],
    all_stage_configs: dict[int, StageConfig],
) -> str:
    """전체 단계의 수집 데이터를 프롬프트용 문자열로 변환"""
    lines = []
    for stage_number in sorted(all_stage_configs):
        stage_key = f"stage_{stage_number}"
        stage_name = all_stage_configs[stage_number].name
        lines.append(f"[stage_{stage_number} - {stage_name}]")
        lines.append(_format_existing_collected_data(collected_data.get(stage_key, {})))
        lines.append("")

    return "\n".join(lines).strip()


def _find_stage_for_field(
    field_name: str,
    all_stage_configs: dict[int, StageConfig],
) -> tuple[int, StageConfig] | None:
    """field_name이 속한 단계를 반환"""
    for stage_number, stage_config in all_stage_configs.items():
        if field_name in stage_config.required_fields:
            return stage_number, stage_config

    return None


def _calculate_overall_completion_percentage(
    experience_name: str,
    collected_data: dict[str, dict[str, CollectedField]],
) -> tuple[float, str | None]:
    """4단계 완료 시 전체 완료율을 LLM으로 계산"""
    try:
        all_collected_data = json.dumps(collected_data, ensure_ascii=False, indent=2, default=str)

        llm = get_analyst_llm(temperature=0.3)
        chain = overall_completion_prompt | llm
        response = _invoke_with_retry(
            chain,
            {
                "experience_name": experience_name,
                "all_collected_data": all_collected_data,
            },
        )
        response_text = str(response.content).strip()
        match = re.fullmatch(r"\d+(?:\.\d+)?", response_text)
        if not match:
            raise ValueError("전체 완료율 숫자 파싱에 실패했습니다.")
        score = float(match.group())
        if not 0.0 <= score <= 100.0:
            raise ValueError(f"전체 완료율 범위 초과: {score}")
        return score, None
    except Exception as e:
        logger.exception("전체 완료율 계산 실패")
        return 0.0, str(e)


def _invoke_with_retry(chain, payload: dict, max_retries: int = _ANALYST_MAX_RETRIES):
    """Analyst LLM 호출에 한해 네트워크 계열 예외를 제한적으로 재시도한다."""

    for attempt in range(max_retries + 1):
        try:
            return chain.invoke(payload)
        except Exception as exc:
            if not _is_retryable_analyst_error(exc) or attempt >= max_retries:
                raise
            logger.warning(
                "Analyst LLM 호출 재시도 예정 (%s/%s): %s",
                attempt + 1,
                max_retries,
                exc.__class__.__name__,
            )
            time.sleep(_ANALYST_RETRY_DELAY_SECONDS)
