"""앵커(TASK.SUMMARY·PROBLEM_SOLVING.SUMMARY) 요약 생성 프롬프트.

구조화 노드는 원문에 요약할 별도 문장이 없으면 앵커를 일부러 비워 둔다
(prompts/structure.py). 세부 슬롯이 다 채워졌는데도 앵커가 영원히
"(빈 블록 — 가이드: ...)"로만 보이면 사용자가 뭘 정리했는지 한눈에 알 수
없다(QA 3차 #9). 이 프롬프트는 그 세부 슬롯 내용만 보고 앵커 한 줄 요약을
새로 만든다.
"""

from langchain_core.prompts import ChatPromptTemplate

ANCHOR_SUMMARY_SYSTEM = """\
당신은 취업 준비생의 경험정리를 돕는 에이전트입니다. 아래 세부 내용을 읽고
그 업무·에피소드 전체를 한 줄로 압축하는 요약 문장을 만듭니다.

# 규칙

- 한 문장, 100자 이내로 씁니다.
- 세부 내용에 없는 수치·고유명사·행동·원인·결과를 새로 만들지 않습니다.
- 세부 내용을 그대로 옮겨 적지 말고, 공통 주제·목적을 압축합니다.
- 자연스러운 명사 종결을 사용합니다(예: "~을 담당함", "~을 해결함").
"""

ANCHOR_SUMMARY_USER = """\
세부 내용:
{details}
"""

anchor_summary_prompt = ChatPromptTemplate.from_messages(
    [("system", ANCHOR_SUMMARY_SYSTEM), ("user", ANCHOR_SUMMARY_USER)]
)


def render_anchor_details(details: list[str]) -> str:
    """앵커 자식 슬롯 내용을 LLM에 전달할 불릿으로 렌더링한다."""
    return "\n".join(f"- {detail}" for detail in details)
