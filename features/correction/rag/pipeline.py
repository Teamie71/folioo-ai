"""첨삭용 RAG 파이프라인"""

import asyncio
import json
import os
import re
from dataclasses import dataclass

from tavily import AsyncTavilyClient

from common.llm.client import get_llm
from features.correction.config import get_correction_rag_config


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
        try:
            response = self._llm.invoke(
                "당신은 채용 공고 기반 기업 분석 리서처입니다.\n"
                f"아래 입력을 분석해 웹 검색용 키워드 {self._keyword_count}개를 생성하세요.\n"
                "각 키워드는 서로 다른 관점을 커버해야 합니다: 조직문화/인재상, 사업전략/비전, 시장/경쟁, 직무/역량.\n"
                "반드시 JSON만 출력하세요.\n"
                "출력 형식:\n"
                '{"search_keywords": ["keyword1", "keyword2", "..."]}\n\n'
                f"기업명: {company_name}\n"
                f"직무명: {job_title}\n"
                f"JD: {job_description}\n"
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
        serialized_keywords = json.dumps(keywords or [], ensure_ascii=False)
        serialized_search_results = json.dumps(search_results, ensure_ascii=False)
        try:
            response = self._llm.invoke(
                f"기업명: {company_name}\n"
                f"직무: {job_title}\n"
                f"검색 키워드: {serialized_keywords}\n"
                f"검색 결과: {serialized_search_results}\n\n"
                "검색 키워드는 참고 정보로만 사용하고, 사실 근거는 검색 결과를 우선해 "
                "기업 문화, 인재상, 직무 특성을 간결하게 요약해 주세요."
            )
        except Exception as exc:
            raise RAGInsightGenerationError(f"인사이트 생성 LLM 호출 실패: {exc}") from exc
        content = getattr(response, "content", response)
        return str(content).strip()


__all__ = [
    "RAGPipeline",
    "RAGRunResult",
    "RAGInsightGenerationError",
    "RAGKeywordExtractionError",
    "RAGSearchError",
]
