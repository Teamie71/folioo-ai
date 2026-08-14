"""이번 턴 커밋 기준 gap 분석 프롬프트 (에이전트 문서 2-1, 5-10)."""

from langchain_core.prompts import ChatPromptTemplate

GAP_ANALYSIS_SYSTEM = """\
당신은 취업 준비생의 경험정리에서 다음에 물어볼 보완 질문을 고릅니다.

# 입력 범위와 절대 규칙

- 이번 턴에 커밋될 항목만 판단 근거로 사용합니다. 현재 맵의 다른 빈칸을 추측하지 마세요.
- 방금 커밋될 text에 이미 있는 사실을 누락으로 질문하지 마세요.
- gap은 최대 하나입니다. 충분하면 gap을 null로 두세요.
- anchor_ref는 제공된 기준 블록 별칭 중 하나만 사용합니다. 실제 ID를 만들거나 노출하지 마세요.
- 제공되지 않은 수치, 역할, 결과, 원인을 만들지 마세요.

# 우선순위

다음 순서로 하나만 고릅니다: (1) 카테고리 핵심 정보 누락, (2) 직접 행동 부족,
(3) 수행 방식 또는 판단 기준 부족, (4) 배운 점 또는 활용 방향 부족, (5) 결과 또는 변화 부족.
같은 순위면 이번 커밋과 더 직접 연결된 것, 한 번의 답변으로 쉽게 보완되는 것,
보완 효과가 큰 것을 차례로 고릅니다.

# gap을 만들지 않는 경우

- 이번 커밋만으로 충분히 구체적인 경우
- 커밋 내용과 직접 연결되지 않는 경우
- 답변해도 품질 개선 효과가 낮은 경우
- 전체 경험 맥락이 없으면 판단할 수 있는 경우

# 질문 문구

- gap이 있으면 message는 한 문장, 직접 답할 수 있는 질문으로 씁니다.
- 평가하거나 부담을 주는 표현을 쓰지 않습니다.
- extend_block은 기존 블록에 보태면 되는 정보, new_child_block은 별도 하위 기록이
  더 자연스러운 정보에만 사용합니다.
"""

GAP_ANALYSIS_USER = """\
이번 턴 커밋 operation:
{commit_items}

anchor로 사용할 수 있는 기존 블록 별칭:
{anchor_aliases}
"""

gap_analysis_prompt = ChatPromptTemplate.from_messages(
    [("system", GAP_ANALYSIS_SYSTEM), ("user", GAP_ANALYSIS_USER)]
)


def render_commit_items(items: list[dict]) -> str:
    """LLM이 방금 반영할 내용만 판단하도록 operation을 렌더링한다."""
    lines = []
    for item in items:
        refs = ", ".join(
            f"{field}={item[field]}"
            for field in ("parent_ref", "parent_item_id", "target_ref", "after_ref")
            if item.get(field) is not None
        )
        text = item.get("text")
        lines.append(
            f"- [{item.get('item_id', 'unknown')}] {item.get('action', 'unknown')}"
            f" ({refs or '참조 없음'}): {text if text is not None else '(빈 템플릿 슬롯)'}"
        )
    return "\n".join(lines)


def render_anchor_aliases(aliases: list[str]) -> str:
    """이번 operation과 직접 연결된 기존 블록 별칭만 렌더링한다."""
    return ", ".join(f"[{alias}]" for alias in aliases)


__all__ = ["gap_analysis_prompt", "render_anchor_aliases", "render_commit_items"]
