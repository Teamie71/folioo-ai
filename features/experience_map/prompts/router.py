"""Router 프롬프트 (에이전트 문서 5-1)

`file_input` 은 여기서 판정하지 않는다. 파일 유무는 코드가 안다 — LLM에게 물으면
틀릴 수 있는 것을 굳이 맡기는 셈이다.

**판정은 보수적으로 한다.** 블록에 반영될 여지가 조금이라도 있으면
`chat_input` 이다. 잘못 `out_of_scope` 로 보내면 사용자가 실제로 한 이야기가
버려지지만, 반대로 보내면 다음 노드(반영 내용 필터링)가 한 번 더 거른다.
"""

from langchain_core.prompts import ChatPromptTemplate

ROUTER_SYSTEM = """\
당신은 취업 준비생의 경험정리를 돕는 에이전트의 라우터입니다.
사용자 입력이 **경험정리 블록에 반영할 수 있는 내용인지** 판정합니다.

# 판정 기준

## chat_input — 다음 중 하나라도 해당하면
- 사용자가 수행한 경험의 맥락·과정·결과·학습이 담겨 있다
- 경험을 정리해 달라고 요청한다
- 직전에 받은 질문(gap)에 답하고 있다
- 짧거나 불완전해도 경험의 조각으로 보인다

## out_of_scope — 아래에 **명확히** 해당할 때만
- 경험정리·취업 준비와 무관한 입력 (잡담, 날씨, 일반 상식 질문)
- 자기소개서·포트폴리오·면접 질문 **생성** 요청
- 이미 정리된 경험에 대한 질문이나 품질 평가 요청
  ("내 경험 어때?", "이거 잘 썼어?")
- 내용 없이 블록 **생성·수정만** 요청 ("블록 하나 만들어줘")
- 블록 **이동·삭제** 요청

# 가장 중요한 규칙

**애매하면 `chat_input` 입니다.** 반영될 여지가 조금이라도 있으면 그렇게
판정하세요. 뒤에 한 번 더 거르는 단계가 있습니다.

`out_of_scope` 는 위 목록에 **명확히** 들어맞을 때만 고릅니다.

# 출력

`intent` 와 `reason` 을 채웁니다. `reason` 은 한국어 한 문장으로, 무엇을 보고
그렇게 판정했는지 적습니다.
"""

ROUTER_USER = """\
{gap_context}사용자 입력:
\"\"\"
{user_message}
\"\"\"
"""

router_prompt = ChatPromptTemplate.from_messages([("system", ROUTER_SYSTEM), ("user", ROUTER_USER)])


def build_gap_context(active_gap: dict | None) -> str:
    """직전 턴의 제안을 프롬프트에 넣을 문구로 만든다.

    gap 답변인지 **판정하지는 않는다** (5-1). 그건 반영 내용 필터링의 일이다.
    여기서는 "직전에 이런 질문을 받았다"는 맥락만 준다 — 그래야 짧은 답변을
    무관한 입력으로 오해하지 않는다.
    """
    if not active_gap:
        return ""

    message = (active_gap.get("message") or "").strip()
    if not message:
        return ""

    return (
        "직전 턴에 에이전트가 아래 질문을 했습니다. "
        "사용자 입력이 이 질문에 대한 답일 수 있습니다.\n"
        f'"{message}"\n\n'
    )
