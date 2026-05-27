"""포트폴리오 생성 프롬프트 테스트"""

from langchain_core.prompts import ChatPromptTemplate

from features.portfolio.prompts import format_collected_data_for_prompt, portfolio_generator_prompt


def test_format_collected_data_for_prompt_marks_uncollected():
    """빈 필드를 수집되지 않음으로 표시하는지 테스트"""
    formatted = format_collected_data_for_prompt(
        {
            "stage_1": {
                "project_background": {
                    "value": "사용자 이탈 문제를 줄이기 위해 시작했습니다.",
                    "completeness": 0.8,
                },
                "problem_definition": {
                    "value": None,
                    "completeness": 0.0,
                },
            },
        }
    )

    assert "project_background" in formatted
    assert "problem_definition" in formatted
    assert "수집되지 않음" in formatted


def test_format_collected_data_for_prompt_numbers_list_values():
    """리스트 타입 필드를 번호 매기기로 변환하는지 테스트"""
    formatted = format_collected_data_for_prompt(
        {
            "stage_2": {
                "work_categories": {
                    "value": ["요구사항 분석", "API 설계", "배포 자동화"],
                    "completeness": 1.0,
                }
            },
            "stage_3": {
                "problem_episodes": {
                    "value": ["초기 응답 속도 저하", "데이터 정합성 이슈"],
                    "completeness": 0.9,
                }
            },
        }
    )

    assert "1. 요구사항 분석" in formatted
    assert "2. API 설계" in formatted
    assert "1. 초기 응답 속도 저하" in formatted
    assert "2. 데이터 정합성 이슈" in formatted


def test_portfolio_prompt_is_chat_prompt_template():
    """ChatPromptTemplate 인스턴스인지 테스트"""
    assert isinstance(portfolio_generator_prompt, ChatPromptTemplate)


def test_portfolio_prompt_has_required_input_variables():
    """필수 입력 변수가 포함되어 있는지 테스트"""
    expected_vars = {"experience_name", "collected_data_text", "section_mapping_guide"}
    assert expected_vars == set(portfolio_generator_prompt.input_variables)


def test_portfolio_prompt_formatting_contains_guidelines():
    """프롬프트 포맷팅 시 핵심 지침이 포함되는지 테스트"""
    messages = portfolio_generator_prompt.format_messages(
        experience_name="결제 전환율 개선 프로젝트",
        collected_data_text="stage_1 ~ stage_4 데이터",
        section_mapping_guide="- description: stage_1 데이터를 중심으로 작성",
    )

    assert len(messages) == 2
    assert "결제 전환율 개선 프로젝트" in messages[0].content
    assert "description" in messages[0].content
    assert "개요식" in messages[0].content
    assert "명사 종결" in messages[0].content
    assert '"~다" 종결' in messages[0].content
    assert "**굵게**만 사용" in messages[0].content
    assert "출력 예시는 분량 기준이 아니라 구조 참고용" in messages[0].content
    assert "수집된 데이터의 사실, 행동, 수치, 판단 근거" in messages[0].content
    assert "work_categories에 수집된 업무 항목" in messages[0].content
    assert "problem_episodes에 수집된 각 에피소드" in messages[0].content
    assert "400자 이내" not in messages[0].content
