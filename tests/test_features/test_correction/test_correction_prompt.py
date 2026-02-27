"""첨삭 프롬프트 테스트"""

from langchain_core.prompts import ChatPromptTemplate

from features.correction.prompts import format_portfolio_for_correction, get_correction_prompt


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


def test_correction_prompt_contains_overall_summary_instruction():
    """overall_summary 3단 구조 지시가 포함되는지 테스트"""
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
    assert "overall_summary" in messages[0].content
    assert "현상 진단" in messages[0].content
    assert "갭 분석" in messages[0].content
    assert "솔루션 제안" in messages[0].content
    assert "comment는 null" in messages[0].content


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
