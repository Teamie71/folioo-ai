"""포트폴리오 생성 프롬프트"""

from langchain_core.prompts import ChatPromptTemplate

_STAGE_FIELD_GUIDES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "stage_1": (
        "프로젝트 개요 및 구조화",
        [
            ("project_background", "이 활동을 시작하게 된 이유"),
            ("problem_definition", "프로젝트의 최종 목표 또는 해결하려던 미션"),
            ("message_or_concept", "결과물을 통해 전달하고자 한 핵심 주제"),
            ("project_duration", "프로젝트 진행 기간"),
            ("team_composition", "전체 참여 인원 수와 본인의 포지션"),
            ("target_audience", "결과물 또는 활동의 예상 사용자/수혜자"),
        ],
    ),
    "stage_2": (
        "구체적 실행 과정",
        [
            ("work_categories", "2개 이상의 업무 카테고리"),
        ],
    ),
    "stage_3": (
        "문제 해결과 논리",
        [
            ("problem_episodes", "2개 이상의 문제 해결 에피소드"),
        ],
    ),
    "stage_4": (
        "성과 및 회고",
        [
            ("final_deliverable", "프로젝트의 최종 상태"),
            ("quantitative_results", "수치로 증명 가능한 결과"),
            ("qualitative_results", "비수치적 결과와 피드백"),
            ("personal_growth", "경험 전후 달라진 직무 역량 및 태도"),
            ("insights_gained", "새롭게 얻은 관점"),
            ("future_plans", "향후 개선 방향 또는 학습 계획"),
        ],
    ),
}


def format_section_mapping_guide(section_mapping: dict[str, list[str]] | None = None) -> str:
    """섹션 매핑 가이드를 프롬프트용 문자열로 변환"""
    if not section_mapping:
        return (
            "- description: stage_1의 project_background, problem_definition, "
            "message_or_concept, project_duration, team_composition, target_audience + "
            "stage_2의 work_categories에서 기술/방법론/툴 추출 + "
            "stage_4의 quantitative_results, qualitative_results를 바탕으로 작성\n"
            "- contributions: stage_2의 work_categories를 바탕으로 작성\n"
            "- achievements: stage_3의 problem_episodes를 바탕으로 작성\n"
            "- insights: stage_4의 personal_growth, insights_gained, future_plans를 바탕으로 작성"
        )

    lines: list[str] = []
    for section_name, stage_keys in section_mapping.items():
        if not isinstance(stage_keys, list):
            continue
        joined_stage_keys = ", ".join(stage_keys)
        lines.append(f"- {section_name}: {joined_stage_keys} 데이터를 중심으로 작성")
    return "\n".join(lines)


def _format_field_value(value: str | list[str] | None, completeness: float | None) -> str:
    if value is None or completeness in (None, 0.0):
        return "수집되지 않음"

    if isinstance(value, list):
        if not value:
            return "수집되지 않음"
        return "\n".join(f"{index}. {item}" for index, item in enumerate(value, start=1))

    return str(value)


def format_collected_data_for_prompt(collected_data: dict) -> str:
    """
    collected_data를 프롬프트 입력용 텍스트로 변환

    Args:
        collected_data: 단계별 수집 데이터

    Returns:
        LLM 입력용 문자열
    """
    lines: list[str] = []

    for stage_key, (stage_name, fields) in _STAGE_FIELD_GUIDES.items():
        lines.append(f"[{stage_key}: {stage_name}]")
        stage_data = collected_data.get(stage_key, {})

        for field_name, description in fields:
            field_data = stage_data.get(field_name, {})
            value = field_data.get("value")
            completeness = field_data.get("completeness")
            formatted_value = _format_field_value(value=value, completeness=completeness)
            lines.append(f"- {field_name} ({description}):")
            lines.append(f"  {formatted_value.replace('\n', '\n  ')}")
        lines.append("")

    return "\n".join(lines).strip()


PORTFOLIO_GENERATOR_SYSTEM_TEMPLATE = """
# 역할
당신은 사용자의 인터뷰 수집 데이터를 바탕으로 포트폴리오 개요식을 작성하는 전문가입니다.

# 상황
사용자가 "{experience_name}" 경험을 정리하고 있습니다.
아래는 인터뷰를 통해 수집된 전체 데이터입니다.

{collected_data_text}

# 섹션별 매핑 가이드
{section_mapping_guide}

# 공통 출력 규칙
1. 개요식, 명사 종결로 작성하세요. (예외: insights(배운 점)는 개요식, "~다" 종결)
2. 전문적이고 건조한 비즈니스 톤을 유지하고, 감정적인 형용사(엄청난, 대단한 등)는 사용하지 마세요.
3. 마크다운은 텍스트 강조가 필요할 때 **굵게**만 사용하세요.
4. description, contributions, achievements, insights 각 섹션의 최종 텍스트는 공백/줄바꿈/마크다운 포함 400자 이내로 작성하세요.
5. 글자수가 초과될 경우 수식어, 중복 표현, 부연 설명을 줄이고 핵심 사실과 성과만 남기세요.
6. 이전 시도 피드백에 특정 섹션의 초과 길이가 제시되면 해당 섹션만 다시 축약하고, 다른 섹션은 유지하세요.
7. 길이 재작성 시 항목 구조, 문제-행동-성과 흐름, 핵심 사실은 보존하세요.

# 예외 처리 규칙
- 정보가 없는 항목에 대해 "없음", "NULL", "해당 사항 없음", "모름" 등의 텍스트를 절대 출력하지 마세요.
- 정보가 누락된 항목은 해당 라인을 아예 생략하거나, 상위 항목에 자연스럽게 포함시키세요.
- 누락된 정보를 채우기 위해 사실이 아닌 내용을 절대 지어내지 마세요.
- quantitative_results(정량적 성과) 데이터가 없을 경우, "정량"과 "정성" 구분을 없애고 "**주요 성과:**" 단일 항목으로 출력하세요.

# 섹션별 출력 형식

## description (상세정보)
각 항목을 "**항목명:** 내용" 형태의 개행 목록으로 출력하세요.
출력 항목 순서:
1. **진행 기간:** (project_duration)
2. **프로젝트 배경 및 목적:** (project_background + problem_definition + message_or_concept 통합)
3. **프로젝트 범위 및 구성:** (team_composition - 역할별 인원 및 본인 역할/기여도 포함)
4. **대상 및 타깃:** (target_audience)
5. **주요 기술, 방법론 및 툴:** (work_categories에서 사용 기술/방법론/툴만 추출)
6. **정량적 성과:** (quantitative_results - 수치 기반, 목표치 대비 달성률 명시. 데이터 없으면 생략)
7. **정성적 성과:** (qualitative_results - 피드백 인용, 간접 평가 지표. 데이터 없으면 생략)
   (정량 데이터가 없을 경우 6, 7번 대신 **주요 성과:** 단일 항목으로 출력)

출력 예시:
- **진행 기간:** 2023.09 ~ 2023.12 (4개월)
- **프로젝트 배경 및 목적:** 교내 커뮤니티의 비효율적인 게시판형 거래 방식을 개선하고, 전공 서적 거래의 편의성과 신뢰도를 높이기 위한 전용 플랫폼 기획 및 앱 리뉴얼
- **프로젝트 범위 및 구성:** 기획/디자인 2명, 개발 2명 (총 4인 팀) / **본인 역할: PM 및 UX 기획 (기여도 40%)**
- **대상 및 타깃:** 비싼 전공 서적 가격에 부담을 느끼며, 교내 직거래를 통해 택배비 절약과 빠른 거래를 원하는 대학생
- **주요 기술, 방법론 및 툴:** Figma, Notion, Slack, Google Analytics, IDI(심층 인터뷰), Usability Test
- **정량적 성과:** 리뉴얼 전 대비 DAU(일간 활성 사용자) 150% 증가, 거래 성사율 20% → 65%로 상승
- **정성적 성과:** 교내 창업 경진대회 대상 수상, 총학생회 제휴 협약 체결, "검색부터 구매 약속까지 과정이 직관적이다"라는 사용자 피드백 다수 확보

## contributions (담당업무)
유사한 성격의 업무를 묶어 카테고리화하고, 각 카테고리 아래 세부 업무를 나열하세요.
단순 업무 나열 없이, 세부 업무에는 업무 의도와 수행 내용을 함께 서술하세요.

출력 형식:
**[큰 업무 카테고리]**
- **세부 업무 1** (업무 의도 + 수행한 업무)
- **세부 업무 2** (업무 의도 + 수행한 업무)

출력 예시:
**[사용자 리서치 및 문제 정의]**
- 기존 커뮤니티 이용자 20명 대상 심층 인터뷰를 통해 핵심 이탈 요인 도출
- 판매자와 구매자의 행동 패턴을 분석한 데이터 기반 페르소나 수립 및 여정 지도 설계

**[서비스 기획 및 UX 설계]**
- 거래 절차를 기존 5단계에서 3단계(검색-채팅-거래)로 단축하는 와이어프레임 및 스토리보드 작성
- 불필요한 채팅 소요를 줄이기 위해 시스템이 가격을 자동 수락하는 '스마트 오퍼' 기능 기획

## achievements (문제해결)
각 문제 상황에 번호를 매기고, 상황/전략/근거 형식으로 작성하세요.
문제를 해결한 방법과 그 방법을 도출해낸 사고의 흐름이 드러나도록 작성하세요.

출력 형식:
**N) 문제 상황 10자 내외 요약**
- **상황:** 직면했던 구체적 과제나 난관
- **전략:** 이를 해결하기 위한 전략이나 기획 의도
- **근거:** 왜 그 전략을 선택했는지에 대한 논리적 이유

출력 예시:
**1) 채팅 기능 개발 리소스 부족에 따른 기능 스펙 조정**
- **상황:** 개발 기간 부족으로 인해 기획했던 '실시간 채팅 서버' 구축이 불가능하여, 핵심 기능인 '판매자와의 소통' 자체가 불가능해진 위기 발생
- **전략:** 실시간 채팅 대신 **'댓글 Q&A'**와 **'카카오톡 오픈채팅 링크 연동'**으로 스펙을 변경하여 MVP(최소 기능 제품) 모델로 전환
- **근거:** 우리 서비스의 핵심 가치는 '친목'이 아닌 '빠른 거래'임에 집중. 불안정한 자체 채팅 서버를 무리하게 구축하여 오류를 범하는 것보다, 사용자가 이미 익숙한 카카오톡을 활용하는 것이 UX 경험을 해치지 않으면서 개발 공수를 70% 이상 줄일 수 있는 가장 효율적인 방법이라고 판단.

**2) '노쇼' 문제 해결을 위한 상호 평가 시스템 도입**
- **상황:** 직거래 약속 후 현장에 나타나지 않는 '노쇼' 비율이 30%에 달해 서비스 신뢰도가 급격히 하락하는 문제 발생
- **전략:** 거래 약속 1시간 전 '푸시 알림' 발송 및 거래 완료 후 '매너 온도(상호 평가)' 시스템 도입, 노쇼 3회 누적 시 이용 정지 페널티 정책 수립
- **근거:** 단순히 페널티만 주는 것은 사용자 이탈을 유발할 수 있다고 우려. '매너 온도'라는 게이미피케이션 요소를 도입하여, 긍정적인 거래 경험을 쌓으려는 심리를 자극하는 것이 장기적인 커뮤니티 자정 작용에 유리하다고 판단.

## insights (배운 점)
성장한 부분, 얻은 인사이트, 향후 시도할 계획으로 구분하여 "~다" 종결로 작성하세요.

출력 형식:
- **성장한 부분:** (역량, 태도, 일하는 방식 측면의 구체적인 성장)
- **얻은 인사이트:** (업무, 관계, 대상 이해 방식에서 새롭게 얻은 관점)
- **향후 시도할 계획:** (추가 학습 내용, 이후 프로젝트에서 시도할 개선 방향이나 실천 계획)

출력 예시:
- **성장한 부분:** 화려한 기능보다는 '핵심 가치'에 집중하여 개발 리소스를 조율하는 PM의 일정 관리 역량과 커뮤니케이션 스킬이 성장했다.
- **얻은 인사이트:** UX 기획은 단순히 화면을 그리는 것이 아니라, 비즈니스 목표(거래 성사율)와 개발 현실(리소스) 사이에서 최적의 균형점을 찾아가는 논리적 설계 과정임을 깨달았다.
- **향후 시도할 계획:** 이번 프로젝트에서는 구글 애널리틱스를 기초적으로만 활용했지만, 향후에는 SQL을 학습하여 직접 DB에서 데이터를 추출하고 더 정교하게 사용자 행동 데이터를 분석해보고 싶다.

# 이전 시도 피드백
{validation_feedback}
"""

portfolio_generator_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", PORTFOLIO_GENERATOR_SYSTEM_TEMPLATE),
        (
            "human",
            "위 지침에 따라 4개 섹션의 포트폴리오 개요식을 작성해주세요.",
        ),
    ]
).partial(validation_feedback="없음")
