"""RAG 파이프라인 테스트"""

from features.correction.rag.pipeline import RAGPipeline


class _DummyLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


def test_extract_keywords_parses_line_and_comma(monkeypatch):
    """키워드 추출 시 줄바꿈/쉼표 구분을 파싱한다."""
    from features.correction.rag import pipeline

    dummy_llm = _DummyLLM(["- 네이버 AI\n2. 네이버 문화, 백엔드 역량"])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    rag_pipeline = RAGPipeline()

    keywords = rag_pipeline._extract_keywords("네이버", "백엔드", "JD")

    assert keywords == ["네이버 AI", "네이버 문화", "백엔드 역량"]


def test_search_returns_stub_result(monkeypatch):
    """검색은 고정된 스텁 결과를 반환한다."""
    from features.correction.rag import pipeline

    dummy_llm = _DummyLLM(["dummy"])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    rag_pipeline = RAGPipeline()

    results = rag_pipeline._search("테스트 쿼리")

    assert results == [
        {
            "title": "Stub result for: 테스트 쿼리",
            "content": "...",
            "url": "https://example.com",
        }
    ]


def test_run_returns_generated_insight(monkeypatch):
    """run은 키워드 추출/검색/인사이트 생성을 순서대로 수행한다."""
    from features.correction.rag import pipeline

    dummy_llm = _DummyLLM(["키워드1\n키워드2", "최종 인사이트"])
    monkeypatch.setattr(pipeline, "get_llm", lambda: dummy_llm)
    rag_pipeline = RAGPipeline()

    insight = rag_pipeline.run("네이버", "백엔드", "JD")

    assert insight == "최종 인사이트"
    assert "Stub result for: 키워드1" in dummy_llm.prompts[1]
