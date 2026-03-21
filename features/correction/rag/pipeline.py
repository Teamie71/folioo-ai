"""첨삭용 RAG 파이프라인"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime

from tavily import AsyncTavilyClient

from common.llm.client import get_llm
from common.utils import (
    count_chars,
    get_char_overflow,
    is_within_char_limit,
    truncate_to_char_limit,
)
from features.correction.config import get_correction_rag_config

_DEFAULT_COMPANY_INSIGHT_MAX_LENGTH = 1500
_DEFAULT_CALL_MAX_RETRIES = 3
_DEFAULT_LENGTH_RETRY_MAX_RETRIES = 2

logger = logging.getLogger(__name__)


def _get_recent_year_ranges() -> tuple[str, str, str]:
    """프롬프트용 최신 연도 범위 문자열 반환"""
    current_year = datetime.now().year
    recent_years = f"{current_year - 2}~{current_year}"
    target_years = f"{current_year - 1} 또는 {current_year}"
    target_years_with_suffix = f"{current_year - 1}년, {current_year}년"
    return recent_years, target_years, target_years_with_suffix


@dataclass(slots=True)
class RAGRunResult:
    """RAG 전체 실행 결과"""

    keywords: list[str]
    search_results: list[dict]
    insight: str


class RAGKeywordExtractionError(Exception):
    """RAG 키워드 추출 실패 예외"""


class RAGSearchError(Exception):
    """RAG 검색 실패 예외"""


class RAGInsightGenerationError(Exception):
    """RAG 인사이트 생성 실패 예외"""


class RAGPipeline:
    """키워드 추출 → 검색(스텁) → 인사이트 생성 파이프라인"""

    def __init__(self) -> None:
        rag_config = get_correction_rag_config()

        self._keyword_count = rag_config.keyword_count
        self._max_results_per_keyword = rag_config.max_results_per_keyword
        self._company_insight_max_length = getattr(
            rag_config,
            "company_insight_max_length",
            _DEFAULT_COMPANY_INSIGHT_MAX_LENGTH,
        )
        self._call_max_retries = getattr(rag_config, "call_max_retries", _DEFAULT_CALL_MAX_RETRIES)
        self._length_retry_max_retries = getattr(
            rag_config,
            "length_retry_max_retries",
            _DEFAULT_LENGTH_RETRY_MAX_RETRIES,
        )
        self._llm = get_llm()
        self._tavily_client: AsyncTavilyClient | None = None

    def _get_tavily_client(self) -> AsyncTavilyClient:
        """Tavily 클라이언트를 lazy 초기화 후 재사용"""
        if self._tavily_client is None:
            api_key = os.getenv("TAVILY_API_KEY")
            if not api_key:
                raise ValueError("TAVILY_API_KEY 환경변수가 설정되지 않았습니다.")
            self._tavily_client = AsyncTavilyClient(api_key=api_key)

        return self._tavily_client

    async def run(self, company_name: str, job_title: str, job_description: str) -> RAGRunResult:
        """기업/직무/JD 기반 RAG 실행 결과 생성"""
        keywords = await asyncio.to_thread(
            self._extract_keywords,
            company_name=company_name,
            job_title=job_title,
            job_description=job_description,
        )

        results = await asyncio.gather(*(self._search(query=keyword) for keyword in keywords))
        search_results: list[dict] = [item for sublist in results for item in sublist]

        insight = await asyncio.to_thread(
            self._generate_insight,
            keywords=keywords,
            search_results=search_results,
            company_name=company_name,
            job_title=job_title,
        )

        return RAGRunResult(
            keywords=keywords,
            search_results=search_results,
            insight=insight,
        )

    async def run_from_search_results(
        self,
        search_results: list[dict],
        company_name: str,
        job_title: str,
        keywords: list[str] | None = None,
    ) -> str:
        """기존 검색 결과로 인사이트만 재생성"""
        return await asyncio.to_thread(
            self._generate_insight,
            keywords=keywords,
            search_results=search_results,
            company_name=company_name,
            job_title=job_title,
        )

    def _extract_keywords(
        self, company_name: str, job_title: str, job_description: str
    ) -> list[str]:
        """LLM으로 검색 키워드 추출"""
        recent_years, target_years, _ = _get_recent_year_ranges()
        try:
            response = self._llm.invoke(
                "---\n"
                'input_variables: ["company_name", "job_title", "job_description"]\n'
                "---\n\n"
                "# RAG 검색을 위한 전략적 키워드 추출 프롬프트\n\n"
                "## 역할\n"
                "당신은 '기업 분석 전문가' AI를 위한 사전 정보 수집가입니다. 주어진 채용 공고(기업명, 직무, 직무 설명)를 분석하여, 최종 목표인 '기업 분석 보고서'의 각 항목(①인재상/문화, ②비전/사업, ③강점/약점)을 채울 수 있는 핵심 정보를 검색하기 위한 최적의 키워드를 추출해야 합니다.\n\n"
                "## 채용 공고 입력 형식\n"
                "- 기업명(submissionTarget): 분석 대상 기업의 정식 명칭 또는 약칭\n"
                "- 직무명(jobTitle): 지원하려는 직무의 명칭\n"
                "- JD(jobDescription): 해당 직무의 상세 설명\n\n"
                f"모든 키워드는 최근 3년 이내({recent_years})의 최신 동향, 전략, 트렌드를 반영해야 합니다.\n\n"
                "## 최종 목표: 기업 분석 보고서 생성\n"
                "1. 인재상과 일하는 방식: 회사의 가치, 문화, 원하는 인재상\n"
                "2. 비전과 사업 방향성: 회사의 목표, 신사업, 성장 전략\n"
                "3. 강점과 약점 분석: 시장 내 위치, 경쟁사 대비 차별점, 개선점\n\n"
                f"## 키워드 개수 규칙\n- 반드시 {self._keyword_count}개의 키워드만 생성합니다.\n"
                "- 일반적인 키워드가 아닌, JD의 핵심 사업과 산업을 반영한 구체적인 키워드를 생성하세요.\n"
                "- 반드시 최신성(최근 동향, 트렌드, 전략 변화)을 반영해야 합니다.\n\n"
                "## 키워드 추출 전략\n"
                "1. [인재상/조직문화] 관점 키워드 1개: [기업명] + 조직문화/인재상\n"
                "2. [사업 전략/비전] 관점 키워드 1개: JD의 핵심 사업 키워드를 반드시 포함\n"
                f"3. [최근 사업 동향/투자] 관점 키워드 1개: 반드시 연도({target_years})를 포함\n"
                "4. [시장/경쟁] 관점 키워드 1개: 산업 분야 + 시장 동향/경쟁/트렌드\n"
                "5. [약점/이슈/고객평가] 관점 키워드 1개: [기업명] + 이슈/약점/고객평가\n"
                "6. [직무/역량] 관점 키워드 1개: [직무명] + 필요 역량 또는 [산업] [직무명] 트렌드/전망\n\n"
                "## 처리 과정 및 출력 형식\n"
                "반드시 아래 JSON 형식으로만 출력하십시오:\n\n"
                "```json\n"
                "{\n"
                '  "search_keywords": [\n'
                '    "keyword1",\n'
                '    "keyword2",\n'
                '    "keyword3",\n'
                '    "keyword4",\n'
                '    "keyword5",\n'
                '    "keyword6"\n'
                "  ]\n"
                "}\n"
                "```\n\n"
                "## 절대 규칙\n"
                f"- 반드시 {self._keyword_count}개의 키워드만 생성합니다.\n"
                "- 2번 키워드(비전)는 JD의 핵심 사업 키워드를 필수로 포함해야 합니다.\n"
                f"- 3번 키워드(최근 동향)는 반드시 연도({target_years})를 포함해야 합니다.\n"
                "- 반드시 지정된 JSON 형식으로만 출력하고, 다른 설명은 절대 추가하지 않습니다.\n"
                "- 일반적인 키워드가 아닌, 구체적이고 맥락이 있는 키워드를 생성해야 합니다.\n\n"
                "## 입력 데이터\n\n"
                f"### 기업명 (`submissionTarget`)\n{company_name}\n\n"
                f"### 직무명 (`jobTitle`)\n{job_title}\n\n"
                f"### JD (`jobDescription`)\n{job_description}\n"
            )
        except Exception as exc:
            raise RAGKeywordExtractionError(f"키워드 추출 LLM 호출 실패: {exc}") from exc

        content = getattr(response, "content", response)
        text = str(content).strip()

        if not text:
            return [f"{company_name} {job_title}"]

        keywords: list[str] = []

        json_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
        try:
            data = json.loads(json_text)
            parsed = data.get("search_keywords", []) if isinstance(data, dict) else []
            if isinstance(parsed, list):
                for item in parsed:
                    keyword = str(item).strip()
                    if keyword and keyword not in keywords:
                        keywords.append(keyword)
        except json.JSONDecodeError:
            return [f"{company_name} {job_title}"]

        if not keywords:
            return [f"{company_name} {job_title}"]

        return keywords[: self._keyword_count]

    async def _search(self, query: str) -> list[dict]:
        """웹 검색 — Tavily API 호출"""
        try:
            response = await self._get_tavily_client().search(
                query=query,
                max_results=self._max_results_per_keyword,
            )
        except Exception as exc:
            raise RAGSearchError(f"Tavily 검색 호출 실패: {exc}") from exc

        results = response.get("results", []) if isinstance(response, dict) else []

        normalized_results: list[dict] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not content:
                content = item.get("raw_content")
            normalized_results.append(
                {
                    "title": str(item.get("title") or ""),
                    "content": str(content or ""),
                    "url": str(item.get("url") or ""),
                }
            )

        return normalized_results

    def _generate_insight(
        self,
        keywords: list[str] | None,
        search_results: list[dict],
        company_name: str,
        job_title: str,
    ) -> str:
        """검색 결과를 요약해 첨삭용 기업 인사이트 텍스트 생성"""
        rewrite_feedback = "없음"

        for attempt_index in range(self._length_retry_max_retries + 1):
            text = self._invoke_insight_prompt(
                self._build_insight_prompt(
                    keywords=keywords,
                    search_results=search_results,
                    company_name=company_name,
                    job_title=job_title,
                    rewrite_feedback=rewrite_feedback,
                )
            )

            if is_within_char_limit(text, self._company_insight_max_length):
                return text

            if attempt_index == self._length_retry_max_retries:
                return self._truncate_company_insight(text)

            rewrite_feedback = self._build_length_retry_feedback(text)

    def _invoke_insight_prompt(self, prompt: str) -> str:
        """호출 실패 재시도 정책을 적용해 인사이트 생성"""
        last_exception: Exception | None = None

        for attempt in range(1, self._call_max_retries + 1):
            try:
                response = self._llm.invoke(prompt)
                content = getattr(response, "content", response)
                return str(content).strip()
            except Exception as exc:
                last_exception = exc
                logger.warning(
                    "인사이트 생성 LLM 호출 실패 (%s/%s)",
                    attempt,
                    self._call_max_retries,
                    exc_info=exc,
                )

        raise RAGInsightGenerationError(f"인사이트 생성 LLM 호출 실패: {last_exception}")

    def _build_insight_prompt(
        self,
        *,
        keywords: list[str] | None,
        search_results: list[dict],
        company_name: str,
        job_title: str,
        rewrite_feedback: str,
    ) -> str:
        """기업 인사이트 생성 프롬프트 구성"""
        serialized_keywords = json.dumps(keywords or [], ensure_ascii=False)
        serialized_search_results = json.dumps(search_results, ensure_ascii=False)
        recent_years, _, target_years_with_suffix = _get_recent_year_ranges()
        return (
            "---\n"
            'input_variables: ["company_name", "job_title", "page_contents"]\n'
            "---\n"
            "# 기업 분석 전문가 시스템 프롬프트\n\n"
            "## 역할 정의\n\n"
            "당신은 취업 준비생의 포트폴리오 작성을 돕는 기업 분석 전문가입니다. 제공된 기업 정보를 바탕으로 해당 기업에 대한 심층적이고 전략적인 분석을 수행해야 합니다.\n\n"
            "## 분석 목적\n"
            "단순한 회사 소개가 아닌, 지원자가 자신의 강점을 어떻게 어필할 수 있는지에 대한 인사이트를 제공하는 실무적 분석을 수행합니다.\n\n"
            "## 최신성 중요도\n"
            f"검색 결과 분석 시 최근 3년 이내({recent_years})의 최신성 정보를 우선적으로 반영해야 합니다.\n"
            "- 최근 동향, 최신 전략, 트렌드 등이 포함된 정보를 우선 활용\n"
            f"- 특히 비전과 사업 방향성 섹션에서는 최신 정보({target_years_with_suffix} 관련 내용)를 필수적으로 포함\n\n"
            "## 필수 분석 영역\n\n"
            "### 1. 인재상과 일하는 방식\n"
            "목표: 회사가 추구하는 인재상과 조직문화를 파악하여 지원자가 강조해야 할 역량과 경험을 도출\n"
            "분석 요소: 회사가 중요하게 여기는 인재상과 핵심 가치, 조직문화와 협업 방식, 업무 스타일과 성과 평가 기준, CEO 및 구성원 인터뷰에서 드러나는 조직 철학\n"
            "도출해야 할 것: 이러한 문화에 적합한 경험이나 역량\n\n"
            "### 2. 비전과 사업 방향성\n"
            "목표: 회사의 미래 계획과 사업 전략을 이해하여 그 방향성에 기여할 수 있는 역량을 파악\n"
            f"분석 요소: 회사의 미션, 비전, 핵심 사업 영역, 최근 사업 확장/투자 유치/신규 서비스 런칭 등의 동향({recent_years} 최신 정보 우선), 시장에서의 포지셔닝과 경쟁 전략, 중장기 성장 계획과 목표\n"
            "도출해야 할 것: 회사의 성장 방향에 부합하는 경험이나 스킬\n\n"
            "### 3. 강점과 약점 분석\n"
            "목표: 경쟁사 대비 강점과 시장에서 지적되는 약점을 파악하여 이를 보완할 수 있는 역량 제시\n"
            "분석 요소: 경쟁사 대비 차별화된 강점과 경쟁우위, 고객/사용자 피드백과 시장 평가, 최근 이슈나 개선이 필요한 영역, 업계 내 평판과 브랜드 인식\n"
            "도출해야 할 것: 약점 보완이나 강점 강화에 기여할 수 있는 경험\n\n"
            "## 출력 형식 (절대 규칙)\n"
            "### 기본 규칙\n"
            "- 개요식, 명사 종결\n"
            f"- 최대 {self._company_insight_max_length}자 이내\n"
            "- 90점 이상의 품질 기준 유지\n\n"
            "### 길이 초과 시 재작성 규칙\n"
            "- 기존 섹션 제목, 순서, 구조를 유지\n"
            "- 핵심 사실, 최신 동향, 추천 어필 포인트는 유지\n"
            "- 예시, 수식어, 중복 설명, 부연 설명을 우선 축약\n\n"
            "### 필수 항목 수\n"
            "- 인재상과 일하는 방식: 최소 3개 항목 이상\n"
            "- 비전과 사업 방향성: 최소 3개 항목 이상\n"
            "- 강점: 최소 3개 항목 이상\n"
            "- 약점: 최소 3개 항목 이상\n"
            "- 포트폴리오 작성 전략: 최소 4개 항목 이상\n\n"
            "### 출력 구조\n"
            "### 기업명: [기업명]\n"
            "### 직무: [직무명]\n\n"
            "## 1. 인재상과 일하는 방식\n"
            "- **핵심 인재상**: ...\n"
            "- **조직문화 특징**: ...\n"
            "- **협업 방식**: ...\n\n"
            "## 2. 비전과 사업 방향성\n"
            "- **회사 비전/미션**: ...\n"
            "- **최근 사업 동향**: ...\n"
            "- **중장기 성장 전략**: ...\n\n"
            "## 3. 강점과 약점 분석\n"
            "### 주요 강점\n"
            "- ...\n"
            "- ...\n"
            "- ...\n\n"
            "### 개선 필요 영역\n"
            "- ...\n"
            "- ...\n"
            "- ...\n\n"
            "### 추천 어필 포인트\n"
            "- **강점 활용**: ...\n"
            "- **약점 보완**: ...\n\n"
            "## 포트폴리오 작성 시 핵심 전략\n"
            "1. ...\n"
            "2. ...\n"
            "3. ...\n"
            "4. ...\n\n"
            "## 주의사항\n"
            "- 제공된 정보만을 바탕으로 분석하되, 정보가 부족한 경우 이를 명시\n"
            "- 구체적이고 실행 가능한 어필 전략 제시\n"
            "- 일반적인 회사 소개가 아닌 지원자 맞춤형 인사이트 제공\n"
            "- 객관적이고 균형잡힌 시각으로 분석\n"
            "- 실제 포트폴리오 작성에 즉시 활용 가능한 수준으로 작성\n"
            "- 리포트 마지막에 맺음말이나 추가 설명을 작성하지 말 것\n"
            "- 검색 키워드는 참고 정보로만 사용하고, 사실 근거는 검색 결과를 우선할 것\n\n"
            "## 이전 시도 피드백\n"
            f"{rewrite_feedback}\n\n"
            "## 입력 데이터\n\n"
            f"**회사명**: {company_name}\n"
            f"**직무명**: {job_title}\n\n"
            f"**검색 키워드**: {serialized_keywords}\n\n"
            f"**검색 결과**:\n{serialized_search_results}\n\n"
            "위 검색 결과를 바탕으로 90점 이상 품질의 기업 분석 리포트를 상기 출력 형식에 맞춰 작성해주세요."
        )

    def _build_length_retry_feedback(self, text: str) -> str:
        """길이 초과 전용 재작성 피드백 생성"""
        current_length = count_chars(text)
        overflow = get_char_overflow(text, self._company_insight_max_length)
        return (
            "이전 출력은 글자 수 제한을 초과했습니다.\n"
            f"- 현재 {current_length}자 / 최대 {self._company_insight_max_length}자 "
            f"({overflow}자 초과)\n"
            "- 기존 섹션 제목, 순서, 구조를 유지하세요.\n"
            "- 예시, 수식어, 중복 설명, 부연 설명부터 줄이세요.\n"
            "- 핵심 사실, 최신 동향, 추천 어필 포인트는 유지하세요."
        )

    def _truncate_company_insight(self, text: str) -> str:
        """기업 인사이트 길이를 1500자로 제한"""
        return truncate_to_char_limit(text, self._company_insight_max_length)


__all__ = [
    "RAGPipeline",
    "RAGRunResult",
    "RAGInsightGenerationError",
    "RAGKeywordExtractionError",
    "RAGSearchError",
]
