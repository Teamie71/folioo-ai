"""대상 활동 선택 프롬프트 (에이전트 문서 5-4).

프롬프트에는 활동 이름과 요청 단위 별칭만 전달한다. 실제 block ID와 다른 활동의
상세 블록은 노출하지 않는다.
"""

from collections.abc import Iterable

from langchain_core.prompts import ChatPromptTemplate

TARGET_ACTIVITY_SYSTEM = """\
당신은 취업 준비생의 경험정리에서 내용을 반영할 **하나의 활동**을 고릅니다.

# 규칙

- 제공된 활동 목록의 별칭만 `activity_alias`에 넣습니다.
- 사용자 입력만으로 하나의 활동을 명확히 고를 수 있을 때만 선택합니다.
- 둘 이상의 활동에 해당하거나 판단 근거가 부족하면 `activity_alias`를 null로 둡니다.
- 추측하거나 가장 최근·첫 번째 활동을 임의로 고르지 않습니다.
- 실제 블록 ID는 알 수 없으며, 출력하거나 만들지 않습니다.

# 출력

`activity_alias`와 한국어 한 문장 `reason`을 채웁니다.
"""

TARGET_ACTIVITY_USER = """\
활동 목록:
{outline}

사용자 입력:
\"\"\"
{user_message}
\"\"\"
"""

target_activity_prompt = ChatPromptTemplate.from_messages(
    [("system", TARGET_ACTIVITY_SYSTEM), ("user", TARGET_ACTIVITY_USER)]
)


def render_activity_outline(activities: Iterable[tuple[str, str]]) -> str:
    """LLM에 줄 level 2 활동 목록을 들여쓰기 텍스트로 만든다."""
    lines = [f"[{alias}] {title}" for alias, title in activities]
    return "\n".join(lines) if lines else "(선택 가능한 활동 없음)"
