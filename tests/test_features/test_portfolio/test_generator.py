"""포트폴리오 생성기 테스트"""

import pytest

from features.portfolio.generator import PortfolioGenerationError, PortfolioGenerator
from features.portfolio.schemas import PortfolioOutput


class DummyChain:
    def __init__(self, responses: list):
        self._responses = responses
        self.calls: list[dict] = []

    def invoke(self, prompt_variables: dict) -> PortfolioOutput:
        self.calls.append(prompt_variables)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class DummyPrompt:
    def __init__(self, chain: DummyChain):
        self._chain = chain

    def __or__(self, _: object) -> DummyChain:
        return self._chain


class DummyLLM:
    def with_structured_output(self, _: type[PortfolioOutput]) -> object:
        return object()


def _valid_output() -> PortfolioOutput:
    text = "포트폴리오 문단 " * 20
    return PortfolioOutput(
        detail_info=text,
        assigned_task=text,
        problem_solving=text,
        lessons_learned=text,
    )


def _invalid_output() -> PortfolioOutput:
    return PortfolioOutput(
        detail_info="짧음",
        assigned_task="- 항목1\n- 항목2",
        problem_solving="",
        lessons_learned="짧음",
    )


def test_generate_retry_with_validation_feedback(monkeypatch: pytest.MonkeyPatch):
    """검증 실패 시 피드백을 포함해 재시도한다."""
    from features.portfolio import generator

    chain = DummyChain([_invalid_output(), _valid_output()])
    monkeypatch.setattr(generator, "portfolio_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda temperature=0.7: DummyLLM())
    monkeypatch.setattr(generator, "format_collected_data_for_prompt", lambda data: "formatted")

    result = PortfolioGenerator().generate(collected_data={}, experience_name="테스트 경험")

    assert result == _valid_output()
    assert len(chain.calls) == 2
    assert chain.calls[1]["validation_feedback"].startswith("이전 출력 보완 필요:")


def test_generate_retry_after_llm_exception(monkeypatch: pytest.MonkeyPatch):
    """LLM 예외 발생 후 재시도에 성공한다."""
    from features.portfolio import generator

    chain = DummyChain([RuntimeError("timeout"), _valid_output()])
    monkeypatch.setattr(generator, "portfolio_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda temperature=0.7: DummyLLM())
    monkeypatch.setattr(generator, "format_collected_data_for_prompt", lambda data: "formatted")

    result = PortfolioGenerator().generate(collected_data={}, experience_name="테스트 경험")

    assert result == _valid_output()
    assert len(chain.calls) == 2
    assert "LLM 호출/파싱 실패" in chain.calls[1]["validation_feedback"]


def test_generate_raises_error_after_all_retries(monkeypatch: pytest.MonkeyPatch):
    """최대 재시도 후에도 실패하면 예외를 발생시킨다."""
    from features.portfolio import generator

    chain = DummyChain([_invalid_output(), _invalid_output(), _invalid_output()])
    monkeypatch.setattr(generator, "portfolio_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda temperature=0.7: DummyLLM())
    monkeypatch.setattr(generator, "format_collected_data_for_prompt", lambda data: "formatted")

    with pytest.raises(PortfolioGenerationError):
        PortfolioGenerator().generate(collected_data={}, experience_name="테스트 경험")
