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
        description=text,
        contributions=text,
        achievements=text,
        insights=text,
    )


def _invalid_output() -> PortfolioOutput:
    """빈 텍스트 검증("" 및 "0")에 실패하는 출력."""
    return PortfolioOutput(
        description="0",
        contributions="개요식 텍스트",
        achievements="",
        insights="개요식 텍스트",
    )


def _too_long_output() -> PortfolioOutput:
    """400자 제한을 초과하는 출력."""
    long_text = "가" * 401
    return PortfolioOutput(
        description=long_text,
        contributions="개요식 텍스트",
        achievements="개요식 텍스트",
        insights="개요식 텍스트",
    )


def test_generate_retry_with_validation_feedback(monkeypatch: pytest.MonkeyPatch):
    """검증 실패 시 피드백을 포함해 재시도한다."""
    from features.portfolio import generator

    chain = DummyChain([_invalid_output(), _valid_output()])
    monkeypatch.setattr(generator, "portfolio_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda model=None, temperature=0.7: DummyLLM())
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
    monkeypatch.setattr(generator, "get_llm", lambda model=None, temperature=0.7: DummyLLM())
    monkeypatch.setattr(generator, "format_collected_data_for_prompt", lambda data: "formatted")

    result = PortfolioGenerator().generate(collected_data={}, experience_name="테스트 경험")

    assert result == _valid_output()
    assert len(chain.calls) == 2
    assert chain.calls[0]["validation_feedback"] == "없음"
    assert chain.calls[1]["validation_feedback"] == "없음"


def test_generate_raises_error_after_all_retries(monkeypatch: pytest.MonkeyPatch):
    """최대 재시도 후에도 실패하면 예외를 발생시킨다."""
    from features.portfolio import generator

    chain = DummyChain([_invalid_output(), _invalid_output(), _invalid_output()])
    monkeypatch.setattr(generator, "portfolio_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda model=None, temperature=0.7: DummyLLM())
    monkeypatch.setattr(generator, "format_collected_data_for_prompt", lambda data: "formatted")

    with pytest.raises(PortfolioGenerationError):
        PortfolioGenerator().generate(collected_data={}, experience_name="테스트 경험")


def test_validation_treats_zero_as_empty():
    """문자열 "0"을 빈 텍스트로 검증하는지 확인한다."""
    output = PortfolioOutput(
        description="0",
        contributions="개요식 텍스트",
        achievements="개요식 텍스트",
        insights="개요식 텍스트",
    )

    errors = PortfolioGenerator()._get_validation_errors(output)

    assert "description 섹션이 비어 있습니다." in errors


def test_validation_detects_field_length_limit():
    """필드가 400자를 초과하면 검증 실패한다."""
    errors = PortfolioGenerator()._get_validation_errors(_too_long_output())

    assert "description 섹션이 글자수 제한을 초과했습니다. (현재 401자 / 최대 400자)" in errors


def test_generate_retry_with_length_validation_feedback(monkeypatch: pytest.MonkeyPatch):
    """길이 초과 시 피드백을 포함해 재시도한다."""
    from features.portfolio import generator

    chain = DummyChain([_too_long_output(), _valid_output()])
    monkeypatch.setattr(generator, "portfolio_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda model=None, temperature=0.7: DummyLLM())
    monkeypatch.setattr(generator, "format_collected_data_for_prompt", lambda data: "formatted")

    result = PortfolioGenerator().generate(collected_data={}, experience_name="테스트 경험")

    assert result == _valid_output()
    assert len(chain.calls) == 2
    assert "description: 현재 401자, 1자 초과" in chain.calls[1]["validation_feedback"]


def test_generate_returns_last_output_after_length_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
):
    """길이 초과 재시도 소진 시 마지막 출력을 반환한다."""
    from features.portfolio import generator

    chain = DummyChain([_too_long_output(), _too_long_output(), _too_long_output()])
    monkeypatch.setattr(generator, "portfolio_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda model=None, temperature=0.7: DummyLLM())
    monkeypatch.setattr(generator, "format_collected_data_for_prompt", lambda data: "formatted")

    result = PortfolioGenerator().generate(collected_data={}, experience_name="테스트 경험")

    assert result.description == "가" * 401
    assert len(chain.calls) == 3


def test_generate_uses_llm_settings_and_section_mapping(monkeypatch: pytest.MonkeyPatch):
    """설정의 LLM 파라미터/섹션 매핑 가이드를 반영한다."""
    from features.portfolio import generator

    chain = DummyChain([_valid_output()])
    llm_calls: list[dict] = []

    monkeypatch.setattr(generator, "portfolio_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(
        generator,
        "_load_portfolio_config",
        lambda: generator.PortfolioConfig.model_validate(
            {
                "llm": {
                    "model": "test-model",
                    "temperature": 0.1,
                    "call_max_retries": 1,
                    "length_retry_max_retries": 0,
                },
                "sections": {"description": {"required": True}},
                "section_mapping": {"description": ["stage_1", "stage_2"]},
            }
        ),
    )
    monkeypatch.setattr(
        generator,
        "get_llm",
        lambda model=None, temperature=0.7: (
            llm_calls.append({"model": model, "temperature": temperature}) or DummyLLM()
        ),
    )
    monkeypatch.setattr(generator, "format_collected_data_for_prompt", lambda data: "formatted")

    PortfolioGenerator().generate(collected_data={}, experience_name="테스트 경험")

    assert llm_calls == [{"model": "test-model", "temperature": 0.1}]
    assert "stage_1, stage_2" in chain.calls[0]["section_mapping_guide"]


def test_invalid_portfolio_yaml_type_raises_korean_error(monkeypatch: pytest.MonkeyPatch):
    """포트폴리오 YAML 루트 타입 오류 시 한국어 예외를 반환한다."""
    from features.portfolio import generator

    monkeypatch.setattr(generator.yaml, "safe_load", lambda _: [])

    with pytest.raises(ValueError, match="포트폴리오 설정 파일 형식이 올바르지 않습니다"):
        generator._load_portfolio_config()
