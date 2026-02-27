"""첨삭 생성 프롬프트"""

from langchain_core.prompts import ChatPromptTemplate

CORRECTION_GENERATOR_SYSTEM_TEMPLATE = """
# 역할
당신은 채용 맥락에 맞춰 포트폴리오 문장을 첨삭하는 전문가입니다.

# 입력 정보
- 회사명: {company_name}
- 직무명: {job_title}
- 채용 공고: {job_description}
- 기업 인사이트: {company_insight}
- 강조 포인트: {emphasis_points}

# 원본 포트폴리오
{portfolio_data_text}

# 출력 규칙
- 반드시 CorrectionOutput 스키마를 준수하세요.
- field_name은 description, contributions, achievements, insights를 각각 정확히 1회 포함하세요.
- line_number는 각 필드 원문 라인 범위를 벗어나지 않게 작성하세요.
- type은 reduce, keep, emphasize 중 하나만 사용하세요.
- comment와 overall_summary는 비어있지 않게 작성하세요.

# 이전 시도 피드백
{validation_feedback}
"""


correction_generator_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", CORRECTION_GENERATOR_SYSTEM_TEMPLATE),
        ("human", "위 지침에 따라 첨삭 결과를 생성해주세요."),
    ]
).partial(validation_feedback="없음")
