"""PDF 추출 생성기 테스트"""

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
                detail="PDF 기반 활동 구조화",
                responsibility="프롬프트 및 생성기 구현",
                problem_solving=[
                    PdfProblemSolvingItem(
                        no=1,
                        situation="추출 형식 불명확",
                        strategy="분류 기준 문서화",
                        reason="출력 일관성을 확보하기 위해",
                    )
                ],
                learning="스키마와 프롬프트 기준을 함께 맞춰야 한다.",
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
