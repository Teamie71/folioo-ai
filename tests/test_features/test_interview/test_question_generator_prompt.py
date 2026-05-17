"""QuestionGenerator 프롬프트 테스트"""

from langchain_core.prompts import ChatPromptTemplate

from features.interview.agents.prompts import (
    contextual_fixed_question_prompt,
    extended_generated_question_prompt,
    first_turn_prompt,
    generated_question_prompt,
)


def test_first_turn_prompt_is_chat_prompt_template():
    """첫 턴 프롬프트는 ChatPromptTemplate 인스턴스다."""
    assert isinstance(first_turn_prompt, ChatPromptTemplate)


def test_contextual_fixed_question_prompt_has_retrieved_insights_variable():
    """후속 고정 질문 프롬프트에 retrieved_insights 변수가 포함된다."""
    expected_vars = {
        "experience_name",
        "fixed_q_used",
        "conversation_context",
        "retrieved_insights",
        "file_contexts",
        "fixed_question_content",
    }

    assert expected_vars == set(contextual_fixed_question_prompt.input_variables)


def test_generated_question_prompt_has_retrieved_insights_variable():
    """생성 질문 프롬프트에 retrieved_insights 변수가 포함된다."""
    expected_vars = {
        "experience_name",
        "stage_name",
        "conversation_context",
        "retrieved_insights",
        "file_contexts",
        "incomplete_fields",
        "remaining_questions",
    }

    assert expected_vars == set(generated_question_prompt.input_variables)


def test_extended_generated_question_prompt_has_retrieved_insights_variable():
    """연장 질문 프롬프트에 retrieved_insights 변수가 포함된다."""
    expected_vars = {
        "experience_name",
        "conversation_context",
        "retrieved_insights",
        "file_contexts",
        "selected_target",
        "remaining_turns",
    }

    assert expected_vars == set(extended_generated_question_prompt.input_variables)


def test_generated_question_prompt_mentions_insight_usage_rules():
    """생성 질문 프롬프트에 인사이트 활용 제약이 반영된다."""
    prompt_text = generated_question_prompt.messages[0].prompt.template

    assert "보조 컨텍스트" in prompt_text
    assert "대화 맥락과 미수집 필드" in prompt_text
    assert "그대로 복붙하지 마세요" in prompt_text


def test_extended_generated_question_prompt_focuses_on_selected_target():
    """추가 대화 프롬프트는 선택된 target 하나만 질문하도록 지시한다."""
    prompt_text = extended_generated_question_prompt.messages[0].prompt.template

    assert "선택된 target 하나" in prompt_text
    assert "내부 field_name이나 target id" in prompt_text
    assert "보조 컨텍스트" in prompt_text
    assert "낮은 completeness" not in prompt_text
    assert "completeness가 낮은 필드" not in prompt_text


def test_contextual_fixed_question_prompt_formats_retrieved_insights():
    """후속 고정 질문 프롬프트는 인사이트 문자열을 정상 포맷팅한다."""
    messages = contextual_fixed_question_prompt.format_messages(
        experience_name="AI 에이전트 프로젝트",
        fixed_q_used=1,
        conversation_context="AI: 첫 질문\n사용자: 답변",
        retrieved_insights="- [문제해결] 인사이트 제목",
        file_contexts="[파일: report.pdf]\n요약 내용",
        fixed_question_content="다음 질문 내용",
    )

    assert len(messages) == 2
    assert "- [문제해결] 인사이트 제목" in messages[0].content
    assert "[파일: report.pdf]\n요약 내용" in messages[0].content
    assert "다음 질문 내용" in messages[0].content
