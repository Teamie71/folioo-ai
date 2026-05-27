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
4. 아래 출력 예시는 분량 기준이 아니라 구조 참고용입니다. 예시의 항목 수, 문장 수, 길이를 따라 짧게 요약하지 마세요.
5. 수집된 데이터의 사실, 행동, 수치, 판단 근거, 사용 도구는 중복이 아닌 한 최대한 모두 반영하세요.
6. 특히 work_categories, problem_episodes, personal_growth, insights_gained, future_plans의 세부 항목을 단일 문장으로 과도하게 압축하지 마세요.
7. 유사한 내용은 묶을 수 있지만, 묶인 항목 안에 입력된 핵심 수행 내용과 의사결정 이유가 빠지지 않게 작성하세요.
8. 최종 전송 단계에서 뒤쪽 내용이 잘릴 수 있으므로, 핵심 성과, 대표 문제해결, 본인 역할처럼 중요도가 높은 항목을 각 섹션 앞쪽에 배치하세요.
9. 이전 시도 피드백이 있으면 비어 있는 섹션이나 누락된 항목만 보완하고, 이미 충분한 다른 섹션은 유지하세요.

# 예외 처리 규칙
- 정보가 없는 항목에 대해 "없음", "NULL", "해당 사항 없음", "모름" 등의 텍스트를 절대 출력하지 마세요.
- 정보가 누락된 항목은 해당 라인을 아예 생략하거나, 상위 항목에 자연스럽게 포함시키세요.
- 누락된 정보를 채우기 위해 사실이 아닌 내용을 절대 지어내지 마세요.
- quantitative_results(정량적 성과) 데이터가 없을 경우, "정량"과 "정성" 구분을 없애고 "**주요 성과:**" 단일 항목으로 출력하세요.

# 섹션별 출력 형식

## description (상세정보)
각 항목을 "**항목명:** 내용" 또는 하위 bullet 형태의 개행 목록으로 출력하세요.
상세정보는 프로젝트의 맥락, 범위, 대상, 기술/방법론/툴, 성과가 빠르게 파악되도록 작성하세요.
출력 항목 순서:
1. **진행 기간:** (project_duration)
2. **프로젝트 배경 및 목적:** (project_background + problem_definition + message_or_concept 통합)
3. **프로젝트 범위 및 구성:** (team_composition에서 역할별 인원 구성, 협업 파트 구성, 본인이 맡은 역할 분리)
4. **대상 및 타깃:** (target_audience)
5. **주요 기술, 방법론 및 툴:** (work_categories에서 본인이 직접 사용하거나 관여한 기술/방법론/툴 추출)
6. **정량적 성과:** (quantitative_results - 수치 기반 성과를 하위 bullet로 분리. 데이터 없으면 생략)
7. **정성적 성과:** (qualitative_results - 피드백 인용, 반응, 수상, 협약 등 간접 평가 지표를 하위 bullet로 분리. 데이터 없으면 생략)
   (정량 데이터가 없을 경우 6, 7번 대신 **주요 성과:** 단일 항목으로 출력)

구조 예시:
- **진행 기간:** 기간
- **프로젝트 배경 및 목적:** 배경, 문제 정의, 메시지 또는 컨셉
- **프로젝트 범위 및 구성**
  - 역할별 인원 구성 및 협업 파트
  - **본인 역할:** 역할과 책임 범위
- **대상 및 타깃:** 사용자, 참여자, 수혜자 또는 고객
- **주요 기술, 방법론 및 툴:** 도구, 기술, 방법론
- **정량적 성과**
  - 수치 기반 성과
- **정성적 성과**
  - 피드백, 반응, 수상, 협약 등 간접 평가 지표

## contributions (담당업무)
유사한 성격의 업무를 묶어 카테고리화하고, 각 카테고리 아래 세부 업무를 나열하세요.
단순 업무 나열 없이, 세부 업무에는 업무 의도와 수행 내용을 함께 서술하세요.
work_categories에 수집된 업무 항목은 중복이 아닌 한 모두 반영하세요.
입력된 업무가 3개 이상이면 예시처럼 2개 카테고리에 맞추지 말고, 필요한 만큼 카테고리와 세부 업무를 늘리세요.

출력 형식:
**[큰 업무 카테고리]**
- **세부 업무 1** (업무 의도 + 수행한 업무)
- **세부 업무 2** (업무 의도 + 수행한 업무)

구조 예시:
**[업무 카테고리]**
- 핵심 세부 업무
- 핵심 세부 업무

## achievements (문제해결)
각 문제 상황에 번호를 매기고, 상황/전략/근거 형식으로 작성하세요.
문제를 해결한 방법과 그 방법을 도출해낸 사고의 흐름이 드러나도록 작성하세요.
problem_episodes에 수집된 각 에피소드는 중복이 아닌 한 번호가 매겨진 독립 항목으로 모두 반영하세요.
각 문제 항목의 제목은 10자 내외의 요약을 우선하되, 의미가 불명확해지면 필요한 만큼 구체화하세요.
상황, 전략, 근거에 여러 정보가 있으면 각각 하위 bullet로 나누어 작성하세요.

출력 형식:
**N) 문제 상황 10자 내외 요약**
- **상황**
  - 직면했던 구체적 과제나 난관
- **전략**
  - 이를 해결하기 위한 전략이나 기획 의도
- **근거**
  - 왜 그 전략을 선택했는지에 대한 논리적 이유

## insights (배운 점)
성장한 부분, 얻은 인사이트, 향후 시도할 계획으로 구분하여 "~다" 종결로 작성하세요.
personal_growth, insights_gained, future_plans에 여러 세부 내용이 있으면 각각 별도 bullet로 반영하세요.

출력 형식:
- **성장한 부분**
  - 역량, 태도, 일하는 방식 측면의 구체적인 성장
- **얻은 인사이트**
  - 업무, 관계, 대상 이해 방식에서 새롭게 얻은 관점
- **향후 시도할 계획**
  - 추가 학습 내용, 이후 프로젝트에서 시도할 개선 방향이나 실천 계획

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
