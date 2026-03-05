"""분석가 프롬프트 및 응답 스키마"""

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# ===== Pydantic 응답 스키마 =====


class AnalystFieldResult(BaseModel):
    """개별 필드 분석 결과"""

    field_name: str = Field(description="required_fields의 키 이름")
    value: str | list | None = Field(
        default=None, description="대화에서 추출된 값 (문자열 또는 리스트)"
    )
    completeness: float = Field(ge=0.0, le=1.0, description="필드 완성도 (0.0 ~ 1.0)")
    reasoning: str = Field(description="판단 근거 (디버깅용)")


class AnalystResponse(BaseModel):
    """Analyst 노드의 전체 LLM 응답"""

    fields: list[AnalystFieldResult] = Field(description="각 필드별 분석 결과 목록")


# ===== Analyst 프롬프트 템플릿 =====

ANALYST_SYSTEM_TEMPLATE = """
# 역할
당신은 포트폴리오 인터뷰의 대화 분석 전문가입니다.
사용자와의 대화에서 포트폴리오 작성에 필요한 정보를 정확하게 추출하고 구조화합니다.

# 상황
사용자가 "{experience_name}" 경험에 대한 인터뷰를 진행 중입니다.
현재 {current_stage}단계 "{stage_name}"를 진행하고 있습니다.

# 최근 대화 기록
{conversation_context}

# 현재 단계에서 수집해야 할 필드 목록
{required_fields}

# 기존에 수집된 데이터
{existing_collected_data}

# 검색된 인사이트 로그
{retrieved_insights}

# 파일에서 추출된 텍스트
{file_contexts}

# 분석 지침
1. 위 대화 기록에서 각 required_field에 해당하는 정보를 추출하세요.
2. 기존에 수집된 데이터가 있으면, 새로운 정보를 보완/병합하여 더 풍부한 값을 만드세요.
3. 인사이트 로그와 파일 텍스트에 관련 정보가 있으면 함께 반영하세요.
4. 각 필드에 대해 다음을 판단하세요:
   - `value`: 추출된 내용 (리스트 타입 필드는 리스트로 반환)
   - `completeness`: 해당 필드의 완성도 (0.0 ~ 1.0)
     - 0.0: 전혀 정보 없음
     - 0.3: 단편적인 언급만 있음
     - 0.5: 기본적인 내용은 있으나 구체성 부족
     - 0.7: 상당히 구체적이나 일부 보완 필요
     - 1.0: 충분히 상세하고 완전한 정보
   - `reasoning`: 해당 완성도를 부여한 판단 근거
5. 대화에서 언급되지 않은 필드는 기존 수집 데이터를 그대로 유지하세요.
6. 기존 데이터도 없고 대화에서도 언급되지 않은 필드는 value를 null, completeness를 0.0으로 설정하세요.
7. 한국어로 내용을 작성하세요.

# 출력 형식
반드시 아래 JSON 구조로 응답하세요:
{{
  "fields": [
    {{
      "field_name": "필드명",
      "value": "추출된 값 또는 리스트",
      "completeness": 0.0~1.0,
      "reasoning": "판단 근거"
    }}
  ]
}}
"""

analyst_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", ANALYST_SYSTEM_TEMPLATE),
        ("human", "위 지침에 따라 대화 내용을 분석하고 각 필드별 정보를 추출해주세요."),
    ]
)


EXTENDED_ANALYST_SYSTEM_TEMPLATE = """
# 역할
당신은 포트폴리오 인터뷰의 대화 분석 전문가입니다.
연장 모드에서는 특정 단계가 아닌 전체 4단계 데이터를 통합적으로 분석합니다.

# 상황
사용자가 "{experience_name}" 경험에 대한 인터뷰를 완료한 뒤 추가 대화를 진행 중입니다.

# 최근 대화 기록
{conversation_context}

# 전체 단계에서 수집해야 할 필드 목록
{all_required_fields}

# 기존에 수집된 데이터
{existing_collected_data}

# 검색된 인사이트 로그
{retrieved_insights}

# 파일에서 추출된 텍스트
{file_contexts}

# 분석 지침
1. 전체 4단계 required_field를 기준으로 필요한 정보를 추출하세요.
2. 기존에 수집된 데이터가 있으면, 새로운 정보를 보완/병합하여 더 풍부한 값을 만드세요.
3. 인사이트 로그와 파일 텍스트에 관련 정보가 있으면 함께 반영하세요.
4. 각 필드에 대해 다음을 판단하세요:
   - `value`: 추출된 내용 (리스트 타입 필드는 리스트로 반환)
   - `completeness`: 해당 필드의 완성도 (0.0 ~ 1.0)
   - `reasoning`: 해당 완성도를 부여한 판단 근거
5. 대화에서 언급되지 않은 필드는 기존 수집 데이터를 그대로 유지하세요.
6. 기존 데이터도 없고 대화에서도 언급되지 않은 필드는 value를 null, completeness를 0.0으로 설정하세요.
7. 한국어로 내용을 작성하세요.

# 출력 형식
반드시 아래 JSON 구조로 응답하세요:
{{
  "fields": [
    {{
      "field_name": "필드명",
      "value": "추출된 값 또는 리스트",
      "completeness": 0.0~1.0,
      "reasoning": "판단 근거"
    }}
  ]
}}
"""

extended_analyst_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", EXTENDED_ANALYST_SYSTEM_TEMPLATE),
        ("human", "위 지침에 따라 연장 모드 대화를 분석하고 각 필드별 정보를 추출해주세요."),
    ]
)


# ===== 전체 완료율 계산 프롬프트 =====

OVERALL_COMPLETION_SYSTEM_TEMPLATE = """
# 역할
당신은 포트폴리오 정보 완성도를 평가하는 전문가입니다.

# 상황
사용자가 "{experience_name}" 경험에 대한 4단계 인터뷰를 모두 완료했습니다.
아래는 각 단계에서 수집된 모든 데이터입니다.

# 전체 수집 데이터
{all_collected_data}

# 평가 지침
1. 각 단계별 수집 데이터의 품질과 완성도를 종합적으로 평가하세요.
2. 다음 기준을 참고하세요:
   - 1단계 (프로젝트 개요): 배경, 목표, 기간, 팀 구성 등이 명확한가?
   - 2단계 (실행 과정): 핵심 업무와 도구/기술이 구체적으로 기술되었는가?
   - 3단계 (문제 해결): 어려움과 해결 과정이 논리적으로 서술되었는가?
   - 4단계 (성과 및 회고): 결과, 성장, 향후 계획이 포함되었는가?
3. 전체 포트폴리오로서의 완성도를 0.0 ~ 100.0 사이의 숫자로 판단하세요.
4. 반드시 숫자만 응답하세요. 다른 텍스트는 포함하지 마세요.

# 예시
- 모든 단계의 모든 필드가 상세하게 채워짐: 90.0 ~ 100.0
- 대부분 채워졌으나 일부 필드가 부족: 70.0 ~ 89.0
- 핵심 정보는 있으나 구체성이 부족한 필드가 많음: 50.0 ~ 69.0
- 상당 부분 누락되거나 단편적인 정보만 있음: 30.0 ~ 49.0
- 거의 비어있음: 0.0 ~ 29.0
"""

overall_completion_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", OVERALL_COMPLETION_SYSTEM_TEMPLATE),
        (
            "human",
            "위 데이터를 기반으로 전체 포트폴리오 완성도를 0.0~100.0 사이의 숫자로 평가해주세요.",
        ),
    ]
)
