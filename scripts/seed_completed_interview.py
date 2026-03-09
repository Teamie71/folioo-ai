"""
Swagger API 테스트용 시드 스크립트

3가지 인터뷰 상태를 체크포인터(PostgreSQL)에 주입합니다.
  1. 완료 상태 (4단계 all_stages_complete=True)  → 포트폴리오 생성 테스트용
  2. 중간 진행 상태 (2단계 진행 중)               → 채팅 이어하기 테스트용
  3. 최초 시작 상태 (1단계 첫 질문 직후)           → 처음부터 채팅 테스트용

사용법:
    uv run python scripts/seed_completed_interview.py

앱 서버 실행 전/후 모두 사용 가능합니다 (같은 PostgreSQL DB 공유).
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

# 프로젝트 루트를 sys.path에 추가 (직접 실행 시 모듈 탐색용)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from features.interview.agents.graph import build_graph
from features.interview.agents.state import InterviewState, StageProgress

load_dotenv()

USER_ID = "seed_user"


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _field(
    name: str,
    description: str,
    value: str | list[str],
    completeness: float = 1.0,
) -> dict:
    return {
        "field_name": name,
        "description": description,
        "value": value,
        "completeness": completeness,
    }


def _empty_collected_data() -> dict:
    return {f"stage_{i}": {} for i in range(1, 5)}


# ---------------------------------------------------------------------------
# 1) 완료 상태 — 4단계 all_stages_complete=True
# ---------------------------------------------------------------------------


def build_completed_state(session_id: str) -> InterviewState:
    """AI 챗봇 서비스 개발 — 인터뷰 완료"""
    return {
        "user_id": USER_ID,
        "session_id": session_id,
        "experience_name": "AI 챗봇 서비스 개발",
        "messages": [
            # Stage 1
            AIMessage(
                content="반가워요! AI 챗봇 서비스 개발의 경험 정리를 시작해볼게요. "
                "가장 먼저 이 활동을 시작하게 된 계기가 궁금해요. "
                "이 활동을 통해 중점적으로 해결해야 하는 과제가 있었나요? "
                "언제부터 언제까지 활동했는지, 구체적인 진행 기간도 알려주세요."
            ),
            HumanMessage(
                content="대학교 4학년 캡스톤 수업에서 기업 연계 프로젝트로 시작했어요. "
                "협력 기업 고객센터의 반복 문의 응대 비용이 매달 수백만 원이었는데, "
                "이걸 AI로 자동화하자는 게 목표였습니다. "
                "2025년 3월부터 6월 말까지 약 4개월간 진행했어요."
            ),
            AIMessage(
                content="혼자 진행했는지, 팀으로 진행한 활동인지도 궁금해요. "
                "팀원은 몇 명이었고, 그 중에서 본인은 어떤 역할을 맡았나요?"
            ),
            HumanMessage(
                content="총 4명이서 했어요. 백엔드 2명, 프론트엔드 1명, UX디자인 1명이고 "
                "저는 백엔드 리드로 전체 아키텍처 설계와 LLM 파이프라인을 담당했습니다. "
                "기술 의사결정도 제가 주도했어요."
            ),
            AIMessage(content="좋아요. 이 프로젝트의 타깃이 되는 고객이나 대상이 있었나요?"),
            HumanMessage(
                content="일차적으로는 월 5,000건 이상 문의가 들어오는 중소 이커머스 업체의 "
                "고객센터 담당자가 타깃이었고, 최종 사용자는 쇼핑몰에서 배송·교환·환불 "
                "문의를 하는 일반 고객이에요."
            ),
            # Stage 2
            AIMessage(
                content="이제 본격적으로 어떤 일을 하셨는지 들어보고 싶어요. "
                "진행 과정에서 가장 공들여서 했던 핵심 업무 3~4가지를 꼽는다면 무엇인가요?"
            ),
            HumanMessage(
                content="첫째, LLM 파이프라인 설계 및 프롬프트 엔지니어링이요. "
                "GPT-4o를 기반으로 시스템 프롬프트를 20번 넘게 반복 튜닝했어요. "
                "둘째, FastAPI 기반 REST API 개발. 비동기 처리와 스트리밍 응답을 구현했습니다. "
                "셋째, Pinecone 벡터 DB를 연동한 RAG 파이프라인 구축. "
                "상품 FAQ 3,000건을 임베딩해서 컨텍스트로 활용했어요. "
                "넷째, GitHub Actions + Docker로 CI/CD 파이프라인을 구축해서 "
                "PR마다 자동 테스트·배포가 되도록 했습니다."
            ),
            AIMessage(
                content="그 업무들을 해내기 위해 어떤 도구들을 사용했나요? "
                "기술 스택, 방법론 등 사용한 기술을 모두 자랑해 주세요!"
            ),
            HumanMessage(
                content="Python, FastAPI, LangChain, OpenAI API(GPT-4o), Pinecone, "
                "Redis(캐싱), Docker, GitHub Actions, Pytest, Notion(문서화), "
                "Figma(디자인 핸드오프), Slack(소통). "
                "방법론은 2주 단위 스프린트 + 데일리 스크럼으로 애자일하게 진행했어요."
            ),
            # Stage 2 생성 질문들
            AIMessage(
                content="LLM 파이프라인 설계에서 프롬프트를 20번 넘게 튜닝하셨다고 했는데, "
                "구체적으로 어떤 기준으로 프롬프트 품질을 평가하고 개선해 나가셨나요?"
            ),
            HumanMessage(
                content="정확도, 톤앤매너, 할루시네이션 발생률 세 가지 기준으로 평가했어요. "
                "테스트 질문 50개 세트를 만들어서 매 버전마다 돌리고 점수를 매겼습니다. "
                "특히 '모르는 건 모른다고 답하게' 하는 게 가장 어려웠는데, "
                "few-shot 예시를 시스템 프롬프트에 넣으니까 확 나아졌어요."
            ),
            AIMessage(
                content="RAG 파이프라인에서 3,000건의 FAQ를 임베딩하셨는데, "
                "검색 정확도를 높이기 위해 특별히 신경 쓴 부분이 있나요?"
            ),
            HumanMessage(
                content="청크 사이즈와 오버랩을 여러 조합으로 실험했어요. "
                "최종적으로 512토큰 청크에 64토큰 오버랩이 가장 좋았습니다. "
                "또 질문-답변 쌍을 하나의 청크로 묶는 게 단순 분할보다 "
                "검색 적중률이 30% 정도 높았어요. "
                "메타데이터에 카테고리 태그를 달아서 필터링도 추가했습니다."
            ),
            AIMessage(content="CI/CD 파이프라인 구축 과정에서 가장 신경 쓴 점은 무엇이었나요?"),
            HumanMessage(
                content="테스트 커버리지 80% 이상을 PR 머지 조건으로 걸었어요. "
                "LLM 호출 부분은 모킹 처리하고, API 통합 테스트는 별도 스테이지에서 "
                "실제 호출하되 비용 제한을 두었습니다. "
                "Docker 이미지 빌드 시간이 처음에 8분이었는데 멀티스테이지 빌드로 3분으로 줄였어요."
            ),
            # Stage 3
            AIMessage(
                content="프로젝트를 하다 보면 꼭 예상치 못한 문제를 만나게 되죠. "
                "진행하면서 가장 식은땀 났던 순간이나 어려웠던 점은 무엇이었나요?"
            ),
            HumanMessage(
                content="베타 테스트 첫 주에 API 비용이 하루 10만 원을 넘긴 적이 있어요. "
                "대화가 길어질수록 전체 히스토리를 매번 보내니까 토큰이 기하급수적으로 늘었거든요. "
                "그리고 특정 상품에 대해 완전히 엉뚱한 정보를 자신 있게 답변하는 "
                "환각 현상도 심각했어요. 고객이 실제로 잘못된 환불 안내를 받을 뻔했습니다."
            ),
            AIMessage(
                content="오, 쉽지 않은 상황이었겠네요. "
                "그 문제를 해결하기 위해 구체적으로 어떤 행동을 했고, 어떻게 대처했는지 궁금해요."
            ),
            HumanMessage(
                content="토큰 비용 문제는 두 가지로 접근했어요. "
                "첫째, Redis에 '질문-응답' 캐싱 레이어를 넣어서 동일·유사 질문은 LLM 호출 없이 응답. "
                "둘째, 대화 10턴이 넘으면 이전 대화를 요약 메시지로 압축하는 전략을 도입했습니다. "
                "환각 문제는 RAG 파이프라인 끝에 '사실 검증 체인'을 추가했어요. "
                "LLM 응답에 포함된 수치·정책 정보를 원본 DB와 대조해서 불일치하면 "
                "'정확한 정보를 확인 중입니다'라고 답하도록 바꿨습니다."
            ),
            AIMessage(
                content="왜 하필 그 방법으로 해결하려고 했나요? "
                "그때 머릿속으로 했던 고민이나 판단의 이유를 알려주세요."
            ),
            HumanMessage(
                content="캐싱은 이커머스 특성상 '배송 언제 오나요?', '교환 어떻게 하나요?' 같은 "
                "질문이 전체의 60% 이상이라 적중률이 높을 거라 판단했어요. "
                "대화 요약은 컨텍스트 윈도우를 줄이면서도 맥락 손실을 최소화하는 방법이었고요. "
                "환각 대응에서 답변 차단이 아니라 '확인 중' 으로 우회한 건, "
                "사용자 경험을 해치지 않으면서 신뢰도를 지키는 절충안이었습니다. "
                "처음에는 confidence score 기반 필터링을 시도했는데 "
                "false positive가 너무 많아서 폐기하고 DB 대조 방식으로 전환했어요."
            ),
            # Stage 3 생성 질문들
            AIMessage(
                content="캐싱 적중률이 실제로 얼마나 나왔는지, "
                "그리고 캐시 무효화는 어떻게 관리하셨나요?"
            ),
            HumanMessage(
                content="적중률이 약 45%까지 나왔어요. FAQ 업데이트 시 관련 캐시 키를 "
                "일괄 삭제하는 배치 스크립트를 만들었고, TTL은 24시간으로 설정했습니다. "
                "시즈널 이벤트(세일 기간 등) 때는 수동으로 캐시를 플러시했어요."
            ),
            AIMessage(
                content="대화 요약 전략에서 요약 품질을 어떻게 검증하셨나요? "
                "맥락이 손실되는 케이스는 없었나요?"
            ),
            HumanMessage(
                content="요약 전후로 동일 후속 질문을 던져서 답변 일관성을 비교했어요. "
                "100개 대화 샘플로 테스트했을 때 92%가 맥락 유지됐습니다. "
                "나머지 8%는 주로 구체적인 주문번호 같은 고유값이 누락되는 케이스라 "
                "요약 프롬프트에 '고유 식별자 반드시 유지' 지시를 추가해서 96%까지 올렸어요."
            ),
            AIMessage(
                content="사실 검증 체인의 latency 오버헤드는 어느 정도였고, "
                "사용자 체감에 영향을 미치진 않았나요?"
            ),
            HumanMessage(
                content="평균 300ms 정도 추가됐는데, 스트리밍 응답 방식이라 "
                "체감 지연은 거의 없었어요. 검증 결과가 나올 때까지 "
                "'답변을 확인하고 있어요' 인디케이터를 보여줬거든요. "
                "사용자 만족도 설문에서도 응답 속도에 대한 불만은 3% 미만이었습니다."
            ),
            # Stage 4
            AIMessage(
                content="정말 열심히 노력하셨던 게 느껴지는데, 최종 결과는 어땠나요? "
                "수치적인 성과도 있었나요?"
            ),
            HumanMessage(
                content="교내 캡스톤 경진대회에서 52팀 중 최우수상을 받았어요. "
                "실제 협력 기업 환경에서 파일럿 운영까지 했는데, "
                "월 5,200건 문의 중 약 3,900건을 자동 응대해서 "
                "상담원 업무량이 47% 감소했습니다. "
                "자동 응답 정확도는 95.3%였고, 고객 만족도 NPS가 도입 전 대비 +12점 올랐어요."
            ),
            AIMessage(
                content="개인적인 성장도 궁금하네요. "
                "이 경험을 하기 전의 나와 지금의 나, 비교해 보면 어떤 점이 달라진 것 같나요?"
            ),
            HumanMessage(
                content="기술적으로는 LLM 기반 서비스의 전체 아키텍처를 설계하고 운영할 수 있게 됐어요. "
                "단순히 API 호출하는 수준에서 비용·품질·속도 트레이드오프를 고려한 "
                "엔지니어링 관점을 갖게 된 게 가장 큰 변화입니다. "
                "팀 리드 경험을 통해 기술 의사결정을 문서화하고 팀원을 설득하는 능력도 많이 늘었어요."
            ),
            AIMessage(
                content="만약 이 프로젝트를 다시 한다면, '이것만큼은 더 잘해보고 싶다' 하는 "
                "아쉬움이나, 향후 계획이 있나요?"
            ),
            HumanMessage(
                content="텍스트만 처리했는데, 이미지(상품 사진)나 음성 입력도 지원하면 "
                "훨씬 편리했을 거예요. 멀티모달 확장이 가장 아쉬운 부분입니다. "
                "그리고 단일 에이전트 구조의 한계도 느꼈는데, "
                "LangGraph 기반 멀티 에이전트 아키텍처를 공부하고 있어요. "
                "모니터링도 Prometheus + Grafana로 체계적으로 하고 싶었는데 "
                "시간이 부족해서 기본 로깅만 했거든요. 다음엔 꼭 제대로 해보고 싶습니다."
            ),
        ],
        "current_stage": 4,
        "stage_progress": StageProgress(
            fixed_q_used=3,
            fixed_q_total=3,
            generated_q_used=2,
            generated_q_max=2,
            force_all_generated_q=False,
            is_complete=True,
        ),
        "collected_data": {
            "stage_1": {
                "project_background": _field(
                    "project_background",
                    "이 활동을 시작하게 된 이유",
                    "대학교 4학년 캡스톤 수업에서 기업 연계 프로젝트로 시작. "
                    "협력 기업 고객센터의 반복 문의 응대 비용이 매달 수백만 원이었고, "
                    "AI 챗봇으로 자동화하자는 제안을 팀에서 주도적으로 했음.",
                ),
                "problem_definition": _field(
                    "problem_definition",
                    "프로젝트의 최종 목표 또는 해결하려던 미션",
                    "반복적인 고객 문의(배송·교환·환불)를 AI가 자동 응대하여 "
                    "상담원 업무 부담을 50% 이상 줄이고, 고객 만족도(NPS)를 유지·향상시키는 것.",
                ),
                "message_or_concept": _field(
                    "message_or_concept",
                    "(창작물의 경우) 결과물을 통해 전달하고자 한 핵심 주제",
                    "사람처럼 자연스러운 대화 경험을 제공하면서도, "
                    "잘못된 정보를 전달하지 않는 신뢰할 수 있는 AI 상담 서비스.",
                ),
                "project_duration": _field(
                    "project_duration",
                    "프로젝트 진행 기간 (YYYY.MM.DD ~ YYYY.MM.DD)",
                    "2025.03.01 ~ 2025.06.30 (약 4개월)",
                ),
                "team_composition": _field(
                    "team_composition",
                    "전체 참여 인원 수와 본인의 포지션",
                    "총 4명 (백엔드 2, 프론트엔드 1, UX디자인 1). "
                    "본인은 백엔드 리드로서 전체 아키텍처 설계 및 기술 의사결정 주도.",
                ),
                "target_audience": _field(
                    "target_audience",
                    "결과물 또는 활동의 예상 사용자, 수혜자 또는 구체적인 페르소나",
                    "1차: 월 5,000건 이상 문의가 들어오는 중소 이커머스 업체의 고객센터 담당자. "
                    "2차: 쇼핑몰에서 배송·교환·환불 문의를 하는 일반 고객.",
                ),
            },
            "stage_2": {
                "work_categories": _field(
                    "work_categories",
                    "2개 이상의 업무 카테고리 (리스트)",
                    [
                        "LLM 파이프라인 설계 및 프롬프트 엔지니어링: GPT-4o 기반 시스템 프롬프트 "
                        "20회 이상 반복 튜닝. 정확도·톤앤매너·할루시네이션 발생률 기준 평가. "
                        "테스트 질문 50개 세트로 매 버전 정량 평가 수행.",
                        "FastAPI 기반 REST API 개발: 비동기 처리와 SSE 스트리밍 응답 구현. "
                        "WebSocket이 아닌 SSE를 선택한 이유는 단방향 스트리밍으로 충분했고 "
                        "인프라 복잡도를 낮출 수 있었기 때문.",
                        "Pinecone 벡터 DB 연동 및 RAG 파이프라인 구축: "
                        "상품 FAQ 3,000건을 512토큰 청크·64토큰 오버랩으로 임베딩. "
                        "질문-답변 쌍 단위 청킹으로 검색 적중률 30% 향상. "
                        "카테고리 메타데이터 필터링 추가.",
                        "CI/CD 파이프라인 구축 (GitHub Actions + Docker): "
                        "테스트 커버리지 80% 이상 PR 머지 조건. LLM 호출은 모킹 처리. "
                        "멀티스테이지 Docker 빌드로 이미지 빌드 시간 8분→3분 단축.",
                    ],
                ),
            },
            "stage_3": {
                "problem_episodes": _field(
                    "problem_episodes",
                    "2개 이상의 에피소드 (리스트)",
                    [
                        "토큰 비용 폭증: 베타 테스트 첫 주 하루 API 비용 10만 원 초과. "
                        "대화 히스토리 전체를 매번 전송하는 구조가 원인. "
                        "Redis 캐싱 레이어 도입(동일·유사 질문 적중률 45%)과 "
                        "10턴 이상 대화 요약 압축 전략으로 토큰 사용량 60% 절감. "
                        "이커머스 특성상 반복 질문 비율이 60% 이상이라 캐싱이 효과적이었음. "
                        "캐시 TTL 24시간, FAQ 업데이트 시 일괄 삭제 배치 스크립트 운용.",
                        "환각(Hallucination) 이슈: 특정 상품 환불 정책을 잘못 안내하여 "
                        "실제 고객이 피해 입을 뻔한 사례 발생. "
                        "RAG 파이프라인 끝단에 사실 검증 체인 추가: "
                        "LLM 응답 내 수치·정책 정보를 원본 DB와 대조, 불일치 시 "
                        "'정확한 정보를 확인 중입니다' 로 우회 응답. "
                        "confidence score 기반 필터링은 false positive가 너무 많아 폐기. "
                        "검증 체인 latency +300ms였으나 SSE 스트리밍 덕분에 체감 지연 없었음. "
                        "최종 정확도 95.3% 달성.",
                    ],
                ),
            },
            "stage_4": {
                "final_deliverable": _field(
                    "final_deliverable",
                    "프로젝트의 최종 상태 (완성, 런칭, 제출, 수상 여부 등)",
                    "교내 캡스톤 경진대회 52팀 중 최우수상 수상. "
                    "협력 기업 환경에서 2주간 파일럿 운영 완료. "
                    "기업 측에서 정식 도입 검토 진행 중.",
                ),
                "quantitative_results": _field(
                    "quantitative_results",
                    "수치로 증명 가능한 결과",
                    "월 5,200건 문의 중 3,900건(75%) 자동 응대. "
                    "상담원 업무량 47% 감소. 자동 응답 정확도 95.3%. "
                    "고객 만족도 NPS 도입 전 대비 +12점 향상. "
                    "토큰 비용 최적화 후 월 운영비 약 15만 원 수준.",
                ),
                "qualitative_results": _field(
                    "qualitative_results",
                    "타인의 평가, 피드백 인용, 체감 반응 등 비수치적 결과",
                    "기업 담당자: '실제 서비스 수준에 근접한 완성도. "
                    "특히 잘못된 정보 차단 로직이 인상적이었다.' "
                    "지도교수: '기술 선택의 근거가 명확하고 문제 해결 과정이 체계적. "
                    "비용-품질 트레이드오프 분석이 현업 수준.' "
                    "팀원: '기술 의사결정을 문서화해서 공유해주니 맥락 파악이 쉬웠다.'",
                ),
                "personal_growth": _field(
                    "personal_growth",
                    "경험 전후 달라진 직무 역량 및 태도",
                    "LLM 기반 서비스의 전체 아키텍처를 설계하고 운영까지 경험. "
                    "단순 API 호출 수준에서 비용·품질·속도 트레이드오프를 고려한 "
                    "엔지니어링 관점으로 성장. "
                    "팀 리드로서 기술 의사결정 문서화와 팀원 설득 능력 향상.",
                ),
                "insights_gained": _field(
                    "insights_gained",
                    "대상, 관계 또는 해당 업무나 산업에 대해 새롭게 얻은 관점",
                    "AI 서비스는 모델 성능보다 사용자 경험 설계와 안전장치가 더 중요하다는 것을 체감. "
                    "프롬프트 엔지니어링은 체계적 정량 평가 없이는 감으로 할 수 없음. "
                    "캐싱·요약 같은 전통적 엔지니어링 기법이 LLM 시대에도 여전히 강력함.",
                ),
                "future_plans": _field(
                    "future_plans",
                    "아쉬웠던 점을 보완하기 위한 구체적인 개선 방향 또는 학습 계획",
                    "멀티모달 입력(이미지·음성) 지원으로 상품 사진 기반 문의 응대 확장. "
                    "LangGraph 기반 멀티 에이전트 아키텍처 학습 및 적용. "
                    "Prometheus + Grafana 기반 체계적 모니터링 파이프라인 구축.",
                ),
            },
        },
        "mentioned_insight_ids": [],
        "retrieved_insights": [],
        "uploaded_files": [],
        "current_turn_files": [],
        "file_contexts": [],
        "next_node": "end",
        "all_stages_complete": True,
        "overall_completion_percentage": 100.0,
        "is_extended_mode": False,
        "extension_count": 0,
        "extension_turns_used": 0,
        "extension_turns_max": 3,
        "llm_error": None,
    }


# ---------------------------------------------------------------------------
# 2) 중간 진행 상태 — 2단계 진행 중 (stage 1 완료, stage 2 고정 질문 1개 소화)
# ---------------------------------------------------------------------------


def build_midway_state(session_id: str) -> InterviewState:
    """친환경 패키지 디자인 — 2단계 진행 중"""
    return {
        "user_id": USER_ID,
        "session_id": session_id,
        "experience_name": "친환경 패키지 디자인 프로젝트",
        "messages": [
            # Stage 1
            AIMessage(
                content="반가워요! 친환경 패키지 디자인 프로젝트의 경험 정리를 시작해볼게요. "
                "가장 먼저 이 활동을 시작하게 된 계기가 궁금해요. "
                "이 활동을 통해 중점적으로 해결해야 하는 과제가 있었나요? "
                "언제부터 언제까지 활동했는지, 구체적인 진행 기간도 알려주세요."
            ),
            HumanMessage(
                content="시각디자인 전공 졸업 작품으로 시작했어요. "
                "온라인 주문 급증으로 택배 포장재 쓰레기가 심각해지는 걸 보고, "
                "분리수거 편의성과 심미성을 동시에 잡는 패키지 디자인을 하고 싶었습니다. "
                "2025년 1월부터 5월까지 약 5개월간 진행했어요."
            ),
            AIMessage(
                content="혼자 진행했는지, 팀으로 진행한 활동인지도 궁금해요. "
                "팀원은 몇 명이었고, 그 중에서 본인은 어떤 역할을 맡았나요?"
            ),
            HumanMessage(
                content="3인 팀이었어요. 저는 패키지 구조 설계와 그래픽 디자인 총괄을 맡았고, "
                "팀원 한 명은 소재 리서치, 나머지 한 명은 3D 목업 제작을 담당했습니다."
            ),
            AIMessage(content="좋아요. 이 프로젝트의 타깃이 되는 고객이나 대상이 있었나요?"),
            HumanMessage(
                content="MZ세대 1인 가구를 주요 타깃으로 잡았어요. "
                "분리수거에 관심은 있지만 번거로워서 안 하는 사람들이 "
                "직관적으로 분리배출할 수 있는 패키지를 만들고 싶었습니다. "
                "실질적 클라이언트는 중소 식품 브랜드를 상정했어요."
            ),
            # Stage 2 — 고정 질문 1개 답변 완료
            AIMessage(
                content="이제 본격적으로 어떤 일을 하셨는지 들어보고 싶어요. "
                "진행 과정에서 가장 공들여서 했던 핵심 업무 3~4가지를 꼽는다면 무엇인가요? "
                "구체적으로 어떤 업무들을 했는지 이야기해 주세요."
            ),
            HumanMessage(
                content="첫째, 기존 택배 패키지 100개를 수집해서 분리수거 난이도를 분석했어요. "
                "테이프·스티커·복합소재 때문에 재활용 불가인 케이스를 유형별로 분류했습니다. "
                "둘째, 접착제 없이 조립되는 구조 설계예요. 15번 넘게 프로토타입을 만들었어요. "
                "셋째, 재생 크라프트지 위에 콩기름 잉크로 인쇄 가능한 그래픽 시스템을 디자인했고, "
                "넷째, 실사용자 10명에게 언박싱·분리배출 테스트를 진행해서 피드백을 반영했습니다."
            ),
        ],
        "current_stage": 2,
        "stage_progress": StageProgress(
            fixed_q_used=1,
            fixed_q_total=2,
            generated_q_used=0,
            generated_q_max=3,
            force_all_generated_q=True,
            is_complete=False,
        ),
        "collected_data": {
            "stage_1": {
                "project_background": _field(
                    "project_background",
                    "이 활동을 시작하게 된 이유",
                    "시각디자인 전공 졸업 작품. 온라인 주문 급증으로 택배 포장재 쓰레기가 "
                    "심각해지는 걸 보고, 분리수거 편의성과 심미성을 동시에 잡는 "
                    "패키지 디자인을 기획.",
                ),
                "problem_definition": _field(
                    "problem_definition",
                    "프로젝트의 최종 목표 또는 해결하려던 미션",
                    "접착제 없이 조립 가능하고, 분리배출이 직관적이며, "
                    "심미성도 갖춘 친환경 택배 패키지를 디자인하는 것.",
                ),
                "message_or_concept": _field(
                    "message_or_concept",
                    "(창작물의 경우) 결과물을 통해 전달하고자 한 핵심 주제",
                    "'버리는 순간도 디자인이다' — 언박싱부터 분리배출까지 "
                    "전체 경험을 설계한다는 메시지.",
                ),
                "project_duration": _field(
                    "project_duration",
                    "프로젝트 진행 기간 (YYYY.MM.DD ~ YYYY.MM.DD)",
                    "2025.01.06 ~ 2025.05.23 (약 5개월)",
                ),
                "team_composition": _field(
                    "team_composition",
                    "전체 참여 인원 수와 본인의 포지션",
                    "3인 팀. 본인은 패키지 구조 설계 + 그래픽 디자인 총괄. "
                    "팀원 1: 소재 리서치, 팀원 2: 3D 목업 제작.",
                ),
                "target_audience": _field(
                    "target_audience",
                    "결과물 또는 활동의 예상 사용자, 수혜자 또는 구체적인 페르소나",
                    "MZ세대 1인 가구. 분리수거에 관심 있지만 번거로워서 안 하는 사람들. "
                    "실질적 클라이언트는 중소 식품 브랜드 상정.",
                ),
            },
            "stage_2": {
                "work_categories": _field(
                    "work_categories",
                    "2개 이상의 업무 카테고리 (리스트)",
                    [
                        "기존 택배 패키지 100개 수집 후 분리수거 난이도 분석 및 유형 분류",
                        "접착제 없이 조립되는 구조 설계 (프로토타입 15회 이상 반복)",
                        "재생 크라프트지 + 콩기름 잉크 기반 그래픽 시스템 디자인",
                        "실사용자 10명 대상 언박싱·분리배출 사용성 테스트 및 피드백 반영",
                    ],
                    completeness=0.6,
                ),
            },
            "stage_3": {},
            "stage_4": {},
        },
        "mentioned_insight_ids": [],
        "retrieved_insights": [],
        "uploaded_files": [],
        "current_turn_files": [],
        "file_contexts": [],
        "next_node": "question_generator",
        "all_stages_complete": False,
        "overall_completion_percentage": 32.0,
        "is_extended_mode": False,
        "extension_count": 0,
        "extension_turns_used": 0,
        "extension_turns_max": 3,
        "llm_error": None,
    }


# ---------------------------------------------------------------------------
# 3) 최초 시작 상태 — 1단계 첫 질문 직후 (사용자 답변 대기)
# ---------------------------------------------------------------------------


def build_initial_state(session_id: str) -> InterviewState:
    """데이터 시각화 대시보드 — 첫 질문 직후"""
    from features.interview.config.loader import load_stage_config

    stage_1_config = load_stage_config(1)

    return {
        "user_id": USER_ID,
        "session_id": session_id,
        "experience_name": "데이터 시각화 대시보드 개발",
        "messages": [
            AIMessage(
                content="반가워요! 데이터 시각화 대시보드 개발의 경험 정리를 시작해볼게요. "
                "가장 먼저 이 활동을 시작하게 된 계기가 궁금해요. "
                "이 활동을 통해 중점적으로 해결해야 하는 과제가 있었나요? "
                "언제부터 언제까지 활동했는지, 구체적인 진행 기간도 알려주세요."
            ),
        ],
        "current_stage": 1,
        "stage_progress": StageProgress(
            fixed_q_used=1,
            fixed_q_total=len(stage_1_config.fixed_questions),
            generated_q_used=0,
            generated_q_max=stage_1_config.max_generated_questions,
            force_all_generated_q=stage_1_config.force_all_generated_questions,
            is_complete=False,
        ),
        "collected_data": {f"stage_{i}": {} for i in range(1, 5)},
        "mentioned_insight_ids": [],
        "retrieved_insights": [],
        "uploaded_files": [],
        "current_turn_files": [],
        "file_contexts": [],
        "next_node": "question_generator",
        "all_stages_complete": False,
        "overall_completion_percentage": 0.0,
        "is_extended_mode": False,
        "extension_count": 0,
        "extension_turns_used": 0,
        "extension_turns_max": 3,
        "llm_error": None,
    }


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

SEEDS = [
    ("completed", build_completed_state, "4단계 완료"),
    ("midway", build_midway_state, "2단계 진행 중"),
    ("initial", build_initial_state, "1단계 첫 질문 직후"),
]


async def main():
    db_url = os.getenv("CHECKPOINT_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError(
            "CHECKPOINT_DATABASE_URL 또는 DATABASE_URL 환경변수가 설정되지 않았습니다."
        )

    results: list[dict] = []

    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()
        graph = build_graph(checkpointer=checkpointer)

        for tag, builder, label in SEEDS:
            session_id = f"seed-{tag}-{uuid4()}"
            config = {"configurable": {"thread_id": session_id}}

            state = builder(session_id)
            await graph.aupdate_state(config, state)

            # 검증
            snapshot = await graph.aget_state(config)
            results.append(
                {
                    "label": label,
                    "session_id": session_id,
                    "experience": state["experience_name"],
                    "stage": snapshot.values["current_stage"],
                    "complete": snapshot.values["all_stages_complete"],
                    "pct": snapshot.values["overall_completion_percentage"],
                }
            )

    # 결과 출력
    print()
    print("=" * 70)
    print("  시드 데이터 주입 완료")
    print("=" * 70)
    print(f"  DB: {db_url}")
    print(f"  user_id: {USER_ID}")
    print("-" * 70)

    for r in results:
        print(f"\n  [{r['label']}]  {r['experience']}")
        print(f"    session_id : {r['session_id']}")
        print(f"    stage      : {r['stage']}  |  complete: {r['complete']}")
        print(f"    progress   : {r['pct']}%")

    print()
    print("-" * 70)
    print("  Swagger 테스트 예시")
    print("-" * 70)

    completed = results[0]
    midway = results[1]
    initial = results[2]

    print("\n  # 상태 조회 (모든 세션 공통)")
    print("  GET /api/v1/interview/sessions/<session_id>/state")

    print("\n  # 포트폴리오 생성 (완료 세션)")
    print("  POST /api/v1/portfolio/generate")
    print(f'  body: {{"session_id": "{completed["session_id"]}", "user_id": "{USER_ID}"}}')

    print("\n  # 채팅 이어하기 (중간 진행 세션)")
    print(f"  POST /api/v1/interview/sessions/{midway['session_id']}/chat")
    print('  body: {"message": "디자인 도구는 Figma와 Illustrator를 사용했어요."}')

    print("\n  # 채팅 시작 (초기 세션)")
    print(f"  POST /api/v1/interview/sessions/{initial['session_id']}/chat")
    print(
        '  body: {"message": "회사 인턴으로 근무하면서 시작했고, '
        '마케팅팀 KPI를 한눈에 볼 수 있는 대시보드가 필요했어요."}'
    )

    print()


if __name__ == "__main__":
    asyncio.run(main())
