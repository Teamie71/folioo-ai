"""첨삭용 RAG 파이프라인"""

import json
import os
import re

from tavily import TavilyClient

from common.llm.client import get_llm


class RAGPipeline:
    """키워드 추출 → 검색(스텁) → 인사이트 생성 파이프라인"""

    def __init__(self) -> None:
        self._llm = get_llm()

    def run(self, company_name: str, job_title: str, job_description: str) -> str:
        """기업/직무/JD 기반 기업 인사이트 텍스트 생성"""
        keywords = self._extract_keywords(
            company_name=company_name,
            job_title=job_title,
            job_description=job_description,
        )

        search_results: list[dict] = []
        for keyword in keywords:
            search_results.extend(self._search(query=keyword))

        return self._generate_insight(
            search_results=search_results,
            company_name=company_name,
            job_title=job_title,
        )

    def _extract_keywords(
        self, company_name: str, job_title: str, job_description: str
    ) -> list[str]:
        """LLM으로 검색 키워드 4개 추출"""
        response = self._llm.invoke(
            "당신은 채용 공고 기반 기업 분석 리서처입니다.\n"
            "아래 입력을 분석해 웹 검색용 키워드 4개를 생성하세요.\n"
            "각 키워드는 서로 다른 관점을 커버해야 합니다: 조직문화/인재상, 사업전략/비전, 시장/경쟁, 직무/역량.\n"
            "반드시 JSON만 출력하세요.\n"
            "출력 형식:\n"
            '{"search_keywords": ["keyword1", "keyword2", "keyword3", "keyword4"]}\n\n'
            f"기업명: {company_name}\n"
            f"직무명: {job_title}\n"
            f"JD: {job_description}\n"
        )
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

        return keywords[:4]

    def _search(self, query: str) -> list[dict]:
        """웹 검색 — Tavily API 호출"""
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise ValueError("TAVILY_API_KEY 환경변수가 설정되지 않았습니다.")

        response = TavilyClient(api_key=api_key).search(query=query)
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
        search_results: list[dict],
        company_name: str,
        job_title: str,
    ) -> str:
        """검색 결과를 요약해 첨삭용 기업 인사이트 텍스트 생성"""
        serialized_search_results = json.dumps(search_results, ensure_ascii=False)
        response = self._llm.invoke(
            f"기업명: {company_name}\n"
            f"직무: {job_title}\n"
            f"검색 결과: {serialized_search_results}\n\n"
            "위 내용을 바탕으로 기업 문화, 인재상, 직무 특성을 간결하게 요약해 주세요."
        )
        content = getattr(response, "content", response)
        return str(content).strip()


__all__ = ["RAGPipeline"]
