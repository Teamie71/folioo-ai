"""파일 전용 경로 프롬프트: 문서 관문, 줄 번호별 칸 배정, 제외 확인."""

from langchain_core.prompts import ChatPromptTemplate

HEADING_SLOT = "HEADING"
EXCLUDE_SLOT = "EXCLUDE"

DOCUMENT_GATE_SYSTEM = """사용자가 '경험 정리'에 올린 문서입니다. 이 문서가 작성자 본인이 직접 겪은 경험
(프로젝트·활동·업무·인턴·아르바이트·대회·학습 과정 등)을 서술하는지 판단하세요.
- 경험 정리 메모, 이력서, 포트폴리오, 회고, 자기소개서 속 경험 서술, 면접 대비 Q&A는 true.
- 관련 없는 문장이 일부 섞여 있어도 경험 서술이 들어 있으면 true.
- 시험지·문제집, 채용 공고, 강의 자료·노트, 기사·논문 요약, 매뉴얼처럼 본인의 경험이 아닌 자료는 false.
애매하면 true로 답하세요."""

ASSIGN_SYSTEM = """경험 정리 문서의 각 줄을 템플릿 칸에 배정합니다. 원문을 다시 쓰지 말고 id별 배정만 돌려주세요.
모든 [id]에 대해 빠짐없이 정확히 한 번씩 답하세요.

- 줄이 구획·소제목 역할만 하는 제목(예: "상세정보", "원인 분석", "## 🎯 왜 만들었나", "Q. 무엇을 배웠나요?")이면
  slot_id="HEADING". 내용이 조금이라도 있으면(기간·인원·성과 등) HEADING이 아닙니다.
- 담당업무·문제해결: 한 업무/에피소드를 대표하는 첫 줄은 그 section의 SUMMARY(앵커) slot, episode는 자기 id.
  그 아래 줄은 한 템플릿의 알맞은 slot, episode는 그 첫 줄 id. 한 에피소드 안에서는 한 템플릿만 쓰세요.
  대표 줄이 따로 없으면 첫 내용 줄을 SUMMARY로 삼으세요.
- 이 경험과 관련 없는 줄(다른 경험 이야기, 지원 동기·취미 같은 자기소개, 문서 안내문)은 slot_id="EXCLUDE"와 reason.
  기간·인원·역할·업무·문제·성과·배운 점이 조금이라도 들어 있으면 EXCLUDE가 아닙니다. 애매하면 배정하세요.
- 상세정보·주요성과·배운 점은 episode=null.
- 기간(언제부터 언제까지, N개월), 소속·인원 구성, 사용한 기술·도구·방법론을 알려주는 문장은 담당업무가 아니라
  상세정보(DETAIL.PERIOD / DETAIL.ROLE / DETAIL.STACK)입니다. 한 문장에 여러 개가 섞이면 가장 중심인 것 하나를 고르세요.
- 원인과 해결이 서로 다른 문제는 별개의 에피소드입니다("첫 번째 문제는…", "두 번째는…", 번호·문단이 바뀌며
  새 문제가 나오면 새 에피소드). 하나의 문제에 대한 원인·해결·결과는 같은 에피소드입니다.

카탈로그:
{catalog}"""

EXCLUDE_VERIFY_SYSTEM = """경험 정리 문서에서 아래 줄들을 '이 경험과 무관'하다고 보고 제외하려 합니다.
문서 전체를 보고, 각 줄이 이 문서의 주된 경험의 기간·소속·역할·업무·문제·성과·배운 점을 조금이라도 담고 있으면
keep=true, 다른 경험·자기소개(지원 동기, 취미)·개인정보·문서 안내문뿐이면 keep=false로 답하세요."""

document_gate_prompt = ChatPromptTemplate.from_messages(
    [("system", DOCUMENT_GATE_SYSTEM), ("human", "{document}")]
)

assign_prompt = ChatPromptTemplate.from_messages(
    [("system", ASSIGN_SYSTEM), ("human", "{document}")]
)

reassign_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", ASSIGN_SYSTEM),
        (
            "human",
            "문서:\n{document}\n\n이전 응답에서 다음 id가 빠졌거나 잘못 배정됐습니다. 이 줄들은 모두 "
            "내용이므로 HEADING이 아닌 카탈로그 slot_id로만 배정하세요: {ids}",
        ),
    ]
)

exclude_verify_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", EXCLUDE_VERIFY_SYSTEM),
        ("human", "문서:\n{document}\n\n제외 후보:\n{candidates}"),
    ]
)
