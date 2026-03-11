"""첨삭 총평 프롬프트"""

from langchain_core.prompts import ChatPromptTemplate

OVERALL_SUMMARY_SYSTEM_TEMPLATE = """
# 역할
당신은 여러 포트폴리오 첨삭 결과를 종합해 채용 맥락의 총평을 작성하는 전문가입니다.

# 목표
- 선택된 모든 포트폴리오를 아우르는 광범위한 총평을 작성하세요.
- 개별 문장 첨삭을 반복하지 말고, 공통 패턴과 우선순위를 정리하세요.
- 반드시 아래 3단 구조를 순서대로 포함하세요.
  1) 현상 진단
  2) 갭 분석
  3) 솔루션 제안

# 작성 규칙
- 회사명, 직무명, JD, 기업 인사이트를 함께 고려하세요.
- 각 포트폴리오별 강점/약점을 종합해 지원자 관점의 실행 가능한 제안을 제시하세요.
- 출력은 일반 텍스트만 반환하세요.
""".strip()

OVERALL_SUMMARY_HUMAN_TEMPLATE = """
## 입력 정보
### 회사명
{company_name}

### 직무명
{job_title}

### 채용 공고
{job_description}

### 기업 인사이트
{company_insight}

### 강조 포인트
{emphasis_points}

### 포트폴리오별 첨삭 결과 요약
{portfolio_corrections_text}
""".strip()
overall_summary_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", OVERALL_SUMMARY_SYSTEM_TEMPLATE),
        ("human", OVERALL_SUMMARY_HUMAN_TEMPLATE),
    ]
)
