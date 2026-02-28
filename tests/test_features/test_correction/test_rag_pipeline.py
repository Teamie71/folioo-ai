"""RAG 파이프라인 테스트"""

import pytest

from features.correction.rag.pipeline import RAGPipeline


class _DummyLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


def test_extract_keywords_returns_default_on_non_json(monkeypatch):
    """키워드 추출 시 JSON이 아니면 기본 키워드를 반환한다."""
    from features.correction.rag import pipeline

    dummy_llm = _DummyLLM(["- 네이버 AI\n2. 네이버 문화, 백엔드 역량"])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    rag_pipeline = RAGPipeline()

    keywords = rag_pipeline._extract_keywords("네이버", "백엔드", "JD")

    assert keywords == ["네이버 백엔드"]


def test_extract_keywords_parses_json_output(monkeypatch):
    """키워드 추출 시 JSON 포맷 응답을 우선 파싱한다."""
    from features.correction.rag import pipeline

    dummy_llm = _DummyLLM(
        [
            """```json
{"search_keywords": ["네이버 인재상 조직문화", "네이버 AI 전략", "인터넷 플랫폼 시장 동향", "백엔드 개발자 핵심 역량"]}
```"""
        ]
    )
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    rag_pipeline = RAGPipeline()

    keywords = rag_pipeline._extract_keywords("네이버", "백엔드", "JD")

    assert keywords == [
        "네이버 인재상 조직문화",
        "네이버 AI 전략",
        "인터넷 플랫폼 시장 동향",
        "백엔드 개발자 핵심 역량",
    ]


def test_search_returns_tavily_results(monkeypatch):
    """검색은 Tavily 응답을 title/content/url 형식으로 정규화한다."""
    from features.correction.rag import pipeline

    class _DummyTavilyClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def search(self, query: str) -> dict:
            assert query == "테스트 쿼리"
            return {
                "results": [
                    {"title": "테스트 제목", "content": "테스트 본문", "url": "https://example.com"}
                ]
            }

    dummy_llm = _DummyLLM(["dummy"])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    monkeypatch.setattr(pipeline, "TavilyClient", _DummyTavilyClient)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    rag_pipeline = RAGPipeline()
    results = rag_pipeline._search("테스트 쿼리")

    assert results == [
        {"title": "테스트 제목", "content": "테스트 본문", "url": "https://example.com"}
    ]


def test_run_returns_generated_insight(monkeypatch):
    """run은 키워드 추출/검색/인사이트 생성을 순서대로 수행한다."""
    from features.correction.rag import pipeline

    keyword_extraction_response = (
        '{"search_keywords": ["키워드1", "키워드2", "키워드3", "키워드4"]}'
    )
    insight_generation_response = "생성된 기업 인사이트"

    dummy_llm = _DummyLLM(
        [
            keyword_extraction_response,
            insight_generation_response,
        ]
    )

    class _DummyTavilyClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def search(self, query: str) -> dict:
            return {
                "results": [
                    {"title": f"검색 결과: {query}", "content": "본문", "url": "https://example.com"}
                ]
            }

    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    monkeypatch.setattr(pipeline, "TavilyClient", _DummyTavilyClient)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    rag_pipeline = RAGPipeline()

    insight = rag_pipeline.run("네이버", "백엔드", "JD")

    assert insight == insight_generation_response
    assert "검색 결과" in dummy_llm.prompts[1]


def test_search_raises_when_tavily_api_key_missing(monkeypatch):
    """Tavily API 키가 없으면 예외를 발생시킨다."""
    from features.correction.rag import pipeline

    class _DummyTavilyClient:
        def __init__(self, api_key: str) -> None:  # pragma: no cover
            self.api_key = api_key

        def search(self, query: str) -> dict:  # pragma: no cover
            return {"results": []}

    dummy_llm = _DummyLLM(["dummy"])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    monkeypatch.setattr(pipeline, "TavilyClient", _DummyTavilyClient)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    rag_pipeline = RAGPipeline()

    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        rag_pipeline._search("테스트 쿼리")
