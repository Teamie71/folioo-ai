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
_SUBHEADER_PATTERN = re.compile(r"^\s*(?:\*\*)?(?:\[[^\]]+\]|\d+\)\s*\S.+)(?:\*\*)?\s*$")
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
- 각 필드에서 번호가 매겨진 줄을 누락 없이 정확히 한 번씩 반환하세요.
- line_number는 입력에 표시된 숫자를 그대로 사용하세요.
- original_text는 번호를 제외한 원문을 그대로 넣으세요.

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

# overall_summary 작성 규칙
- overall_summary는 단일 문자열로 작성하세요.
- 반드시 3단 구조를 모두 포함하세요.
  1) 현상 진단: 현재 톤앤매너와 강점 유형
  2) 갭 분석: 타깃 기업/직무 기준의 핵심 부족점
  3) 솔루션 제안: 가장 시급한 개선 방향 1가지

# 출력 제약
- 출력은 CorrectionOutput 스키마에 정확히 맞춰야 합니다.
- fields에는 description, contributions, achievements, insights를 모두 포함하세요.
- type은 reduce, keep, emphasize만 사용하세요.
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
        line_number = 0
        latest_numbered_line_index: int | None = None

        for line in field_lines:
            if _is_subheader_line(line):
                output_lines.append(line)
                latest_numbered_line_index = None
                continue

            bullet_match = _BULLET_PATTERN.match(line)
            if bullet_match is not None:
                line_number += 1
                bullet_content = bullet_match.group("content").strip()
                output_lines.append(f"{line_number}. {bullet_content}")
                latest_numbered_line_index = len(output_lines) - 1
                continue

            if latest_numbered_line_index is None:
                output_lines.append(line)
                continue

            output_lines[latest_numbered_line_index] = (
                f"{output_lines[latest_numbered_line_index]} {line}"
            )

        output_lines.append("")

    return "\n".join(output_lines).strip()


def get_correction_prompt() -> ChatPromptTemplate:
    """첨삭용 ChatPromptTemplate 반환"""
    return ChatPromptTemplate.from_messages(
        [
            ("system", CORRECTION_SYSTEM_PROMPT),
            ("human", CORRECTION_HUMAN_PROMPT),
        ]
    )


__all__ = [
    "CORRECTION_HUMAN_PROMPT",
    "CORRECTION_SYSTEM_PROMPT",
    "format_portfolio_for_correction",
    "get_correction_prompt",
]
