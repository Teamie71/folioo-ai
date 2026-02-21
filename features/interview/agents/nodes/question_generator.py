"""QuestionGenerator 노드 - 인터뷰 질문 생성"""

import logging

from langchain_core.messages import AIMessage

from common.llm.client import get_llm
from features.interview.config.loader import (
    StageConfig,
    get_global_config,
    load_stage_config,
)

from ..prompts import (
    contextual_fixed_question_prompt,
    first_turn_prompt,
    generated_question_prompt,
)
from ..state import CollectedField, InterviewState
from .utils import _get_conversation_context

logger = logging.getLogger(__name__)


def _get_incomplete_fields(
    state: InterviewState,
    stage_config: StageConfig,
    completeness_threshold: float = 0.7,
) -> list[dict[str, str]]:
    """
    현재 단계에서 미수집 또는 불완전한 필드 목록 반환

    Args:
        state: 현재 상태
        stage_config: 단계 설정
        completeness_threshold: 완성도 임계값 (이하면 불완전으로 간주)

    Returns:
        미수집/불완전 필드 목록 [{"field_name": ..., "description": ...}, ...]
    """
    current_stage = state["current_stage"]
    stage_key = f"stage_{current_stage}"
    collected = state["collected_data"].get(stage_key, {})
    required = stage_config.required_fields
    incomplete = []
    for field_name, field_info in required.items():
        collected_field: CollectedField | None = collected.get(field_name)
        if collected_field is None:
            # 아예 수집되지 않음
            incomplete.append(
                {
                    "field_name": field_name,
                    "description": field_info["description"],
                }
            )
        elif collected_field["completeness"] < completeness_threshold:
            # 수집되었으나 불완전
            incomplete.append(
                {
                    "field_name": field_name,
                    "description": field_info["description"],
                }
            )
    return incomplete


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
    llm = get_llm(temperature=0.7)
    chain = first_turn_prompt | llm

    llm_error = None
    try:
        response = chain.invoke(
            {
                "experience_name": state["experience_name"],
                "fixed_question_content": fixed_question_content,
            }
        )
        question = response.content
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

    # 2. LLM 프롬프트 구성
    llm = get_llm(temperature=0.7)
    chain = contextual_fixed_question_prompt | llm

    # 3. LLM 호출 및 fallback 처리
    llm_error = None
    try:
        response = chain.invoke(
            {
                "experience_name": state["experience_name"],
                "fixed_q_used": progress["fixed_q_used"],
                "conversation_context": context,
                "fixed_question_content": fixed_question_content,
            }
        )
        question = response.content
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
    # 3. 진행 상황 정보
    progress = state["stage_progress"]
    remaining_questions = progress["generated_q_max"] - progress["generated_q_used"]
    # 4. LLM 호출
    llm = get_llm(temperature=0.7)
    chain = generated_question_prompt | llm
    llm_error = None
    try:
        response = chain.invoke(
            {
                "experience_name": state["experience_name"],
                "stage_name": stage_config.name,
                "conversation_context": context,
                "incomplete_fields": incomplete_fields_str,
                "remaining_questions": remaining_questions,
            }
        )
        question = response.content
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


def _should_skip_generated_questions(
    state: InterviewState,
    stage_config: StageConfig,
) -> bool:
    """
    생성 질문을 건너뛸 수 있는지 판단
    조건:
    - force_all_generated_questions가 False
    - 모든 required_fields가 충분히 수집됨 (completeness >= threshold)
    Args:
        state: 현재 상태
        stage_config: 단계 설정
    Returns:
        True면 생성 질문 건너뛰기 가능
    """
    if stage_config.force_all_generated_questions:
        return False
    incomplete_fields = _get_incomplete_fields(state, stage_config)
    return len(incomplete_fields) == 0


def run(state: InterviewState) -> InterviewState:
    """
    인터뷰 질문 생성 (초기 또는 분석 기반)

    플로우:
    - 첫 턴: 첫 고정 질문 생성
    - 고정 질문 단계: 순차적으로 고정 질문 생성
    - 생성 질문 단계: 미수집 필드 기반 동적 질문 생성
    - 질문 소진: 단계 완료 표시
    """

    # 1. 현재 단계 설정 로드
    stage_config = load_stage_config(state["current_stage"])
    progress = state["stage_progress"]

    # 2. 첫 턴 여부 판단
    is_first_turn = len(state["messages"]) == 0

    question: str
    llm_error: str | None
    updated_progress: dict

    if is_first_turn:
        # ===== 첫 턴 질문 생성 =====
        question, llm_error = _generate_first_turn_question(state, stage_config)
        updated_progress = {
            **progress,
            "fixed_q_used": 1,
        }

    elif progress["fixed_q_used"] < progress["fixed_q_total"]:
        # ===== 후속 고정 질문 생성 =====
        next_fixed_question_idx = progress["fixed_q_used"]
        fixed_question_raw = stage_config.fixed_questions[next_fixed_question_idx]

        question, llm_error = _generate_contextual_fixed_question(
            state=state,
            fixed_question_content=fixed_question_raw,
        )

        updated_progress = {
            **progress,
            "fixed_q_used": progress["fixed_q_used"] + 1,
        }

    elif progress["generated_q_used"] < progress["generated_q_max"]:
        # ===== 생성 질문 생성 =====
        # 생성 질문 건너뛰기 가능 여부 확인
        if _should_skip_generated_questions(state, stage_config):
            # 모든 필드 수집 완료 + 강제 소진 아님 -> 단계 완료
            logger.info(f"Stage {state['current_stage']}: 모든 필드 수집 완료, 생성 질문 건너뜀")
            return {
                **state,
                "stage_progress": {
                    **progress,
                    "is_complete": True,
                },
                "next_node": "analyst",  # Analyst가 단계 전환 처리
                "llm_error": None,
            }

        # 동적 질문 생성
        question, llm_error = _generate_dynamic_question(state, stage_config)
        updated_progress = {
            **progress,
            "generated_q_used": progress["generated_q_used"] + 1,
        }

    else:
        # ===== 질문 소진 =====
        logger.info(f"Stage {state['current_stage']}: 모든 질문 소진, 단계 완료")
        return {
            **state,
            "stage_progress": {
                **progress,
                "is_complete": True,
            },
            "next_node": "analyst",  # Analyst가 단계 전환 처리
            "llm_error": None,
        }

    # 4. 공통 반환 처리
    result_state: InterviewState = {
        **state,
        "messages": [AIMessage(content=question)],
        "stage_progress": updated_progress,
        "next_node": "end",
        "llm_error": llm_error,
    }

    return result_state
