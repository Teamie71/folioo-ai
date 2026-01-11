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
    - 사용자 답변에 대한 공감/반응 추가

    TODO: 실제 질문 생성 로직은 후속 이슈에서 구현
    - YAML 설정 로드
    - LLM 기반 동적 질문 생성
    - 대화 컨텍스트 관리
    """

    stage_config = load_stage_config(state["current_stage"])

    # 이전 답변에 대한 공감 메시지 생성
    acknowledgment = _generate_acknowledgment(state)

    # 고정 질문이 남아있는지 확인
    fixed_questions = stage_config.fixed_questions
    fixed_q_count = state["fixed_q_count"]

    if fixed_q_count < len(fixed_questions):
        # 고정 질문 전달
        question = _deliver_fixed_question(
            question=fixed_questions[fixed_q_count],
            stage_config=stage_config,
        )
        # 공감 + 질문 결합
        full_message = _combine_message(acknowledgment, question)
        return {
            **state,
            "messages": [AIMessage(content=full_message)],
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
        # 공감 + 질문 결합
        full_message = _combine_message(acknowledgment, question)
        return {
            **state,
            "messages": [AIMessage(content=full_message)],
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


def _generate_acknowledgment(state: InterviewState) -> str:
    """사용자의 마지막 답변에 대한 공감/반응 메시지 생성"""
    messages = state.get("messages", [])

    # 첫 질문이거나 사용자 답변이 없으면 공감 메시지 없음
    if not messages or state["fixed_q_count"] == 0:
        return ""

    # 마지막 사용자 메시지 찾기
    last_user_message = None
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_user_message = msg.content
            break

    if not last_user_message:
        return ""

    # LLM으로 공감 메시지 생성
    llm = get_llm(temperature=0.7)

    prompt = """당신은 친근하고 공감적인 인터뷰어입니다.
사용자의 답변에 대해 짧고 자연스러운 공감/반응을 생성하세요.

요구사항:
- 1-2문장으로 간결하게
- 사용자 답변의 핵심을 인정하고 공감
- 지나치게 형식적이거나 과장되지 않게
- 다음 질문으로 자연스럽게 이어질 수 있도록

예시:
- "아, 3년차 백엔드 개발자시군요! 좋습니다."
- "재미있는 프로젝트네요. 더 자세히 알고 싶어요."
- "그렇군요. 그 경험이 중요하게 작용했겠네요."
"""

    messages_for_llm = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": f"사용자 답변: {last_user_message}\n\n공감 메시지를 생성하세요.",
        },
    ]

    response = llm.invoke(messages_for_llm)
    return response.content.strip()


def _combine_message(acknowledgment: str, question: str) -> str:
    """공감 메시지와 질문을 자연스럽게 결합"""
    if not acknowledgment:
        return question

    return f"{acknowledgment}\n\n{question}"
