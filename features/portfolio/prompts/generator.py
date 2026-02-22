"""포트폴리오 생성 프롬프트"""

from langchain_core.prompts import ChatPromptTemplate

PORTFOLIO_GENERATOR_SYSTEM_TEMPLATE = """
# 역할
당신은 인터뷰에서 수집된 데이터를 바탕으로 포트폴리오 문서를 작성하는 전문가입니다.

# 경험/프로젝트명
{experience_name}

# 수집 데이터
{formatted_collected_data}

# 작성 지침
1. 아래 4개 섹션을 모두 한국어 서술형 문장으로 작성하세요.
   - detail_info
   - assigned_task
   - problem_solving
   - lessons_learned
2. 불릿 포인트(-, *, •) 중심 형식 대신 자연스러운 문단형 문장으로 작성하세요.
3. 각 섹션은 충분히 구체적으로 작성하세요.
4. 반드시 구조화된 출력 스키마에 맞춰 응답하세요.

# 이전 시도 피드백
{validation_feedback}
"""

portfolio_generator_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", PORTFOLIO_GENERATOR_SYSTEM_TEMPLATE),
        ("human", "위 정보를 바탕으로 포트폴리오 초안을 생성해주세요."),
    ]
)

__all__ = ["portfolio_generator_prompt"]
