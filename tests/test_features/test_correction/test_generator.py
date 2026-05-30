"""첨삭 생성기 테스트"""

import pytest
from pydantic import ValidationError

from features.correction.config.loader import CorrectionConfig, CorrectionValidationConfig
from features.correction.generator import (
    CorrectionGenerationError,
    CorrectionGenerator,
    _coerce_llm_text_response,
    _combine_decision_with_original_text,
    _format_portfolio_corrections_for_summary,
    _parse_single_correction_decision_output,
    _strip_json_code_fence,
    get_correction_generator,
    reset_correction_generator,
)
from features.correction.schemas import (
    PortfolioCorrectionResult,
    SingleCorrectionDecisionOutput,
    SingleCorrectionOutput,
)


class DummyChain:
    """미리 정의한 응답을 순차 반환하는 체인 모킹 객체."""

    def __init__(self, responses: list):
        self._responses = responses
        self.calls: list[dict] = []

    def invoke(self, prompt_variables: dict):
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
    """LLM 클라이언트를 대체하는 모킹 객체."""


def _output(line_number: int = 1, comment: str | None = "좋습니다.") -> SingleCorrectionOutput:
    return SingleCorrectionOutput.model_validate(
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
            ]
        }
    )


def _output_with_description_line_numbers(
    line_numbers: list[int],
) -> SingleCorrectionOutput:
    """description 필드 라인 번호를 지정한 최종 출력 객체를 생성한다."""
    output = _output().model_dump()
    output["fields"][0]["lines"] = [
        {
            "line_number": line_number,
            "original_text": f"desc {line_number}",
            "type": "keep",
            "comment": None,
        }
        for line_number in line_numbers
    ]
    return SingleCorrectionOutput.model_validate(output)


def _decision_output(
    line_number: int = 1,
    comment: str | None = "좋습니다.",
) -> SingleCorrectionDecisionOutput:
    """LLM decision 전용 출력 객체를 생성한다."""
    return SingleCorrectionDecisionOutput.model_validate(
        {
            "fields": [
                {
                    "field_name": "description",
                    "lines": [
                        {
                            "line_number": line_number,
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
                            "type": "keep",
                            "comment": "좋습니다.",
                        }
                    ],
                },
            ]
        }
    )


def _decision_json(line_number: int = 1, comment: str | None = "좋습니다.") -> str:
    """`_decision_output()`을 JSON 문자열로 직렬화한다."""
    return _decision_output(line_number=line_number, comment=comment).model_dump_json()


def _decision_json_with_empty_insights() -> str:
    """insights에 첨삭 대상 라인이 없는 decision JSON을 생성한다."""
    output = _decision_output().model_dump()
    for field in output["fields"]:
        if field["field_name"] == "insights":
            field["lines"] = []
    return SingleCorrectionDecisionOutput.model_validate(output).model_dump_json()


def _output_with_empty_insights() -> SingleCorrectionOutput:
    """insights에 첨삭 대상 라인이 없는 최종 출력 객체를 생성한다."""
    output = _output().model_dump()
    for field in output["fields"]:
        if field["field_name"] == "insights":
            field["lines"] = []
    return SingleCorrectionOutput.model_validate(output)


def _portfolio_data_for_output() -> dict:
    """`_output()`의 original_text와 같은 원문을 가진 포트폴리오 입력을 생성한다."""
    return {
        "description": "- desc",
        "contributions": "- contri",
        "achievements": "- ach",
        "insights": "- ins",
    }


def test_generate_retry_with_validation_feedback(monkeypatch: pytest.MonkeyPatch):
    """검증 실패 시 피드백을 포함해 재시도한다."""
    from features.correction import generator

    invalid = _decision_json(line_number=2)
    valid = _decision_json(line_number=1)

    chain = DummyChain([invalid, valid])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

    result = CorrectionGenerator(max_retries=2).generate(
        company_name="테스트 회사",
        job_title="백엔드",
        job_description="채용 공고",
        company_insight="인사이트",
        portfolio_data=_portfolio_data_for_output(),
        emphasis_points="강조 포인트",
    )

    assert result == _output(line_number=1)
    assert len(chain.calls) == 2
    assert chain.calls[1]["validation_feedback"].startswith("이전 출력 보완 필요:")


def test_generate_retries_on_fenced_json_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    """LLM이 ```json``` 코드펜스로 감싼 JSON을 반환해도 파싱 후 진행한다."""
    from features.correction import generator

    fenced = f"```json\n{_decision_json(line_number=1)}\n```"
    chain = DummyChain([fenced])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

    result = CorrectionGenerator(max_retries=0).generate(
        company_name="테스트 회사",
        job_title="백엔드",
        job_description="채용 공고",
        company_insight="인사이트",
        portfolio_data=_portfolio_data_for_output(),
        emphasis_points="강조 포인트",
    )

    assert result == _output(line_number=1)


def test_generate_retries_when_response_is_invalid_json(monkeypatch: pytest.MonkeyPatch):
    """첫 응답이 JSON 파싱 실패하면 피드백을 포함해 재시도한다."""
    from features.correction import generator

    chain = DummyChain(["```json\n{not valid json\n```", _decision_json(line_number=1)])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

    result = CorrectionGenerator(max_retries=2).generate(
        company_name="테스트 회사",
        job_title="백엔드",
        job_description="채용 공고",
        company_insight="인사이트",
        portfolio_data=_portfolio_data_for_output(),
        emphasis_points="강조 포인트",
    )

    assert result == _output(line_number=1)
    assert len(chain.calls) == 2
    assert chain.calls[1]["validation_feedback"].startswith("LLM 호출/파싱 실패:")


def test_generate_raises_when_retries_exhausted_by_validation(monkeypatch: pytest.MonkeyPatch):
    """재시도 소진 시 검증 실패 출력을 반환하지 않고 예외를 발생시킨다."""
    from features.correction import generator

    invalid_last = _decision_json(line_number=3)
    chain = DummyChain([invalid_last, invalid_last, invalid_last])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

    with pytest.raises(CorrectionGenerationError, match="line_number 범위 초과"):
        CorrectionGenerator(max_retries=2).generate(
            company_name="테스트 회사",
            job_title="백엔드",
            job_description="채용 공고",
            company_insight="인사이트",
            portfolio_data=_portfolio_data_for_output(),
            emphasis_points="강조 포인트",
        )

    assert len(chain.calls) == 3


def test_generate_raises_error_when_llm_fails_without_output(monkeypatch: pytest.MonkeyPatch):
    """LLM 호출이 계속 실패하면 예외를 발생시킨다."""
    from features.correction import generator

    chain = DummyChain([RuntimeError("timeout"), RuntimeError("timeout")])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

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


def test_generate_accepts_plain_lines_when_field_has_no_bullets(
    monkeypatch: pytest.MonkeyPatch,
):
    """불릿 없는 필드도 plain line fallback으로 첨삭 대상 라인을 만든다."""
    from features.correction import generator

    chain = DummyChain([_decision_json(line_number=1)])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

    result = CorrectionGenerator(max_retries=2).generate(
        company_name="테스트 회사",
        job_title="백엔드",
        job_description="채용 공고",
        company_insight="인사이트",
        portfolio_data={
            "description": "desc",
            "contributions": "- contri",
            "achievements": "- ach",
            "insights": "- ins",
        },
        emphasis_points="강조 포인트",
    )

    assert result == _output()
    assert len(chain.calls) == 1


def test_generate_accepts_empty_field_with_empty_lines(monkeypatch: pytest.MonkeyPatch):
    """원문이 없는 섹션은 빈 lines로 반환하면 첨삭 생성을 통과한다."""
    from features.correction import generator

    chain = DummyChain([_decision_json_with_empty_insights()])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

    result = CorrectionGenerator(max_retries=2).generate(
        company_name="테스트 회사",
        job_title="백엔드",
        job_description="채용 공고",
        company_insight="인사이트",
        portfolio_data={
            "description": "- desc",
            "contributions": "- contri",
            "achievements": "- ach",
            "insights": "",
        },
        emphasis_points="강조 포인트",
    )

    assert result == _output_with_empty_insights()


def test_generate_raises_when_all_fields_have_no_lines(monkeypatch: pytest.MonkeyPatch):
    """모든 섹션에 첨삭 대상 라인이 없으면 LLM 호출 전에 실패한다."""
    from features.correction import generator

    chain = DummyChain([_decision_json_with_empty_insights()])
    monkeypatch.setattr(generator, "correction_generator_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

    with pytest.raises(CorrectionGenerationError, match="첨삭 대상 원본 라인이 없습니다"):
        CorrectionGenerator(max_retries=2).generate(
            company_name="테스트 회사",
            job_title="백엔드",
            job_description="채용 공고",
            company_insight="인사이트",
            portfolio_data={
                "description": "",
                "contributions": "",
                "achievements": "",
                "insights": "",
            },
            emphasis_points="강조 포인트",
        )

    assert chain.calls == []


def test_generate_overall_summary(monkeypatch: pytest.MonkeyPatch):
    """여러 포트폴리오 첨삭 결과를 바탕으로 총평을 생성한다."""
    from features.correction import generator

    chain = DummyChain(["현상 진단\n갭 분석\n솔루션 제안"])
    monkeypatch.setattr(generator, "overall_summary_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

    result = CorrectionGenerator(max_retries=0).generate_overall_summary(
        company_name="테스트 회사",
        job_title="백엔드",
        job_description="채용 공고",
        company_insight="인사이트",
        portfolio_corrections=[
            PortfolioCorrectionResult(portfolio_id=1, fields=_output().fields),
            PortfolioCorrectionResult(portfolio_id=2, fields=_output().fields),
        ],
        emphasis_points="강조 포인트",
    )

    assert result == "현상 진단\n갭 분석\n솔루션 제안"
    assert "포트폴리오 ID: 1" in chain.calls[0]["portfolio_corrections_text"]
    assert "포트폴리오 ID: 2" in chain.calls[0]["portfolio_corrections_text"]


def test_generate_overall_summary_raises_error_for_empty_output(
    monkeypatch: pytest.MonkeyPatch,
):
    """총평 결과가 비어 있으면 예외를 발생시킨다."""
    from features.correction import generator

    chain = DummyChain(["   "])
    monkeypatch.setattr(generator, "overall_summary_prompt", DummyPrompt(chain))
    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())

    with pytest.raises(CorrectionGenerationError, match="총평 생성 결과가 비어 있습니다"):
        CorrectionGenerator(max_retries=0).generate_overall_summary(
            company_name="테스트 회사",
            job_title="백엔드",
            job_description="채용 공고",
            company_insight="인사이트",
            portfolio_corrections=[
                PortfolioCorrectionResult(portfolio_id=1, fields=_output().fields)
            ],
            emphasis_points="강조 포인트",
        )


def test_format_portfolio_corrections_for_summary():
    """총평용 포트폴리오 첨삭 요약 텍스트를 생성한다."""
    summary_text = _format_portfolio_corrections_for_summary(
        [
            PortfolioCorrectionResult(portfolio_id=1, fields=_output().fields),
            PortfolioCorrectionResult(portfolio_id=2, fields=_output().fields),
        ]
    )

    assert "[포트폴리오 ID: 1]" in summary_text
    assert "[포트폴리오 ID: 2]" in summary_text
    assert "1번 | keep | 원문: desc | 코멘트: 좋습니다." in summary_text


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("텍스트 응답", "텍스트 응답"),
        (type("Message", (), {"content": "메시지 응답"})(), "메시지 응답"),
        (
            type("RichMessage", (), {"content": ["첫 줄", {"text": "둘째 줄"}]})(),
            "첫 줄\n둘째 줄",
        ),
    ],
)
def test_coerce_llm_text_response(response: object, expected: str):
    """LLM 응답 객체를 문자열로 정규화한다."""
    assert _coerce_llm_text_response(response) == expected


def test_validate_detects_empty_comment(monkeypatch: pytest.MonkeyPatch):
    """검증에서 빈 코멘트를 잡아낸다."""
    from features.correction import generator

    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())
    output = _output(comment=" ")

    errors = CorrectionGenerator(max_retries=0)._validate(
        output,
        portfolio_data={
            "description": "- 한 줄",
            "contributions": "- 한 줄",
            "achievements": "- 한 줄",
            "insights": "- 한 줄",
        },
    )

    assert "description의 1번 라인 comment가 비어 있습니다." in errors


def test_validate_allows_null_comment_for_keep(monkeypatch: pytest.MonkeyPatch):
    """keep 타입은 comment가 null이어도 검증을 통과한다."""
    from features.correction import generator

    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())
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

    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())
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

    valid_errors = validator._validate(
        _output_with_description_line_numbers([1, 2, 3]),
        portfolio_data=portfolio_data,
    )
    invalid_errors = validator._validate(
        _output_with_description_line_numbers([1, 2, 4]),
        portfolio_data=portfolio_data,
    )

    assert not valid_errors
    assert any("description의 line_number 누락: [3]" in error for error in invalid_errors)
    assert any("description의 line_number 범위 초과: [4]" in error for error in invalid_errors)


def test_validate_detects_missing_duplicate_and_ordered_line_numbers(
    monkeypatch: pytest.MonkeyPatch,
):
    """라인 번호 누락, 중복, 순서 불일치를 검증 오류로 반환한다."""
    from features.correction import generator

    monkeypatch.setattr(generator, "get_llm", lambda **_: DummyLLM())
    validator = CorrectionGenerator(max_retries=0)
    portfolio_data = {
        "description": "- 첫 줄\n- 둘째 줄",
        "contributions": "- 한 줄",
        "achievements": "- 한 줄",
        "insights": "- 한 줄",
    }

    duplicate_errors = validator._validate(
        _output_with_description_line_numbers([1, 1]),
        portfolio_data=portfolio_data,
    )
    reordered_errors = validator._validate(
        _output_with_description_line_numbers([2, 1]),
        portfolio_data=portfolio_data,
    )

    assert any("description의 line_number 누락: [2]" in error for error in duplicate_errors)
    assert any("description의 line_number 중복: [1]" in error for error in duplicate_errors)
    assert any(
        "description의 line_number 순서가 원본과 일치하지 않습니다" in error
        for error in reordered_errors
    )


def test_correction_yaml_max_retries_has_priority(monkeypatch: pytest.MonkeyPatch):
    """max_retries는 correction.yaml(validation) 값을 우선 사용한다."""
    from features.correction import generator

    monkeypatch.setattr(
        generator,
        "get_llm",
        lambda model=None, temperature=0.2, timeout=None: DummyLLM(),
    )
    config = CorrectionConfig.model_validate(
        {
            "llm": {"model": "test-model", "temperature": 0.3},
            "validation": {"max_retries": 5},
        }
    )
    monkeypatch.setattr(
        generator,
        "get_correction_validation_config",
        lambda: config.validation,
    )
    monkeypatch.setattr(generator, "get_correction_llm_config", lambda: config.llm)

    instance = generator.CorrectionGenerator()

    assert instance._max_retries == 5


def test_generator_yaml_fallback_is_used_when_correction_missing(monkeypatch: pytest.MonkeyPatch):
    """max_retries를 arg로 전달하면 config 설정값을 무시한다."""
    from features.correction import generator

    monkeypatch.setattr(
        generator,
        "get_llm",
        lambda model=None, temperature=0.2, timeout=None: DummyLLM(),
    )
    config = CorrectionConfig.model_validate(
        {
            "llm": {"model": "test-model", "temperature": 0.3},
            "validation": {"max_retries": 2},
        }
    )
    monkeypatch.setattr(
        generator,
        "get_correction_validation_config",
        lambda: config.validation,
    )
    monkeypatch.setattr(generator, "get_correction_llm_config", lambda: config.llm)

    instance = generator.CorrectionGenerator(max_retries=4)

    assert instance._max_retries == 4


def test_validate_respects_min_lines_per_field(monkeypatch: pytest.MonkeyPatch):
    """min_lines_per_field 설정을 검증 로직에 반영한다."""
    from features.correction import generator

    monkeypatch.setattr(
        generator,
        "get_llm",
        lambda model=None, temperature=0.2, timeout=None: DummyLLM(),
    )
    monkeypatch.setattr(
        generator,
        "get_correction_validation_config",
        lambda: CorrectionValidationConfig(min_lines_per_field=2, max_retries=1),
    )
    validator = generator.CorrectionGenerator()
    output = _output(line_number=1)

    errors = validator._validate(
        output,
        portfolio_data={
            "description": "- 한 줄",
            "contributions": "- 한 줄",
            "achievements": "- 한 줄",
            "insights": "- 한 줄",
        },
    )

    assert "description 필드는 최소 2개 라인이 필요합니다." in errors


def test_validate_respects_allow_null_comment_for_keep(monkeypatch: pytest.MonkeyPatch):
    """allow_null_comment_for_keep=false면 keep null comment를 실패 처리한다."""
    from features.correction import generator

    monkeypatch.setattr(
        generator,
        "get_llm",
        lambda model=None, temperature=0.2, timeout=None: DummyLLM(),
    )
    monkeypatch.setattr(
        generator,
        "get_correction_validation_config",
        lambda: CorrectionValidationConfig(
            min_lines_per_field=1,
            max_retries=1,
            allow_null_comment_for_keep=False,
        ),
    )
    validator = generator.CorrectionGenerator()
    output = _output(comment=None)

    errors = validator._validate(
        output,
        portfolio_data={
            "description": "- 한 줄",
            "contributions": "- 한 줄",
            "achievements": "- 한 줄",
            "insights": "- 한 줄",
        },
    )

    assert "description의 1번 라인 comment가 비어 있습니다." in errors


def test_invalid_correction_yaml_type_raises_korean_error(monkeypatch: pytest.MonkeyPatch):
    """첨삭 YAML 루트 타입 오류 시 한국어 예외를 반환한다."""
    from features.correction.config import loader

    monkeypatch.setattr(loader.yaml, "safe_load", lambda _: [123])
    loader.load_correction_config.cache_clear()

    with pytest.raises(ValueError, match="첨삭 설정 파일 형식이 올바르지 않습니다"):
        loader.load_correction_config()


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"fields": []}', '{"fields": []}'),
        ('```json\n{"fields": []}\n```', '{"fields": []}'),
        ('```\n{"fields": []}\n```', '{"fields": []}'),
        ('  ```json\n  {"fields": []}\n  ```  ', '{"fields": []}'),
        ('```JSON\n{"fields": []}\n```', '{"fields": []}'),
        ('응답:\n```json\n{"fields": []}\n```\n끝', '{"fields": []}'),
    ],
)
def test_strip_json_code_fence(raw: str, expected: str):
    """다양한 형태의 마크다운 코드펜스를 제거한다."""
    assert _strip_json_code_fence(raw) == expected


def test_parse_single_correction_decision_output_accepts_fenced_json():
    """```json``` 펜스로 감싸진 JSON도 정상 파싱된다."""
    payload = _decision_json(line_number=1)
    parsed = _parse_single_correction_decision_output(f"```json\n{payload}\n```")

    assert parsed == _decision_output(line_number=1)


def test_parse_single_correction_decision_output_uses_valid_fenced_candidate():
    """여러 코드펜스 중 decision 스키마로 검증되는 JSON 후보를 선택한다."""
    invalid_example = '{"fields": [{"field_name": "description", "lines": []}]}'
    valid_payload = _decision_json(line_number=1)

    parsed = _parse_single_correction_decision_output(
        "예시는 아래와 같습니다.\n"
        f"```json\n{invalid_example}\n```\n"
        "실제 응답입니다.\n"
        f"```json\n{valid_payload}\n```"
    )

    assert parsed == _decision_output(line_number=1)


def test_parse_single_correction_decision_output_accepts_embedded_json_object():
    """코드펜스 없이 설명에 섞인 JSON 객체도 검증 가능한 경우 복구한다."""
    parsed = _parse_single_correction_decision_output(
        f"응답은 다음과 같습니다.\n{_decision_json(line_number=1)}"
    )

    assert parsed == _decision_output(line_number=1)


def test_parse_single_correction_decision_output_rejects_original_text():
    """LLM decision 스키마는 original_text 출력을 거부한다."""
    payload = _output(line_number=1).model_dump_json()

    with pytest.raises(ValidationError):
        _parse_single_correction_decision_output(payload)


def test_combine_decision_with_original_text_uses_line_map():
    """decision 결과에 라인맵의 원문을 결합해 최종 출력으로 변환한다."""
    combined = _combine_decision_with_original_text(
        _decision_output(line_number=1),
        {
            "description": {1: "desc"},
            "contributions": {1: "contri"},
            "achievements": {1: "ach"},
            "insights": {1: "ins"},
        },
    )

    assert combined == _output(line_number=1)


def test_parse_single_correction_decision_output_raises_on_empty():
    """공백/펜스만 들어 있으면 ValueError를 발생시킨다."""
    with pytest.raises(ValueError, match="LLM 응답이 비어 있습니다"):
        _parse_single_correction_decision_output("```json\n\n```")


def test_parse_single_correction_decision_output_raises_on_invalid_json():
    """깨진 JSON은 ValidationError로 전파된다."""
    with pytest.raises(ValidationError):
        _parse_single_correction_decision_output("{not valid json")
