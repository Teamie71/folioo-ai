"""Analyst 프롬프트 및 응답 스키마 테스트"""

import pytest
from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError

from features.interview.agents.prompts import (
    AnalystFieldResult,
    AnalystResponse,
    ExtendedAnalystResponse,
    analyst_prompt,
    extended_analyst_prompt,
    overall_completion_prompt,
)

# ===== Pydantic 스키마 테스트 =====


class TestAnalystFieldResult:
    """AnalystFieldResult 스키마 테스트"""

    def test_valid_string_value(self):
        """문자열 값으로 생성 가능한지 테스트"""
        result = AnalystFieldResult(
            field_name="project_background",
            value="AI 에이전트 개발에 관심이 있어서 시작",
            completeness=0.7,
        )
        assert result.field_name == "project_background"
        assert result.value == "AI 에이전트 개발에 관심이 있어서 시작"
        assert result.completeness == 0.7

    def test_valid_list_value(self):
        """리스트 값으로 생성 가능한지 테스트"""
        result = AnalystFieldResult(
            field_name="work_categories",
            value=["프론트엔드 개발", "API 설계", "데이터 모델링"],
            completeness=0.8,
        )
        assert isinstance(result.value, list)
        assert len(result.value) == 3

    def test_null_value(self):
        """null 값 허용 테스트"""
        result = AnalystFieldResult(
            field_name="target_audience",
            value=None,
            completeness=0.0,
        )
        assert result.value is None
        assert result.completeness == 0.0

    def test_default_null_value(self):
        """value 기본값이 None인지 테스트"""
        result = AnalystFieldResult(
            field_name="target_audience",
            completeness=0.0,
        )
        assert result.value is None

    def test_completeness_boundary_min(self):
        """completeness 최솟값 경계 테스트"""
        result = AnalystFieldResult(field_name="test", value=None, completeness=0.0)
        assert result.completeness == 0.0

    def test_completeness_boundary_max(self):
        """completeness 최댓값 경계 테스트"""
        result = AnalystFieldResult(field_name="test", value="완전한 정보", completeness=1.0)
        assert result.completeness == 1.0

    def test_completeness_below_min_raises_error(self):
        """completeness가 0.0 미만이면 에러 발생 테스트"""
        with pytest.raises(ValidationError):
            AnalystFieldResult(field_name="test", value=None, completeness=-0.1)

    def test_completeness_above_max_raises_error(self):
        """completeness가 1.0 초과이면 에러 발생 테스트"""
        with pytest.raises(ValidationError):
            AnalystFieldResult(field_name="test", value=None, completeness=1.1)

    def test_missing_required_field_raises_error(self):
        """필수 필드 누락 시 에러 발생 테스트"""
        with pytest.raises(ValidationError):
            AnalystFieldResult(value="테스트", completeness=0.5)


class TestAnalystResponse:
    """AnalystResponse 스키마 테스트"""

    def test_valid_response(self):
        """정상 응답 생성 테스트"""
        response = AnalystResponse(
            fields=[
                AnalystFieldResult(
                    field_name="project_background",
                    value="AI 프로젝트",
                    completeness=0.7,
                ),
                AnalystFieldResult(
                    field_name="problem_definition",
                    value=None,
                    completeness=0.0,
                ),
            ]
        )
        assert len(response.fields) == 2
        assert response.fields[0].field_name == "project_background"
        assert response.fields[1].field_name == "problem_definition"

    def test_empty_fields_list(self):
        """빈 필드 목록 허용 테스트"""
        response = AnalystResponse(fields=[])
        assert len(response.fields) == 0


# ===== 프롬프트 템플릿 테스트 =====


class TestAnalystPrompt:
    """Analyst 프롬프트 템플릿 테스트"""

    def test_prompt_is_chat_prompt_template(self):
        """ChatPromptTemplate 인스턴스인지 테스트"""
        assert isinstance(analyst_prompt, ChatPromptTemplate)

    def test_prompt_has_required_input_variables(self):
        """필수 입력 변수가 포함되어 있는지 테스트"""
        expected_vars = {
            "experience_name",
            "current_stage",
            "stage_name",
            "conversation_context",
            "required_fields",
            "existing_collected_data",
            "retrieved_insights",
            "file_contexts",
        }
        assert expected_vars == set(analyst_prompt.input_variables)

    def test_prompt_formatting(self):
        """프롬프트 포맷팅이 정상 동작하는지 테스트"""
        messages = analyst_prompt.format_messages(
            experience_name="AI 에이전트 개발 프로젝트",
            current_stage=1,
            stage_name="프로젝트 개요 및 구조화",
            conversation_context="AI: 프로젝트 배경이 궁금합니다.\nUser: AI에 관심이 있어서요.",
            required_fields="- project_background: 이 활동을 시작하게 된 이유",
            existing_collected_data="없음",
            retrieved_insights="없음",
            file_contexts="없음",
        )
        assert len(messages) == 2
        assert "AI 에이전트 개발 프로젝트" in messages[0].content
        assert "프로젝트 개요 및 구조화" in messages[0].content
        assert "reasoning" not in messages[0].content

    def test_prompt_handles_empty_insights_and_files(self):
        """인사이트와 파일 컨텍스트가 비어있어도 정상 동작하는지 테스트"""
        messages = analyst_prompt.format_messages(
            experience_name="테스트 프로젝트",
            current_stage=2,
            stage_name="구체적 실행 과정",
            conversation_context="대화 내용",
            required_fields="- work_categories: 2개 이상의 업무 카테고리",
            existing_collected_data="",
            retrieved_insights="",
            file_contexts="",
        )
        assert len(messages) == 2
        assert "테스트 프로젝트" in messages[0].content

    def test_prompt_does_not_request_reasoning(self):
        """기본 Analyst 프롬프트는 reasoning 필드를 요구하지 않는다."""
        assert "`reasoning`" not in analyst_prompt.messages[0].prompt.template


class TestExtendedAnalystResponse:
    """ExtendedAnalystResponse 스키마 테스트"""

    def test_default_flags_are_false(self):
        """종료 의도·target 충족 플래그 기본값은 False다."""
        response = ExtendedAnalystResponse(fields=[])
        assert response.should_end_extended_mode is False
        assert response.last_target_satisfied is False

    def test_valid_response_with_flags(self):
        """플래그를 명시적으로 설정할 수 있다."""
        response = ExtendedAnalystResponse(
            fields=[
                AnalystFieldResult(
                    field_name="project_background",
                    value="보완된 배경",
                    completeness=0.8,
                )
            ],
            should_end_extended_mode=True,
            last_target_satisfied=True,
        )
        assert len(response.fields) == 1
        assert response.should_end_extended_mode is True
        assert response.last_target_satisfied is True


class TestExtendedAnalystPrompt:
    """연장 모드 Analyst 프롬프트 테스트"""

    def test_extended_prompt_does_not_request_reasoning(self):
        """연장 모드 프롬프트도 reasoning 필드를 요구하지 않는다."""
        assert "`reasoning`" not in extended_analyst_prompt.messages[0].prompt.template

    def test_extended_prompt_has_last_asked_target_variable(self):
        """연장 모드 프롬프트는 last_asked_target 입력 변수를 포함한다."""
        assert "last_asked_target" in extended_analyst_prompt.input_variables

    def test_extended_prompt_describes_end_intent_criteria(self):
        """연장 모드 프롬프트는 종료 의도 판단 기준을 포함한다."""
        template = extended_analyst_prompt.messages[0].prompt.template
        assert "should_end_extended_mode" in template
        # 추가 대화 전체 종료를 나타내는 true 예시
        assert "이제 추가 질문 다 그만할게요" in template

    def test_extended_prompt_includes_false_examples(self):
        """경험 서술·단일 target 회피는 종료 의도가 아니라는 false 예시를 포함한다."""
        template = extended_analyst_prompt.messages[0].prompt.template
        # 경험 서술에 포함된 '그만' 류 표현
        assert "그만 포기하지 않고" in template
        # 단일 target 회피 표현
        assert "패스" in template

    def test_extended_prompt_describes_last_target_judgement(self):
        """연장 모드 프롬프트는 last_target_satisfied 판정 지침을 포함한다."""
        template = extended_analyst_prompt.messages[0].prompt.template
        assert "last_target_satisfied" in template


class TestOverallCompletionPrompt:
    """전체 완료율 계산 프롬프트 테스트"""

    def test_prompt_is_chat_prompt_template(self):
        """ChatPromptTemplate 인스턴스인지 테스트"""
        assert isinstance(overall_completion_prompt, ChatPromptTemplate)

    def test_prompt_has_required_input_variables(self):
        """필수 입력 변수가 포함되어 있는지 테스트"""
        expected_vars = {"experience_name", "all_collected_data"}
        assert expected_vars == set(overall_completion_prompt.input_variables)

    def test_prompt_formatting(self):
        """프롬프트 포맷팅이 정상 동작하는지 테스트"""
        messages = overall_completion_prompt.format_messages(
            experience_name="AI 에이전트 개발 프로젝트",
            all_collected_data="stage_1: {...}, stage_2: {...}, stage_3: {...}, stage_4: {...}",
        )
        assert len(messages) == 2
        assert "AI 에이전트 개발 프로젝트" in messages[0].content
        assert "stage_1" in messages[0].content
