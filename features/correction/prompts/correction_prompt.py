"""첨삭 프롬프트 정의"""

import re

from langchain_core.prompts import ChatPromptTemplate

_CORRECTION_FIELD_ORDER = ("description", "contributions", "achievements", "insights")
_FIELD_TITLE_BY_NAME = {
    "description": "상세 정보",
    "contributions": "담당 업무",
    "achievements": "문제해결",
    "insights": "배운 점",
}

_BULLET_PATTERN = re.compile(r"^\s*[-*]\s+(?P<content>.+)$")
_NUMBERED_LINE_PATTERN = re.compile(r"^\s*\d+\.\s+(?P<content>.+)$")
_SUBHEADER_PATTERN = re.compile(
    r"^\s*(?:\*\*)?(?:\[[^\]]+\]|\d+\)\s*\S.+|#\s*\d+(?:\s+\S.*)?)(?:\*\*)?\s*$"
)
_EMPTY_TEXT_VALUES = {"", "0"}

CORRECTION_SYSTEM_PROMPT = """
# 역할
당신은 채용 전문가이자 포트폴리오 첨삭 전문가입니다.
지원자의 마스터 포트폴리오를 기업 정보와 JD에 맞춰 줄 단위로 첨삭합니다.

# 핵심 목표
- 직무와 관련성이 낮거나 불필요한 내용은 reduce로 분류해 축소를 권고합니다.
- 기업/직무 적합성이 높고 확장 가치가 있는 내용은 emphasize로 분류해 구체화를 권고합니다.
- 이미 적절한 내용은 keep으로 유지합니다.

# 라인 분석 규칙
- portfolioData는 필드별로 번호가 매겨진 줄 목록입니다.
- 숫자로 시작하는 줄(예: "1. ...")만 첨삭 대상입니다.
- "[소구분]" 또는 "1) 소구분" 헤더 줄은 첨삭 대상이 아닙니다.
- fields에는 description, contributions, achievements, insights를 각각 정확히 1회씩 포함하세요.
- 번호가 매겨진 줄이 없는 필드도 field 객체는 포함하고, lines는 빈 배열([])로 반환하세요.
- 번호가 매겨진 줄이 있는 필드에서는 해당 줄을 누락 없이 정확히 한 번씩 반환하세요. 중복 반환이나 일부 누락은 허용되지 않습니다.
- line_number는 입력에 표시된 숫자를 그대로 사용하세요.
- line_number는 전체 문서 기준으로 이어서 세지 말고, 각 field 내부에서 1부터 다시 시작하는 번호만 사용하세요.
- 다른 field의 line_number를 가져오거나 섞지 마세요.
- original_text 또는 originalText는 절대 출력하지 마세요. 원문 텍스트는 서버 코드가 채웁니다.

# 첨삭 타입 기준
- reduce: 직무와 무관하거나 장황한 내용, 일반론, 비핵심 보조 업무
- keep: 기업/직무 적합성과 구체성이 충분한 내용
- emphasize: JD 핵심 역량과 직접 연결되며 수치/사례 보강 시 임팩트가 커지는 내용
- reduce에서 "제외" 표현은 매우 보수적으로 사용하세요. 모호하면 "축소"를 권고하세요.

# 항목별 첨삭 기준
- description: JD 키워드, 직무 연관 기술/방법론, KPI와 연결되는 성과를 우선 강조
- contributions: 입사 후 R&R과 유사한 업무, 하드 스킬 활용 경험을 우선 강조
- achievements: 기업이 겪을 법한 문제를 논리적으로 해결한 경험을 우선 강조
- insights: 지원 직무 페르소나에 맞는 마인드셋과 실천 계획을 강조

# 코멘트 작성 규칙
- keep인 줄의 comment는 null로 설정하세요.
- reduce 또는 emphasize는 comment를 반드시 작성하세요.
- comment 형식: "이유 한 문장. 제안 한 문장."
- emphasize는 가능하면 comment 끝에 "수정 예시: ..."를 추가하세요.

# 출력 제약
- 출력은 SingleCorrectionDecisionOutput 스키마에 정확히 맞춰야 합니다.
- 각 field 객체는 반드시 1개씩만 반환하세요.
- type은 reduce, keep, emphasize만 사용하세요.
- 응답은 추가 설명 문장, Markdown 코드블록, ```json, ``` 없이 순수 JSON object 하나만 반환하세요.
- 모든 line 객체는 line_number, type, comment 키만 포함해야 하며, keep 타입은 comment를 null로 설정하세요.
- 모든 line 객체에는 original_text 또는 originalText 키를 포함하지 마세요.
- JSON 구조 예시:
  {{"fields": [{{"field_name": "description", "lines": [{{"line_number": 1, "type": "keep", "comment": null}}]}}]}}
""".strip()

CORRECTION_GENERATOR_SYSTEM_TEMPLATE = f"""
{CORRECTION_SYSTEM_PROMPT}

# 이전 시도 피드백
{{validation_feedback}}
""".strip()

CORRECTION_HUMAN_PROMPT = """
## 입력 데이터
### 기업명
{companyName}

### 직무명
{jobTitle}

### Job Description (JD)
{jobDescription}

### 기업 분석 정보
{companyInsight}

### 첨삭 대상 마스터 포트폴리오
{portfolioData}

### 강조 포인트
{emphasisPoints}
""".strip()


def _normalize_lines(value: object) -> list[str]:
    """필드 값을 줄 단위 텍스트 목록으로 정규화"""
    if value is None:
        return []

    if isinstance(value, str):
        raw_lines = value.splitlines()
    elif isinstance(value, list):
        raw_lines = []
        for item in value:
            if item is None:
                continue
            raw_lines.extend(str(item).splitlines())
    else:
        raw_lines = str(value).splitlines()

    lines: list[str] = []
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line or line in _EMPTY_TEXT_VALUES:
            continue
        lines.append(line)

    return lines


def _is_subheader_line(line: str) -> bool:
    """[소구분] 형태 헤더 라인인지 확인"""
    return _SUBHEADER_PATTERN.match(line) is not None


def _format_field_for_correction(field_lines: list[str]) -> tuple[list[str], dict[int, str]]:
    """필드 라인을 첨삭 대상 텍스트와 원문 라인맵으로 변환"""
    output_lines: list[str] = []
    original_text_by_line_number: dict[int, str] = {}
    line_number = 0
    latest_numbered_line_index: int | None = None
    latest_line_number: int | None = None
    has_bullet_line = any(_BULLET_PATTERN.match(line) is not None for line in field_lines)

    for line in field_lines:
        if _is_subheader_line(line):
            output_lines.append(line)
            latest_numbered_line_index = None
            latest_line_number = None
            continue

        bullet_match = _BULLET_PATTERN.match(line)
        if bullet_match is not None:
            line_number += 1
            bullet_content = bullet_match.group("content").strip()
            output_lines.append(f"{line_number}. {bullet_content}")
            original_text_by_line_number[line_number] = bullet_content
            latest_numbered_line_index = len(output_lines) - 1
            latest_line_number = line_number
            continue

        if not has_bullet_line:
            line_number += 1
            numbered_line_match = _NUMBERED_LINE_PATTERN.match(line)
            original_text = (
                numbered_line_match.group("content").strip()
                if numbered_line_match is not None
                else line
            )
            output_lines.append(f"{line_number}. {original_text}")
            original_text_by_line_number[line_number] = original_text
            latest_numbered_line_index = len(output_lines) - 1
            latest_line_number = line_number
            continue

        if latest_numbered_line_index is None or latest_line_number is None:
            output_lines.append(line)
            continue

        output_lines[latest_numbered_line_index] = (
            f"{output_lines[latest_numbered_line_index]} {line}"
        )
        original_text_by_line_number[latest_line_number] = (
            f"{original_text_by_line_number[latest_line_number]} {line}"
        )

    return output_lines, original_text_by_line_number


def format_portfolio_for_correction(portfolio: dict) -> str:
    """
    첨삭용 포트폴리오 텍스트 포맷팅

    Args:
        portfolio: description/contributions/achievements/insights를 포함한 포트폴리오 dict

    Returns:
        필드별로 줄 번호가 부여된 LLM 입력 문자열
    """
    if not isinstance(portfolio, dict):
        raise TypeError("portfolio는 dict 타입이어야 합니다.")

    output_lines: list[str] = []

    for field_name in _CORRECTION_FIELD_ORDER:
        title = _FIELD_TITLE_BY_NAME[field_name]
        output_lines.append(f"[{title} - {field_name}]")

        field_lines = _normalize_lines(portfolio.get(field_name))
        field_output_lines, _ = _format_field_for_correction(field_lines)
        output_lines.extend(field_output_lines)

        output_lines.append("")

    return "\n".join(output_lines).strip()


def build_portfolio_correction_line_map(portfolio: dict) -> dict[str, dict[int, str]]:
    """
    첨삭 대상 원문 라인맵 생성

    Args:
        portfolio: description/contributions/achievements/insights를 포함한 포트폴리오 dict

    Returns:
        field_name -> line_number -> original_text 매핑
    """
    if not isinstance(portfolio, dict):
        raise TypeError("portfolio는 dict 타입이어야 합니다.")

    line_map: dict[str, dict[int, str]] = {}
    for field_name in _CORRECTION_FIELD_ORDER:
        field_lines = _normalize_lines(portfolio.get(field_name))
        _, original_text_by_line_number = _format_field_for_correction(field_lines)
        line_map[field_name] = original_text_by_line_number

    return line_map


def get_correction_prompt() -> ChatPromptTemplate:
    """첨삭용 ChatPromptTemplate 반환"""
    return ChatPromptTemplate.from_messages(
        [
            ("system", CORRECTION_SYSTEM_PROMPT),
            ("human", CORRECTION_HUMAN_PROMPT),
        ]
    )


correction_generator_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", CORRECTION_GENERATOR_SYSTEM_TEMPLATE),
        (
            "human",
            "## 입력 데이터\n"
            "### 기업명\n"
            "{company_name}\n\n"
            "### 직무명\n"
            "{job_title}\n\n"
            "### Job Description (JD)\n"
            "{job_description}\n\n"
            "### 기업 분석 정보\n"
            "{company_insight}\n\n"
            "### 첨삭 대상 마스터 포트폴리오\n"
            "{portfolio_data_text}\n\n"
            "### 강조 포인트\n"
            "{emphasis_points}",
        ),
    ]
).partial(validation_feedback="없음")


__all__ = [
    "CORRECTION_GENERATOR_SYSTEM_TEMPLATE",
    "CORRECTION_HUMAN_PROMPT",
    "CORRECTION_SYSTEM_PROMPT",
    "build_portfolio_correction_line_map",
    "correction_generator_prompt",
    "format_portfolio_for_correction",
    "get_correction_prompt",
]
