"""Interviewer 노드 - 질문 생성 및 대화 진행"""

from langchain_core.messages import AIMessage, HumanMessage

from common.llm import get_llm
from features.interview.config.loader import load_stage_config

from ..prompts.interviewer import (
    GENERATE_QUESTION_SYSTEM,
)
from ..state import InterviewState


def run(state: InterviewState) -> InterviewState:
    """
    고정 질문 또는 생성 질문 수행
    - stages.yaml에서 고정 질문 로드
    - LLM 기반 추가 질문 생성

    TODO: 실제 질문 생성 로직은 후속 이슈에서 구현
    - YAML 설정 로드
    - LLM 기반 동적 질문 생성
    - 대화 컨텍스트 관리
    """

    stage_config = load_stage_config(state["current_stage"])

    # 고정 질문이 남아있는지 확인
    fixed_questions = stage_config.fixed_questions
    fixed_q_count = state["fixed_q_count"]

    if fixed_q_count < len(fixed_questions):
        # 고정 질문 전달
        question = _deliver_fixed_question(
            question=fixed_questions[fixed_q_count],
            stage_config=stage_config,
        )
        return {
            **state,
            "messages": [AIMessage(content=question)],
            "fixed_q_count": fixed_q_count + 1,
            "next_node": "supervisor",
        }

    # 추가 질문 생성 필요 여부 확인
    generated_q_count = state["generated_q_count"]
    max_generated = stage_config.max_generated_questions
    force_all = stage_config.force_all_generated

    if generated_q_count < max_generated and (force_all or _has_missing_fields(state)):
        # LLM으로 동적 질문 생성
        question = _generate_followup_question(state, stage_config)
        return {
            **state,
            "messages": [AIMessage(content=question)],
            "generated_q_count": generated_q_count + 1,
            "next_node": "supervisor",
        }


def _deliver_fixed_question(question: str, stage_config) -> str:
    """고정 질문을 자연스럽게 전달"""
    # 현재는 그대로 반환, 추후 LLM 기반 자연스러운 변형 고민 중
    return question


def _has_missing_fields(state: InterviewState) -> bool:
    """수집되지 않은 필수 필드가 있는지 확인"""
    stage_config = load_stage_config(state["current_stage"])
    collected = state.get("collected_data", {}).get(f"stage_{state['current_stage']}", {})

    for field in stage_config.required_fields:
        if field not in collected or not collected[field]:
            return True
    return False


def _generate_followup_question(state: InterviewState, stage_config) -> str:
    """LLM으로 후속 질문 생성"""
    llm = get_llm(temperature=0.7)

    # 수집되지 않은 필드 확인
    missing_fields = _get_missing_fields(state, stage_config)

    # 대화 히스토리 구성
    conversation = _format_conversation(state["messages"][-10:])  # 최근 10개

    prompt = GENERATE_QUESTION_SYSTEM.format(
        stage_name=stage_config.name,
        stage_description=stage_config.description,
        missing_fields="\n".join(f"- {f}: {desc}" for f, desc in missing_fields.items()),
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"대화 맥락:\n{conversation}\n\n후속 질문을 생성해주세요."},
    ]

    response = llm.invoke(messages)
    return response.content


def _get_missing_fields(state: InterviewState, stage_config) -> dict:
    """수집되지 않은 필수 필드 목록 반환"""
    collected = state.get("collected_data", {}).get(f"stage_{state['current_stage']}", {})
    missing = {}

    for field, info in stage_config.required_fields.items():
        if field not in collected or not collected[field]:
            missing[field] = info.get("description", "")

    return missing


def _format_conversation(messages: list) -> str:
    """메시지 리스트를 문자열로 포맷"""
    lines = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            lines.append(f"사용자: {msg.content}")
        elif isinstance(msg, AIMessage):
            lines.append(f"AI: {msg.content}")
    return "\n".join(lines)
