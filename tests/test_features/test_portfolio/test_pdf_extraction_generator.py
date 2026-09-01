"""PDF 추출 생성기 테스트"""

from types import SimpleNamespace

import pytest

from features.portfolio.pdf_extraction.generator import (
    PdfExtractionGenerationError,
    PdfExtractionGenerator,
)
from features.portfolio.pdf_extraction.schemas import (
    PdfActivity,
    PdfExtractionResult,
    PdfProblemSolvingItem,
)


class DummyStructuredLlm:
    def __init__(self, response: PdfExtractionResult | Exception):
        self._response = response
        self.calls: list[list] = []

    def invoke(self, messages: list):
        self.calls.append(messages)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class DummyLlm:
    def __init__(self, structured_llm: DummyStructuredLlm):
        self._structured_llm = structured_llm
        self.structured_output_schema = None

    def with_structured_output(self, schema: type[PdfExtractionResult]) -> DummyStructuredLlm:
        self.structured_output_schema = schema
        return self._structured_llm


def _sample_result() -> PdfExtractionResult:
    return PdfExtractionResult(
        activities=[
            PdfActivity(
                activity_name="포트폴리오 고도화",
                detail=["PDF 기반 활동 구조화"],
                responsibility=["프롬프트 및 생성기 구현"],
                problem_solving=[
                    PdfProblemSolvingItem(
                        no=1,
                        situation="추출 형식 불명확",
                        strategy="분류 기준 문서화",
                        reason="출력 일관성을 확보하기 위해",
                    )
                ],
                learning=["스키마와 프롬프트 기준을 함께 맞춰야 한다."],
            )
        ]
    )


def test_extract_returns_structured_result(monkeypatch: pytest.MonkeyPatch):
    """생성기는 구조화된 추출 결과를 반환한다."""
    from features.portfolio.pdf_extraction import generator

    expected = _sample_result()
    structured_llm = DummyStructuredLlm(expected)
    llm = DummyLlm(structured_llm)

    monkeypatch.setattr(generator, "get_llm", lambda model=None, temperature=0.7: llm)
    monkeypatch.setattr(generator, "build_pdf_extraction_messages", lambda **_: ["message"])

    result = PdfExtractionGenerator().extract(b"%PDF", "resume.pdf")

    assert result == expected
    assert llm.structured_output_schema is PdfExtractionResult
    assert structured_llm.calls == [["message"]]


def test_extract_wraps_llm_failure(monkeypatch: pytest.MonkeyPatch):
    """LLM 호출 실패는 도메인 예외로 래핑한다."""
    from features.portfolio.pdf_extraction import generator

    structured_llm = DummyStructuredLlm(RuntimeError("timeout"))
    llm = DummyLlm(structured_llm)

    monkeypatch.setattr(generator, "get_llm", lambda model=None, temperature=0.7: llm)
    monkeypatch.setattr(generator, "build_pdf_extraction_messages", lambda **_: ["message"])

    with pytest.raises(
        PdfExtractionGenerationError, match="PDF 추출 생성에 실패했습니다"
    ) as exc_info:
        PdfExtractionGenerator().extract(b"%PDF", "resume.pdf")

    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_extract_uses_pdf_extraction_model_name(monkeypatch: pytest.MonkeyPatch):
    """전용 환경변수 모델명을 우선 사용한다."""
    from features.portfolio.pdf_extraction import generator

    expected = _sample_result()
    structured_llm = DummyStructuredLlm(expected)
    llm_calls: list[dict] = []

    monkeypatch.setenv("PDF_EXTRACTION_MODEL_NAME", "openai/gpt-4.1-mini")
    monkeypatch.setattr(
        generator,
        "get_llm",
        lambda model=None, temperature=0.7: (
            llm_calls.append({"model": model, "temperature": temperature})
            or DummyLlm(structured_llm)
        ),
    )
    monkeypatch.setattr(generator, "build_pdf_extraction_messages", lambda **_: ["message"])

    PdfExtractionGenerator().extract(b"%PDF", "resume.pdf")

    assert llm_calls == [{"model": "openai/gpt-4.1-mini", "temperature": 0.0}]


@pytest.mark.asyncio
async def test_extract_stream_offloads_message_building_to_thread(monkeypatch: pytest.MonkeyPatch):
    """extract_stream 도 extract() 와 동일하게 메시지 생성(파일 읽기 + PDF 인코딩)을
    스레드로 넘겨 이벤트 루프를 블로킹하지 않는다."""
    from features.portfolio.pdf_extraction import generator

    build_calls: list[dict] = []

    def _fake_build_messages(*, file_bytes: bytes, filename: str):
        build_calls.append({"file_bytes": file_bytes, "filename": filename})
        return ["message"]

    async def _fake_astream(messages):
        assert messages == ["message"]
        yield SimpleNamespace(content='{"activities": [')
        yield SimpleNamespace(
            content=(
                '{"activity_name": "A", "detail": [], "responsibility": [], '
                '"problem_solving": [], "learning": []}'
            )
        )
        yield SimpleNamespace(content="]}")

    class DummyStreamLlm:
        def astream(self, messages):
            return _fake_astream(messages)

    monkeypatch.setattr(generator, "get_llm", lambda model=None, temperature=0.7: DummyStreamLlm())
    monkeypatch.setattr(generator, "build_pdf_extraction_messages", _fake_build_messages)

    activities = [
        activity
        async for activity in PdfExtractionGenerator().extract_stream(b"%PDF", "resume.pdf")
    ]

    assert [a.activity_name for a in activities] == ["A"]
    # 스레드로 넘겨도 asyncio.to_thread 는 kwargs 를 그대로 전달한다.
    assert build_calls == [{"file_bytes": b"%PDF", "filename": "resume.pdf"}]


def test_extract_uses_preview_model_by_default(monkeypatch: pytest.MonkeyPatch):
    """기본 PDF 추출 모델은 preview ID를 사용한다."""
    from features.portfolio.pdf_extraction import generator

    expected = _sample_result()
    structured_llm = DummyStructuredLlm(expected)
    llm_calls: list[dict] = []

    monkeypatch.delenv("PDF_EXTRACTION_MODEL_NAME", raising=False)
    monkeypatch.setattr(
        generator,
        "get_llm",
        lambda model=None, temperature=0.7: (
            llm_calls.append({"model": model, "temperature": temperature})
            or DummyLlm(structured_llm)
        ),
    )
    monkeypatch.setattr(generator, "build_pdf_extraction_messages", lambda **_: ["message"])

    PdfExtractionGenerator().extract(b"%PDF", "resume.pdf")

    assert llm_calls == [{"model": "google/gemini-3.1-pro-preview", "temperature": 0.0}]
