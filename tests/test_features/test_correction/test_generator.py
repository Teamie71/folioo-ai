"""첨삭 생성기 테스트"""

import pytest

from features.correction.generator import (
    CorrectionGenerationError,
    CorrectionGenerator,
    get_correction_generator,
    reset_correction_generator,
)
from features.correction.schemas import CorrectionOutput


class DummyChain:
    """미리 정의한 응답을 순차 반환하는 체인 모킹 객체."""

    def __init__(self, responses: list):
        self._responses = responses
        self.calls: list[dict] = []

    def invoke(self, prompt_variables: dict) -> CorrectionOutput:
        self.calls.append(prompt_variables)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class DummyPrompt:
    """`|` 연산 시 DummyChain을 반환하는 프롬프트 모킹 객체."""

    def __init__(self, chain: DummyChain):
        self._chain = chain

    def __or__(self, _: object) -> DummyChain:
        return self._chain


class DummyLLM:
    """structured output 래퍼를 대체하는 LLM 모킹 객체."""

    def with_structured_output(self, _: type[CorrectionOutput]) -> object:
        return object()


def _output(line_number: int = 1, comment: str | None = "좋습니다.") -> CorrectionOutput:
    return CorrectionOutput.model_validate(
        {
            "fields": [
                {
                    "field_name": "description",
                    "lines": [
                        {
                            "line_number": line_number,
                            "original_text": "desc",
                            "type": "keep",
                            "comment": comment,
                        }
                    ],
                },
                {
                    "field_name": "contributions",
                    "lines": [
                        {
                            "line_number": 1,
                            "original_text": "contri",
                            "type": "reduce",
                            "comment": "좋습니다.",
                        }
                    ],
                },
                {
                    "field_name": "achievements",
                    "lines": [
                        {
                            "line_number": 1,
                            "original_text": "ach",
                            "type": "emphasize",
                            "comment": "좋습니다.",
                        }
                    ],
                },
                {
                    "field_name": "insights",
                    "lines": [
                        {
                            "line_number": 1,
                            "original_text": "ins",
                            "type": "keep",
                            "comment": "좋습니다.",
                        }
                    ],
                },
            ],
            "overall_summary": "전체 요약",
        }
    )


def test_generate_retry_with_validation_feedback(monkeypatch: pytest.MonkeyPatch):
    """검증 실패 시 피드백을 포함해 재시도한다."""
    from features.correction import generator

    invalid = _output(line_number=2)
    valid = _output(line_number=1)

    chain = DummyChain([invalid, valid])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda temperature=0.2: DummyLLM())

    result = CorrectionGenerator(max_retries=2).generate(
        company_name="테스트 회사",
        job_title="백엔드",
        job_description="채용 공고",
        company_insight="인사이트",
        portfolio_data={
            "description": "- 한 줄",
            "contributions": "- 한 줄",
            "achievements": "- 한 줄",
            "insights": "- 한 줄",
        },
        emphasis_points="강조 포인트",
    )

    assert result == valid
    assert len(chain.calls) == 2
    assert chain.calls[1]["validation_feedback"].startswith("이전 출력 보완 필요:")


def test_generate_returns_last_output_when_retries_exhausted(monkeypatch: pytest.MonkeyPatch):
    """재시도 소진 시 마지막 출력을 반환한다."""
    from features.correction import generator

    invalid_last = _output(line_number=3)
    chain = DummyChain([invalid_last, invalid_last, invalid_last])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda temperature=0.2: DummyLLM())

    result = CorrectionGenerator(max_retries=2).generate(
        company_name="테스트 회사",
        job_title="백엔드",
        job_description="채용 공고",
        company_insight="인사이트",
        portfolio_data={
            "description": "- 한 줄",
            "contributions": "- 한 줄",
            "achievements": "- 한 줄",
            "insights": "- 한 줄",
        },
        emphasis_points="강조 포인트",
    )

    assert result == invalid_last


def test_generate_raises_error_when_llm_fails_without_output(monkeypatch: pytest.MonkeyPatch):
    """LLM 호출이 계속 실패하면 예외를 발생시킨다."""
    from features.correction import generator

    chain = DummyChain([RuntimeError("timeout"), RuntimeError("timeout")])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda temperature=0.2: DummyLLM())

    with pytest.raises(CorrectionGenerationError):
        CorrectionGenerator(max_retries=1).generate(
            company_name="테스트 회사",
            job_title="백엔드",
            job_description="채용 공고",
            company_insight="인사이트",
            portfolio_data={
                "description": "- 한 줄",
                "contributions": "- 한 줄",
                "achievements": "- 한 줄",
                "insights": "- 한 줄",
            },
            emphasis_points="강조 포인트",
        )


def test_validate_detects_empty_summary_and_comment(monkeypatch: pytest.MonkeyPatch):
    """검증에서 빈 요약과 빈 코멘트를 잡아낸다."""
    from features.correction import generator

    monkeypatch.setattr(generator, "get_llm", lambda temperature=0.2: DummyLLM())
    output = _output(comment=" ")
    output.overall_summary = " "

    errors = CorrectionGenerator(max_retries=0)._validate(
        output,
        portfolio_data={
            "description": "- 한 줄",
            "contributions": "- 한 줄",
            "achievements": "- 한 줄",
            "insights": "- 한 줄",
        },
    )

    assert "overall_summary가 비어 있습니다." in errors
    assert "description의 1번 라인 comment가 비어 있습니다." in errors


def test_validate_allows_null_comment_for_keep(monkeypatch: pytest.MonkeyPatch):
    """keep 타입은 comment가 null이어도 검증을 통과한다."""
    from features.correction import generator

    monkeypatch.setattr(generator, "get_llm", lambda temperature=0.2: DummyLLM())
    output = _output(comment=None)

    errors = CorrectionGenerator(max_retries=0)._validate(
        output,
        portfolio_data={
            "description": "- 한 줄",
            "contributions": "- 한 줄",
            "achievements": "- 한 줄",
            "insights": "- 한 줄",
        },
    )

    assert "description의 1번 라인 comment가 비어 있습니다." not in errors


def test_validate_counts_only_numbered_lines_with_subheaders(monkeypatch: pytest.MonkeyPatch):
    """소구분 헤더를 제외한 번호 라인 수 기준으로 line_number를 검증한다."""
    from features.correction import generator

    monkeypatch.setattr(generator, "get_llm", lambda temperature=0.2: DummyLLM())
    validator = CorrectionGenerator(max_retries=0)
    portfolio_data = {
        "description": (
            "**1) 리소스 부족**\n"
            "- **상황:** 실시간 채팅 서버 구축 불가\n"
            "- **전략:** MVP 스펙으로 전환\n"
            "- **근거:** 핵심 가치 집중"
        ),
        "contributions": "- 한 줄",
        "achievements": "- 한 줄",
        "insights": "- 한 줄",
    }

    valid_errors = validator._validate(_output(line_number=3), portfolio_data=portfolio_data)
    invalid_errors = validator._validate(_output(line_number=4), portfolio_data=portfolio_data)

    assert "description의 line_number 3가 원본 라인 수(3)를 벗어났습니다." not in valid_errors
    assert "description의 line_number 4가 원본 라인 수(3)를 벗어났습니다." in invalid_errors


def test_correction_generator_singleton(monkeypatch: pytest.MonkeyPatch):
    """싱글톤 반환/리셋 동작 테스트"""
    from features.correction import generator

    class FakeGenerator:
        pass

    reset_correction_generator()
    monkeypatch.setattr(generator, "CorrectionGenerator", FakeGenerator)

    first = get_correction_generator()
    second = get_correction_generator()

    assert first is second

    reset_correction_generator()
    third = get_correction_generator()

    assert third is not first
