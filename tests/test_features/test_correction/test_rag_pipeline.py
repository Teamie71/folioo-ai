"""RAG 파이프라인 테스트"""

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from features.correction.rag.pipeline import (
    RAGInsightGenerationError,
    RAGKeywordExtractionError,
    RAGPipeline,
    RAGSearchError,
)


class _DummyLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


class _FailingLLM:
    def invoke(self, prompt: str) -> str:
        raise RuntimeError("invoke failed")


class _DummyAsyncTavilyClient:
    response_builder: Callable[[str, int], dict] | None = None
    calls: list[tuple[str, int]] = []
    instances_created = 0

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        type(self).instances_created += 1

    async def search(self, query: str, max_results: int) -> dict:
        type(self).calls.append((query, max_results))
        response_builder = type(self).response_builder
        if response_builder is None:
            return {"results": []}
        return response_builder(query, max_results)

    @classmethod
    def reset_state(cls) -> None:
        cls.response_builder = None
        cls.calls = []
        cls.instances_created = 0


@pytest.fixture
def mock_tavily_client(monkeypatch):
    from features.correction.rag import pipeline

    monkeypatch.setattr(pipeline, "AsyncTavilyClient", _DummyAsyncTavilyClient)

    def _configure(
        *,
        response_builder: Callable[[str, int], dict] | None = None,
        api_key: str | None = "test-key",
    ) -> type[_DummyAsyncTavilyClient]:
        _DummyAsyncTavilyClient.reset_state()
        _DummyAsyncTavilyClient.response_builder = response_builder
        if api_key is None:
            monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        else:
            monkeypatch.setenv("TAVILY_API_KEY", api_key)

        return _DummyAsyncTavilyClient

    return _configure


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


def test_extract_keywords_raises_on_llm_invoke_failure(monkeypatch):
    """키워드 추출 LLM 호출이 실패하면 전용 예외를 발생시킨다."""
    from features.correction.rag import pipeline

    monkeypatch.setattr(pipeline, "get_llm", lambda: _FailingLLM())
    rag_pipeline = RAGPipeline()

    with pytest.raises(RAGKeywordExtractionError, match="키워드 추출 LLM 호출 실패"):
        rag_pipeline._extract_keywords("네이버", "백엔드", "JD")


def test_extract_keywords_returns_default_on_json_parse_failure(monkeypatch):
    """키워드 추출 시 JSON 파싱이 실패하면 기본 키워드를 반환한다."""
    from features.correction.rag import pipeline

    dummy_llm = _DummyLLM(['{"search_keywords": ["네이버 전략",]'])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    rag_pipeline = RAGPipeline()

    keywords = rag_pipeline._extract_keywords("네이버", "백엔드", "JD")

    assert keywords == ["네이버 백엔드"]


@pytest.mark.asyncio
async def test_search_returns_tavily_results(monkeypatch, mock_tavily_client):
    """검색은 Tavily 응답을 title/content/url 형식으로 정규화한다."""
    from features.correction.rag import pipeline

    def _response_builder(query: str, max_results: int) -> dict:
        assert query == "테스트 쿼리"
        assert max_results == 5
        return {
            "results": [
                {"title": "테스트 제목", "content": "테스트 본문", "url": "https://example.com"}
            ]
        }

    dummy_llm = _DummyLLM(["dummy"])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    mock_tavily_client(response_builder=_response_builder)

    rag_pipeline = RAGPipeline()
    results = await rag_pipeline._search("테스트 쿼리")

    assert results == [
        {"title": "테스트 제목", "content": "테스트 본문", "url": "https://example.com"}
    ]


@pytest.mark.asyncio
async def test_run_returns_generated_insight(monkeypatch, mock_tavily_client):
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

    def _response_builder(query: str, max_results: int) -> dict:
        assert max_results == 5
        return {
            "results": [
                {
                    "title": f"검색 결과: {query}",
                    "content": "본문",
                    "url": "https://example.com",
                }
            ]
        }

    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    dummy_tavily_client = mock_tavily_client(response_builder=_response_builder)
    rag_pipeline = RAGPipeline()

    insight = await rag_pipeline.run("네이버", "백엔드", "JD")

    assert insight == insight_generation_response
    assert "검색 결과" in dummy_llm.prompts[1]
    assert dummy_tavily_client.instances_created == 1


@pytest.mark.asyncio
async def test_search_raises_when_tavily_api_key_missing(monkeypatch, mock_tavily_client):
    """Tavily API 키가 없으면 검색 예외로 래핑해 전파한다."""
    from features.correction.rag import pipeline

    dummy_llm = _DummyLLM(["dummy"])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    dummy_tavily_client = mock_tavily_client(api_key=None)

    rag_pipeline = RAGPipeline()

    with pytest.raises(RAGSearchError, match="TAVILY_API_KEY"):
        await rag_pipeline._search("테스트 쿼리")

    assert dummy_tavily_client.instances_created == 0


@pytest.mark.asyncio
async def test_search_raises_rag_error_on_tavily_failure(monkeypatch, mock_tavily_client):
    """Tavily 호출이 실패하면 전용 예외를 발생시킨다."""
    from features.correction.rag import pipeline

    def _response_builder(query: str, max_results: int) -> dict:
        raise RuntimeError("tavily down")

    dummy_llm = _DummyLLM(["dummy"])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    mock_tavily_client(response_builder=_response_builder)

    rag_pipeline = RAGPipeline()

    with pytest.raises(RAGSearchError, match="Tavily 검색 호출 실패"):
        await rag_pipeline._search("테스트 쿼리")


@pytest.mark.asyncio
async def test_run_applies_rag_config_values(monkeypatch, mock_tavily_client):
    """RAG 설정값에 따라 키워드 수와 키워드당 검색 개수를 적용한다."""
    from features.correction.rag import pipeline

    dummy_llm = _DummyLLM(
        [
            '{"search_keywords": ["키워드1", "키워드2", "키워드3"]}',
            "생성된 인사이트",
        ]
    )
    monkeypatch.setattr(
        pipeline,
        "get_correction_rag_config",
        lambda: SimpleNamespace(keyword_count=2, max_results_per_keyword=3),
    )
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)

    def _response_builder(query: str, max_results: int) -> dict:
        return {
            "results": [
                {
                    "title": f"검색 결과: {query}",
                    "content": "본문",
                    "url": "https://example.com",
                }
            ]
        }

    dummy_tavily_client = mock_tavily_client(response_builder=_response_builder)

    rag_pipeline = RAGPipeline()
    insight = await rag_pipeline.run("네이버", "백엔드", "JD")

    assert insight == "생성된 인사이트"
    assert len(dummy_tavily_client.calls) == 2
    assert all(max_results == 3 for _, max_results in dummy_tavily_client.calls)
    assert {query for query, _ in dummy_tavily_client.calls} == {"키워드1", "키워드2"}


def test_generate_insight_raises_on_llm_invoke_failure(monkeypatch):
    """인사이트 생성 LLM 호출이 실패하면 전용 예외를 발생시킨다."""
    from features.correction.rag import pipeline

    monkeypatch.setattr(pipeline, "get_llm", lambda: _FailingLLM())
    rag_pipeline = RAGPipeline()

    with pytest.raises(RAGInsightGenerationError, match="인사이트 생성 LLM 호출 실패"):
        rag_pipeline._generate_insight([], "네이버", "백엔드")
