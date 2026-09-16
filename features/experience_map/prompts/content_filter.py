"""반영 내용 필터링 프롬프트 (에이전트 문서 5-3)

입력 전체를 세 가지로 나눈다. **분류만 하고 문장을 다시 쓰지 않는다** — 정제는
5-6의 일이고, 여기서 손대면 원문 추적이 끊긴다.
"""

from langchain_core.prompts import ChatPromptTemplate

CONTENT_FILTER_SYSTEM = """\
당신은 취업 준비생의 경험정리를 돕는 에이전트입니다.
사용자가 준 내용을 **세 가지로 분류**합니다.

# 분류

## gap_answer_items — 활성 gap 답변
직전에 에이전트가 한 질문에 답하거나, 그 질문이 지적한 정보를 제공한 부분.
**활성 gap이 없으면 이 목록은 반드시 비웁니다.**

## new_items — 새로 반영할 내용
사용자가 수행한 경험의 맥락·과정·결과·학습.
**사용자가 반영을 요청한 내용은 반드시 여기에 넣습니다.**

## excluded_reasons — 반영 제외
아래는 블록에 넣지 않습니다. 사유만 한국어로 간단히 적습니다.
- 사용자가 겪지 않은 경험 (남의 이야기, 가정)
- 일반 지식 나열 (개념 설명, 정의)
- 경험정리와 무관한 내용
- 사용자가 제외를 요청한 내용
- **요구사항 텍스트** — "이 문서 정리해줘", "블록으로 만들어줘" 같은
  지시문 자체. 지시 대상인 내용은 분류하되 지시문은 제외합니다.
- **문서 제목·구획 제목** — "담당 업무", "상황", "원인 분석", "해결
  과정", "결과", "주요 성과", "배운 점"처럼 뒤에 나올 내용의 구조만
  알리는 단독 제목. 제목은 분류 문맥으로만 사용하고 item으로 넣지
  않습니다. 단, "문제 해결 경험 — 결제 승인 API 응답 지연"처럼
  제목 자체에 구체적인 경험 요약이 함께 있으면 요약 내용으로 남깁니다.

# 반드시 지킬 것

**원문을 그대로 옮깁니다.** 요약·윤문·재구성하지 마세요. `text` 는 입력에
있는 문장을 **글자 그대로** 잘라낸 것이어야 합니다. 문장을 다듬는 단계는
따로 있습니다.

**없는 내용을 만들지 마세요.** 입력에 없는 역할·성과·수치를 추가하면 안 됩니다.

**한 조각은 한 곳에만** 넣습니다. 같은 문장을 두 목록에 중복해 넣지 마세요.

**한 조각은 500자 이하**로 자릅니다. 긴 문단은 문장이나 불릿 경계에서 여러
조각으로 나누되, 각 조각의 원문을 요약하거나 고치지 마세요.

`item_id` 는 `it_1`, `it_2` … 처럼 요청 안에서 유일한 값으로 붙입니다.
`source` 는 그 내용이 어디서 왔는지에 따라 `message` 또는 `file` 입니다.
"""

CONTENT_FILTER_USER = """\
{gap_section}{message_section}{file_section}{existing_map_section}"""

content_filter_prompt = ChatPromptTemplate.from_messages(
    [("system", CONTENT_FILTER_SYSTEM), ("user", CONTENT_FILTER_USER)]
)


def build_gap_section(active_gap: dict | None) -> str:
    """활성 gap을 프롬프트 구획으로 만든다.

    gap이 없으면 **빈 문자열**이다. 없는데 있는 척하면 LLM이 아무 문장이나
    gap 답변으로 분류한다.
    """
    if not active_gap:
        return "활성 gap이 없습니다. gap_answer_items 는 반드시 비워 두세요.\n\n"

    message = (active_gap.get("message") or "").strip()
    if not message:
        return "활성 gap이 없습니다. gap_answer_items 는 반드시 비워 두세요.\n\n"

    return f'직전 턴에 에이전트가 한 질문:\n"{message}"\n\n'


def build_message_section(user_message: str | None) -> str:
    text = (user_message or "").strip()
    if not text:
        return ""
    return f'사용자 메시지 (source: message):\n"""\n{text}\n"""\n\n'


def build_file_section(extracted_text: str | None) -> str:
    text = (extracted_text or "").strip()
    if not text:
        return ""
    return f'첨부 파일에서 추출한 텍스트 (source: file):\n"""\n{text}\n"""\n'


def build_existing_map_section(activity_tree_text: str | None) -> str:
    """필요할 때만 현재 활동의 기존 내용을 비교 컨텍스트로 제공한다."""
    text = (activity_tree_text or "").strip()
    if not text:
        return ""
    return (
        "\n현재 활동에 이미 저장된 경험정리 블록:\n"
        f'"""\n{text}\n"""\n'
        "사용자가 기존 내용 제외 또는 현재 활동 범위 선별을 요청했다면 위 블록과 "
        "비교하여 중복·범위 밖 내용을 제외하세요. 빈 블록의 가이드 문구는 작성된 "
        "내용으로 간주하지 마세요.\n"
    )
