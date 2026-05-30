"""첨삭 프롬프트 테스트"""

import pytest
from langchain_core.prompts import ChatPromptTemplate

from features.correction.prompts import (
    build_portfolio_correction_line_map,
    correction_generator_prompt,
    format_portfolio_for_correction,
    get_correction_prompt,
    overall_summary_prompt,
)


def test_get_correction_prompt_is_chat_prompt_template():
    """첨삭 프롬프트가 ChatPromptTemplate인지 테스트"""
    prompt = get_correction_prompt()

    assert isinstance(prompt, ChatPromptTemplate)


def test_get_correction_prompt_has_required_input_variables():
    """필수 입력 변수가 정확히 포함되는지 테스트"""
    prompt = get_correction_prompt()

    expected_vars = {
        "companyName",
        "jobTitle",
        "jobDescription",
        "companyInsight",
        "portfolioData",
        "emphasisPoints",
    }
    assert expected_vars == set(prompt.input_variables)


def test_correction_prompt_removes_overall_summary_instruction():
    """단일 포트폴리오 첨삭 프롬프트에는 overall_summary 지시가 없다."""
    prompt = get_correction_prompt()
    messages = prompt.format_messages(
        companyName="테스트 회사",
        jobTitle="백엔드 개발자",
        jobDescription="Python, FastAPI 기반 서비스 개발",
        companyInsight="데이터 기반 의사결정을 중시",
        portfolioData="[상세 정보 - description]\n1. 테스트",
        emphasisPoints="문제해결 능력과 협업을 강조",
    )

    assert len(messages) == 2
    assert "overall_summary" not in messages[0].content
    assert "SingleCorrectionDecisionOutput" in messages[0].content
    assert "comment는 null" in messages[0].content
    assert "original_text 또는 originalText는 절대 출력하지 마세요" in messages[0].content


def test_correction_generator_prompt_has_required_input_variables():
    """런타임 첨삭 프롬프트 입력 변수를 검증한다."""
    expected_vars = {
        "company_name",
        "job_title",
        "job_description",
        "company_insight",
        "portfolio_data_text",
        "emphasis_points",
    }

    assert expected_vars == set(correction_generator_prompt.input_variables)


def test_correction_generator_prompt_includes_field_scoped_validation_rules():
    """런타임 첨삭 프롬프트가 field 단위 번호 규칙을 명시하는지 테스트"""
    messages = correction_generator_prompt.format_messages(
        company_name="테스트 회사",
        job_title="백엔드 개발자",
        job_description="Python, FastAPI 기반 서비스 개발",
        company_insight="데이터 기반 의사결정을 중시",
        portfolio_data_text="[상세 정보 - description]\n1. 테스트",
        emphasis_points="문제해결 능력과 협업을 강조",
    )

    assert len(messages) == 2
    assert "각 field 내부에서 1부터 다시 시작" in messages[0].content
    assert "번호가 매겨진 줄이 없는 필드도 field 객체는 포함" in messages[0].content
    assert "번호가 매겨진 줄이 있는 필드에서는 해당 줄을 누락 없이" in messages[0].content
    assert "각각 정확히 1회씩 포함" in messages[0].content
    assert "line_number, type, comment 키만 포함" in messages[0].content
    assert "Markdown 코드블록" in messages[0].content
    assert "이전 시도 피드백\n없음" in messages[0].content


def test_overall_summary_prompt_has_required_input_variables():
    """총평 프롬프트 입력 변수를 검증한다."""
    expected_vars = {
        "company_name",
        "job_title",
        "job_description",
        "company_insight",
        "emphasis_points",
        "portfolio_corrections_text",
    }

    assert expected_vars == set(overall_summary_prompt.input_variables)


def test_overall_summary_prompt_contains_three_step_structure():
    """총평 프롬프트가 3단 구조를 명시하는지 테스트"""
    messages = overall_summary_prompt.format_messages(
        company_name="테스트 회사",
        job_title="백엔드 개발자",
        job_description="Python, FastAPI 기반 서비스 개발",
        company_insight="데이터 기반 의사결정을 중시",
        emphasis_points="문제해결 능력과 협업을 강조",
        portfolio_corrections_text="[포트폴리오 ID: 1]\n- description\n  - 1번 | keep | 원문: 테스트 | 코멘트: 없음",
    )

    assert len(messages) == 2
    assert "현상 진단" in messages[0].content
    assert "갭 분석" in messages[0].content
    assert "솔루션 제안" in messages[0].content
    assert "포트폴리오별 첨삭 결과 요약" in messages[1].content


def test_format_portfolio_for_correction_numbers_only_bullet_lines():
    """[소구분] 헤더는 유지하고 불릿 줄만 번호를 매기는지 테스트"""
    portfolio = {
        "description": "- **진행 기간:** 2023.09 ~ 2023.12\n- **대상 및 타깃:** 대학생",
        "contributions": (
            "**[사용자 리서치 및 문제 정의]**\n"
            "- 기존 커뮤니티 이용자 20명 대상 심층 인터뷰\n"
            "- 데이터 기반 페르소나 수립"
        ),
        "achievements": (
            "**1) 리소스 부족**\n"
            "- **상황:** 실시간 채팅 서버 구축 불가\n"
            "- **전략:** MVP 스펙으로 전환\n"
            "- **근거:** 핵심 가치 집중"
        ),
        "insights": "- **성장한 부분:** 우선순위 조율 역량 강화",
    }

    formatted = format_portfolio_for_correction(portfolio)

    assert "[상세 정보 - description]" in formatted
    assert "1. **진행 기간:** 2023.09 ~ 2023.12" in formatted
    assert "2. **대상 및 타깃:** 대학생" in formatted

    assert "[담당 업무 - contributions]" in formatted
    assert "**[사용자 리서치 및 문제 정의]**" in formatted
    assert "1. 기존 커뮤니티 이용자 20명 대상 심층 인터뷰" in formatted
    assert "2. 데이터 기반 페르소나 수립" in formatted

    assert "[문제해결 - achievements]" in formatted
    assert "**1) 리소스 부족**" in formatted
    assert "1. **상황:** 실시간 채팅 서버 구축 불가" in formatted
    assert "2. **전략:** MVP 스펙으로 전환" in formatted
    assert "3. **근거:** 핵심 가치 집중" in formatted

    assert "[배운 점 - insights]" in formatted
    assert "1. **성장한 부분:** 우선순위 조율 역량 강화" in formatted


def test_build_portfolio_correction_line_map_matches_formatter_rules():
    """라인맵은 포맷터와 같은 소구분/불릿/이어지는 줄 병합 규칙을 사용한다."""
    portfolio = {
        "description": "- 첫 줄\n이어지는 설명\n일반 헤더\n- 둘째 줄",
        "contributions": "**[역할]**\n- 기여 첫 줄\n기여 추가 설명",
        "achievements": "**1) 리소스 부족**\n- 성과 첫 줄",
        "insights": "- 배운 점",
    }

    formatted = format_portfolio_for_correction(portfolio)
    line_map = build_portfolio_correction_line_map(portfolio)

    assert "1. 첫 줄 이어지는 설명 일반 헤더" in formatted
    assert "2. 둘째 줄" in formatted
    assert line_map["description"] == {
        1: "첫 줄 이어지는 설명 일반 헤더",
        2: "둘째 줄",
    }
    assert "**[역할]**" in formatted
    assert line_map["contributions"] == {1: "기여 첫 줄 기여 추가 설명"}
    assert "**1) 리소스 부족**" in formatted
    assert line_map["achievements"] == {1: "성과 첫 줄"}
    assert line_map["insights"] == {1: "배운 점"}


def test_format_portfolio_for_correction_numbers_plain_lines_when_no_bullets():
    """불릿 없는 외부 포트폴리오 텍스트도 첨삭 대상 번호 라인으로 변환한다."""
    portfolio = {
        "description": (
            '배경: "관광 1번지" 명동의 카페\n'
            "외국인 관광객 비율이 30% 이상인 카페에서 6개월간 근무했습니다.\n"
            "Zero Complaint: 주문 실수로 인한 컴플레인 0건"
        ),
        "contributions": '외국인 고객에게 "Try this with Oat Milk"라며 옵션을 제안했습니다.',
        "achievements": (
            "#1\n"
            '상황: "Less Sweet", "No Ice" 등 커스텀 주문 오류가 빈번했습니다.\n'
            "전략: 커스텀 주문 가이드북을 직접 제작했습니다.\n"
            "이유: 말로 설명하는 데는 한계가 있었습니다."
        ),
        "insights": "고객에게 특별한 경험을 제공하는 계기가 되었습니다.",
    }

    formatted = format_portfolio_for_correction(portfolio)
    line_map = build_portfolio_correction_line_map(portfolio)

    assert '1. 배경: "관광 1번지" 명동의 카페' in formatted
    assert "2. 외국인 관광객 비율이 30% 이상인 카페에서 6개월간 근무했습니다." in formatted
    assert "3. Zero Complaint: 주문 실수로 인한 컴플레인 0건" in formatted
    assert '1. 외국인 고객에게 "Try this with Oat Milk"라며 옵션을 제안했습니다.' in formatted
    assert "#1" in formatted
    assert '1. 상황: "Less Sweet", "No Ice" 등 커스텀 주문 오류가 빈번했습니다.' in formatted
    assert "2. 전략: 커스텀 주문 가이드북을 직접 제작했습니다." in formatted
    assert "3. 이유: 말로 설명하는 데는 한계가 있었습니다." in formatted
    assert "1. 고객에게 특별한 경험을 제공하는 계기가 되었습니다." in formatted
    assert line_map["description"] == {
        1: '배경: "관광 1번지" 명동의 카페',
        2: "외국인 관광객 비율이 30% 이상인 카페에서 6개월간 근무했습니다.",
        3: "Zero Complaint: 주문 실수로 인한 컴플레인 0건",
    }
    assert line_map["achievements"] == {
        1: '상황: "Less Sweet", "No Ice" 등 커스텀 주문 오류가 빈번했습니다.',
        2: "전략: 커스텀 주문 가이드북을 직접 제작했습니다.",
        3: "이유: 말로 설명하는 데는 한계가 있었습니다.",
    }


def test_format_portfolio_for_correction_renumbers_existing_numbered_plain_lines():
    """이미 번호가 붙은 외부 plain line은 번호 접두사를 제거한 뒤 다시 번호를 매긴다."""
    portfolio = {
        "description": "1. 첫 설명\n2. 둘째 설명",
        "contributions": "1. 첫 담당\n2. 둘째 담당",
        "achievements": "#1\n1. 상황 설명\n2. 전략 설명",
        "insights": "1. 배운 점",
    }

    formatted = format_portfolio_for_correction(portfolio)
    line_map = build_portfolio_correction_line_map(portfolio)

    assert "1. 첫 설명" in formatted
    assert "2. 둘째 설명" in formatted
    assert "1. 1. 첫 설명" not in formatted
    assert "1. 상황 설명" in formatted
    assert "1. 1. 상황 설명" not in formatted
    assert line_map["description"] == {1: "첫 설명", 2: "둘째 설명"}
    assert line_map["achievements"] == {1: "상황 설명", 2: "전략 설명"}


def test_format_portfolio_for_correction_raises_type_error_for_invalid_portfolio():
    """portfolio가 dict 타입이 아닐 때 TypeError를 발생시키는지 테스트"""
    with pytest.raises(TypeError, match="portfolio는 dict 타입이어야 합니다"):
        format_portfolio_for_correction("invalid")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="portfolio는 dict 타입이어야 합니다"):
        format_portfolio_for_correction(["invalid"])  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="portfolio는 dict 타입이어야 합니다"):
        format_portfolio_for_correction(123)  # type: ignore[arg-type]
