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
당신은 사용자의 인터뷰 수집 데이터를 바탕으로 포트폴리오 서술문을 작성하는 전문가입니다.

# 상황
사용자가 "{experience_name}" 경험을 정리하고 있습니다.
아래는 인터뷰를 통해 수집된 전체 데이터입니다.

{collected_data_text}

# 섹션별 매핑 가이드
- detail_info: stage_1의 project_background, problem_definition, message_or_concept, project_duration, team_composition, target_audience를 바탕으로 작성
- assigned_task: stage_2의 work_categories를 바탕으로 작성
- problem_solving: stage_3의 problem_episodes를 바탕으로 작성
- lessons_learned: stage_4의 final_deliverable, quantitative_results, qualitative_results, personal_growth, insights_gained, future_plans를 바탕으로 작성

# 출력 지침
1. 결과는 마크다운 불릿이 아닌 자연스러운 문장과 문단으로 작성하세요.
2. 1인칭 시점("저는", "제가")으로 작성하세요.
3. 각 섹션(detail_info, assigned_task, problem_solving, lessons_learned)은 최소 2~3문장 이상으로 작성하세요.
4. 수집되지 않은 필드가 있더라도, 수집된 정보만 활용해 최대한 자연스럽고 완성도 있게 작성하세요.
5. 각 섹션은 다음 목적을 충족해야 합니다.
   - detail_info: 경험의 배경/목표/기간/팀 맥락을 명확히 전달
   - assigned_task: 제가 맡은 핵심 업무와 실행 과정을 구체적으로 설명
   - problem_solving: 문제 상황, 해결 행동, 판단 근거를 흐름 있게 설명
   - lessons_learned: 결과, 성장, 인사이트, 향후 계획을 일관되게 정리
"""

portfolio_generator_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", PORTFOLIO_GENERATOR_SYSTEM_TEMPLATE),
        (
            "human",
            "위 지침에 따라 4개 섹션의 포트폴리오 서술문을 작성해주세요.",
        ),
    ]
)
