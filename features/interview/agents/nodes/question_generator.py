"""QuestionGenerator 노드 - 인터뷰 질문 생성"""

import json
import logging

from langchain_core.messages import AIMessage

from common.llm.client import get_analyst_llm, get_llm
from features.interview.config.loader import (
    StageConfig,
    get_all_stages,
    get_global_config,
    load_stage_config,
)

from ..prompts import (
    AdditionalTargetSufficiencyResponse,
    additional_target_sufficiency_prompt,
    contextual_fixed_question_prompt,
    extended_end_guide_prompt,
    extended_generated_question_prompt,
    first_turn_prompt,
    generated_question_prompt,
)
from ..state import InterviewState, ensure_interview_state_defaults
from .utils import (
    AdditionalQuestionTargetWithPriority,
    _all_additional_question_targets_satisfied,
    _ensure_additional_question_target_statuses,
    _flatten_additional_question_targets,
    _format_additional_target_sufficiency_inputs,
    _format_file_contexts,
    _format_incomplete_fields,
    _format_retrieved_insights,
    _format_selected_additional_question_target,
    _get_conversation_context,
    _get_incomplete_fields,
    _increment_additional_question_target_asked_count,
    _select_next_additional_question_target,
)

logger = logging.getLogger(__name__)

_ADDITIONAL_CONVERSATION_COMPLETE_MESSAGE = (
    "이미 필요한 추가 확인 사항이 충분히 정리되어 있어 추가 질문 없이 인터뷰를 마무리하겠습니다."
)
_EXTENDED_END_GUIDE_FALLBACK_MESSAGE = (
    "좋아요. 입력창 왼쪽의 꽃잎 모양에 커서를 올리면 종료 버튼이 나타나요!"
)


def _invoke_with_retry(
    chain,
    prompt_variables: dict[str, object],
    max_retries_per_question: int,
) -> str:
    """질문 생성 LLM 호출 재시도"""
    last_error: Exception | None = None
    for _ in range(max_retries_per_question + 1):
        try:
            response = chain.invoke(prompt_variables)
            return response.content
        except Exception as exc:
            last_error = exc
    if last_error is None:
        raise RuntimeError("질문 생성 호출에 실패했습니다.")
    raise last_error


def _generate_first_turn_question(
    state: InterviewState,
    stage_config: StageConfig,
) -> tuple[str, str | None]:
    """
    첫 턴 질문 생성

    Args:
        state: 현재 상태
        stage_config: 단계 설정

    Returns:
        (질문 내용, LLM 에러 메시지)
    """

    # 1. 첫 고정 질문 내용 가져오기
    if not stage_config.fixed_questions:
        raise ValueError(f"Stage {state['current_stage']}에 고정 질문이 설정되지 않았습니다.")
    fixed_question_raw = stage_config.fixed_questions[0]

    # 2. 플레이스홀더 치환
    fixed_question_content = fixed_question_raw.replace("[경험명]", state["experience_name"])

    # 3. LLM으로 자연스러운 질문 생성
    global_config = get_global_config()
    llm = get_llm(temperature=0.7)
    chain = first_turn_prompt | llm

    llm_error = None
    try:
        question = _invoke_with_retry(
            chain=chain,
            prompt_variables={
                "experience_name": state["experience_name"],
                "fixed_question_content": fixed_question_content,
            },
            max_retries_per_question=global_config.max_retries_per_question,
        )
    except Exception as e:
        # LLM 호출 실패 시 고정 질문을 fallback으로 사용
        # TODO: 고정 질문 그대로 사용 or 질문 생성 재시도 결정
        logger.exception("LLM 호출 실패")
        question = fixed_question_content
        llm_error = str(e)
    return (question, llm_error)


def _generate_contextual_fixed_question(
    state: InterviewState,
    fixed_question_content: str,
) -> tuple[str, str | None]:
    """
    대화 맥락을 반영한 후속 고정 질문 생성

    Args:
        state: 현재 상태
        fixed_question_content: 고정 질문 내용

    Returns:
        (질문 내용, LLM 에러 메시지)
    """
    # 1. 이전 대화 컨텍스트 추출
    global_config = get_global_config()
    context = _get_conversation_context(state, max_messages=global_config.context_window_size)
    progress = state["stage_progress"]
    retrieved_insights_str = _format_retrieved_insights(state["retrieved_insights"])
    file_contexts_str = _format_file_contexts(state["file_contexts"])

    # 2. LLM 프롬프트 구성
    llm = get_llm(temperature=0.7)
    chain = contextual_fixed_question_prompt | llm

    # 3. LLM 호출 및 fallback 처리
    llm_error = None
    try:
        question = _invoke_with_retry(
            chain=chain,
            prompt_variables={
                "experience_name": state["experience_name"],
                "fixed_q_used": progress["fixed_q_used"],
                "conversation_context": context,
                "retrieved_insights": retrieved_insights_str,
                "file_contexts": file_contexts_str,
                "fixed_question_content": fixed_question_content,
            },
            max_retries_per_question=global_config.max_retries_per_question,
        )
    except Exception as e:
        logger.exception("LLM 호출 실패")
        question = fixed_question_content
        llm_error = str(e)

    return (question, llm_error)


def _generate_dynamic_question(
    state: InterviewState,
    stage_config: StageConfig,
) -> tuple[str, str | None]:
    """
    대화 맥락과 미수집 필드 기반으로 동적 질문 생성
    Args:
        state: 현재 상태
        stage_config: 단계 설정
    Returns:
        (질문 내용, LLM 에러 메시지)
    """
    # 1. 이전 대화 컨텍스트 추출
    global_config = get_global_config()
    context = _get_conversation_context(state, max_messages=global_config.context_window_size)
    # 2. 미수집/불완전 필드 파악
    incomplete_fields = _get_incomplete_fields(state, stage_config)
    incomplete_fields_str = _format_incomplete_fields(incomplete_fields)
    retrieved_insights_str = _format_retrieved_insights(state["retrieved_insights"])
    file_contexts_str = _format_file_contexts(state["file_contexts"])
    # 3. 진행 상황 정보
    progress = state["stage_progress"]
    remaining_questions = progress["generated_q_max"] - progress["generated_q_used"]
    # 4. LLM 호출
    llm = get_llm(temperature=0.7)
    chain = generated_question_prompt | llm
    llm_error = None
    try:
        question = _invoke_with_retry(
            chain=chain,
            prompt_variables={
                "experience_name": state["experience_name"],
                "stage_name": stage_config.name,
                "conversation_context": context,
                "incomplete_fields": incomplete_fields_str,
                "retrieved_insights": retrieved_insights_str,
                "file_contexts": file_contexts_str,
                "remaining_questions": remaining_questions,
            },
            max_retries_per_question=global_config.max_retries_per_question,
        )
    except Exception as e:
        logger.exception("LLM 호출 실패: 생성 질문")
        # fallback: 미수집 필드 중 첫 번째에 대한 기본 질문
        if incomplete_fields:
            field = incomplete_fields[0]
            question = f"'{field['description']}'에 대해 조금 더 자세히 말씀해 주시겠어요?"
        else:
            question = "혹시 더 추가하고 싶은 내용이 있으신가요?"
        llm_error = str(e)
    return (question, llm_error)


def _pre_evaluate_additional_question_targets(
    state: InterviewState,
    targets: list[AdditionalQuestionTargetWithPriority],
) -> tuple[dict[str, bool], str | None]:
    """기존 정규 답변 기준으로 추가 질문 target 충분성을 한 번에 판정한다."""
    if not targets:
        return {}, None

    targets_str = _format_additional_target_sufficiency_inputs(targets, state["collected_data"])
    collected_data_str = json.dumps(
        state["collected_data"],
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    try:
        llm = get_analyst_llm(temperature=0.3)
        structured_llm = llm.with_structured_output(AdditionalTargetSufficiencyResponse)
        chain = additional_target_sufficiency_prompt | structured_llm
        response: AdditionalTargetSufficiencyResponse = chain.invoke(
            {
                "experience_name": state["experience_name"],
                "collected_data": collected_data_str,
                "targets": targets_str,
            }
        )
    except Exception as exc:
        logger.exception("추가 질문 target 사전 판정 실패")
        return {}, str(exc)

    target_ids = {target["target"] for target in targets}
    result: dict[str, bool] = {}
    for target_result in response.targets:
        if target_result.target not in target_ids:
            logger.warning("LLM이 설정에 없는 추가 질문 target을 반환: %s", target_result.target)
            continue
        result[target_result.target] = target_result.is_satisfied

    return result, None


def _generate_extended_question(
    state: InterviewState,
    selected_target: AdditionalQuestionTargetWithPriority,
) -> tuple[str, str | None]:
    """선택된 target 하나에 대한 추가 대화 질문 생성"""
    global_config = get_global_config()
    context = _get_conversation_context(state, max_messages=global_config.context_window_size)
    selected_target_str = _format_selected_additional_question_target(selected_target)
    retrieved_insights_str = _format_retrieved_insights(state["retrieved_insights"])
    file_contexts_str = _format_file_contexts(state["file_contexts"])

    remaining_turns = state["extension_turns_max"] - state["extension_turns_used"]

    llm = get_llm(temperature=0.7)
    chain = extended_generated_question_prompt | llm
    llm_error = None

    try:
        question = _invoke_with_retry(
            chain=chain,
            prompt_variables={
                "experience_name": state["experience_name"],
                "conversation_context": context,
                "selected_target": selected_target_str,
                "retrieved_insights": retrieved_insights_str,
                "file_contexts": file_contexts_str,
                "remaining_turns": remaining_turns,
            },
            max_retries_per_question=global_config.max_retries_per_question,
        )
    except Exception as e:
        logger.exception("LLM 호출 실패: 추가 대화 질문")
        question = (
            f"좋아요. {selected_target['label']}에 대해 조금 더 구체적으로 말씀해 주시겠어요? "
            f"{selected_target['question_hint']}"
        )
        llm_error = str(e)

    return (question, llm_error)


def _generate_extended_end_guide(state: InterviewState) -> tuple[str, str | None]:
    """사용자의 종료 의도에 대해 종료 버튼 안내 문구를 생성한다."""
    global_config = get_global_config()
    context = _get_conversation_context(state, max_messages=global_config.context_window_size)

    llm = get_llm(temperature=0.7)
    chain = extended_end_guide_prompt | llm
    llm_error = None

    try:
        guide_message = _invoke_with_retry(
            chain=chain,
            prompt_variables={
                "experience_name": state["experience_name"],
                "conversation_context": context,
            },
            max_retries_per_question=global_config.max_retries_per_question,
        )
    except Exception as e:
        logger.exception("LLM 호출 실패: 추가 대화 종료 버튼 안내")
        guide_message = _EXTENDED_END_GUIDE_FALLBACK_MESSAGE
        llm_error = str(e)

    return (guide_message, llm_error)


def _complete_extended_mode(
    state: InterviewState,
    llm_error: str | None = None,
) -> InterviewState:
    """추가 대화를 안내 문구와 함께 종료한다."""
    return {
        **state,
        "messages": [AIMessage(content=_ADDITIONAL_CONVERSATION_COMPLETE_MESSAGE)],
        "is_extended_mode": False,
        "all_stages_complete": True,
        "next_node": "end",
        "llm_error": llm_error,
    }


def _run_extended_mode(state: InterviewState) -> InterviewState:
    """추가 대화 분기 처리"""
    global_config = get_global_config()
    all_stage_configs = get_all_stages()
    targets = _flatten_additional_question_targets(global_config, all_stage_configs)
    statuses = _ensure_additional_question_target_statuses(state, targets)
    updated_state: InterviewState = {
        **state,
        "additional_question_target_statuses": statuses,
    }
    llm_error = None

    if updated_state["pending_extended_end_guide"]:
        guide_message, llm_error = _generate_extended_end_guide(updated_state)
        return {
            **updated_state,
            "messages": [AIMessage(content=guide_message)],
            "all_stages_complete": False,
            "is_extended_mode": True,
            "pending_extended_end_guide": False,
            "next_node": "end",
            "llm_error": llm_error,
        }

    if not updated_state["additional_question_pre_evaluated"]:
        pre_evaluation, llm_error = _pre_evaluate_additional_question_targets(
            updated_state,
            targets,
        )
        statuses = {
            target_id: {
                **status,
                "is_satisfied": pre_evaluation.get(target_id, status["is_satisfied"]),
            }
            for target_id, status in statuses.items()
        }
        updated_state = {
            **updated_state,
            "additional_question_target_statuses": statuses,
            "additional_question_pre_evaluated": True,
        }

    if _all_additional_question_targets_satisfied(updated_state, targets):
        return _complete_extended_mode(updated_state, llm_error=llm_error)

    if updated_state["extension_turns_used"] >= updated_state["extension_turns_max"]:
        return _complete_extended_mode(updated_state, llm_error=llm_error)

    selected_target = _select_next_additional_question_target(updated_state, targets)
    if selected_target is None:
        return _complete_extended_mode(updated_state, llm_error=llm_error)

    question, question_llm_error = _generate_extended_question(updated_state, selected_target)
    merged_llm_error = llm_error
    if question_llm_error:
        merged_llm_error = (
            f"{llm_error} | {question_llm_error}" if llm_error else question_llm_error
        )

    updated_statuses = _increment_additional_question_target_asked_count(
        updated_state["additional_question_target_statuses"],
        selected_target["target"],
    )

    return {
        **updated_state,
        "messages": [AIMessage(content=question)],
        "additional_question_target_statuses": updated_statuses,
        "current_additional_question_target_id": selected_target["target"],
        "extension_turns_used": updated_state["extension_turns_used"] + 1,
        "next_node": "end",
        "llm_error": merged_llm_error,
    }


def run(state: InterviewState) -> InterviewState:
    """
    인터뷰 질문 생성 (초기 또는 분석 기반)

    플로우:
    - 첫 턴: 첫 고정 질문 생성
    - 고정 질문 단계: 순차적으로 고정 질문 생성
    - 질문 소진: 안전한 fallback 질문 생성
    """

    normalized_state = ensure_interview_state_defaults(state)

    if normalized_state["is_extended_mode"]:
        return _run_extended_mode(normalized_state)

    # 1. 현재 단계 설정 로드
    stage_config = load_stage_config(normalized_state["current_stage"])
    progress = normalized_state["stage_progress"]

    # 2. 첫 질문 생성 여부 판단
    is_first_turn = normalized_state["turn_number"] == 0

    question: str
    llm_error: str | None
    updated_progress: dict

    if is_first_turn:
        # ===== 첫 턴 질문 생성 =====
        question, llm_error = _generate_first_turn_question(normalized_state, stage_config)
        updated_progress = {
            **progress,
            "fixed_q_used": 1,
        }

    elif progress["fixed_q_used"] < progress["fixed_q_total"]:
        # ===== 후속 고정 질문 생성 =====
        next_fixed_question_idx = progress["fixed_q_used"]
        fixed_question_raw = stage_config.fixed_questions[next_fixed_question_idx]

        question, llm_error = _generate_contextual_fixed_question(
            state=normalized_state,
            fixed_question_content=fixed_question_raw,
        )

        updated_progress = {
            **progress,
            "fixed_q_used": progress["fixed_q_used"] + 1,
        }

    else:
        # ===== 질문 소진 (방어 로직) =====
        logger.warning(
            "Stage %s: QuestionGenerator가 질문 소진 상태로 호출되어 fallback 질문을 생성합니다.",
            normalized_state["current_stage"],
        )
        question = "혹시 더 추가하고 싶은 내용이 있으신가요?"
        llm_error = None
        updated_progress = {
            **progress,
        }

    # 4. 공통 반환 처리
    result_state: InterviewState = {
        **normalized_state,
        "messages": [AIMessage(content=question)],
        "stage_progress": updated_progress,
        "next_node": "end",
        "llm_error": llm_error,
    }

    return result_state
