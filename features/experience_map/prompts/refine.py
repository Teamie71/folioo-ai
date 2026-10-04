"""문장 정제 프롬프트 (에이전트 문서 2-1, 5-6)."""

from langchain_core.prompts import ChatPromptTemplate

REFINE_SYSTEM = """\
당신은 취업 준비생의 경험정리 문장을 정제합니다.

# 절대 규칙

- 입력 item_id마다 정확히 하나의 결과를 반환합니다. item_id를 추가·삭제·변경하지 않습니다.
- 입력에 없는 수치, 고유명사, 역할, 성과, 기간, 원인이나 결과를 만들지 않습니다.
- 배정 정보는 다루지 않습니다. 출력에는 item_id와 refined_text만 있습니다.
- text가 null인 템플릿 빈 슬롯은 refined_text도 null로 그대로 둡니다.

# 정제 기준

- 원문의 사실·의미·구체성을 보존하고, 가능한 범위에서 What / How / Why / Result가 보이게 합니다.
- 원문에 실제로 있는 수치와 고유명사는 유지하되, 없으면 새로 추가하지 않습니다.
- 자연스러운 명사 종결을 사용합니다.
- 내용의 관계가 원문에 명확할 때만 화살표(→) 또는 슬래시(/)로 구조화합니다.

한 활동의 모든 문장을 한 번에 보고 문체만 일관되게 맞추세요. 빈칸을 추측으로 채우지 마세요.
"""

REFINE_USER = """\
선택 활동:
{activity_tree}

정제할 항목:
{items}
"""

refine_prompt = ChatPromptTemplate.from_messages([("system", REFINE_SYSTEM), ("user", REFINE_USER)])


def render_refinement_items(items: list[dict]) -> str:
    """한 활동 단위로 LLM에 전달할 item과 원문을 렌더링한다."""
    lines = []
    for item in items:
        text = item["text"]
        rendered = "null" if text is None else f'"{text}"'
        lines.append(f"- [{item['item_id']}] {rendered}")
    return "\n".join(lines)
