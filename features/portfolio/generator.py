"""포트폴리오 생성기"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from common.llm.client import get_llm
from common.utils import count_chars, get_char_overflow

from .prompts.portfolio_generator import (
    format_collected_data_for_prompt,
    format_section_mapping_guide,
    portfolio_generator_prompt,
)
from .schemas import PortfolioOutput

_DEFAULT_CALL_MAX_RETRIES = 3
_DEFAULT_LENGTH_RETRY_MAX_RETRIES = 2
_SECTION_FIELDS = ("description", "contributions", "achievements", "insights")
_PORTFOLIO_FIELD_MAX_LENGTH = 400
# min_length 제약 제거 후 LLM이 반환하는 플레이스홀더("0")와 빈 문자열을 빈 응답으로 간주한다.
_EMPTY_TEXT_VALUES = {"", "0"}


class PortfolioLlmConfig(BaseModel):
    """포트폴리오 LLM 설정"""

    model: str = "openai/gpt-oss-120b"
    temperature: float = 0.7
    call_max_retries: int = Field(default=_DEFAULT_CALL_MAX_RETRIES, ge=1)
    length_retry_max_retries: int = Field(default=_DEFAULT_LENGTH_RETRY_MAX_RETRIES, ge=0)


class PortfolioConfig(BaseModel):
    """포트폴리오 설정"""

    sections: dict[str, dict[str, object]] = {}
    llm: PortfolioLlmConfig = PortfolioLlmConfig()
    section_mapping: dict[str, list[str]] = {}


class PortfolioGenerationError(Exception):
    """포트폴리오 생성 실패 예외"""


class PortfolioGenerator:
    """LLM 기반 포트폴리오 생성기"""

    def __init__(self) -> None:
        self._config = _load_portfolio_config()
        self._section_rules = self._config.sections
        self._section_mapping_guide = format_section_mapping_guide(self._config.section_mapping)

    def generate(self, collected_data: dict, experience_name: str) -> PortfolioOutput:
        """수집 데이터를 바탕으로 포트폴리오 텍스트 생성"""
        validation_feedback = "없음"
        last_failure_reason: str | None = None

        max_length_attempts = self._config.llm.length_retry_max_retries + 1
        for attempt_index in range(max_length_attempts):
            output = self._invoke_portfolio_generation(
                collected_data=collected_data,
                experience_name=experience_name,
                validation_feedback=validation_feedback,
            )

            required_errors = self._get_required_field_errors(output)
            if required_errors:
                last_failure_reason = "; ".join(required_errors)
                if attempt_index == max_length_attempts - 1:
                    break

                validation_feedback = self._build_validation_feedback(required_errors)
                continue

            length_errors = self._get_length_errors(output)
            if not length_errors:
                return output

            last_failure_reason = "; ".join(length_errors)
            if attempt_index == max_length_attempts - 1:
                return output

            validation_feedback = self._build_length_retry_feedback(output)

        raise PortfolioGenerationError(
            "포트폴리오 생성에 실패했습니다. "
            f"최대 시도({max_length_attempts}회) 후 중단: {last_failure_reason or '알 수 없는 오류'}"
        )

    def _invoke_portfolio_generation(
        self,
        *,
        collected_data: dict,
        experience_name: str,
        validation_feedback: str,
    ) -> PortfolioOutput:
        """호출 실패 재시도 정책을 적용해 포트폴리오 생성"""
        prompt_variables = {
            "experience_name": experience_name,
            "collected_data_text": format_collected_data_for_prompt(collected_data),
            "section_mapping_guide": self._section_mapping_guide,
            "validation_feedback": validation_feedback,
        }

        last_exception: Exception | None = None
        for _ in range(self._config.llm.call_max_retries):
            try:
                llm = get_llm(
                    model=self._config.llm.model,
                    temperature=self._config.llm.temperature,
                )
                structured_llm = llm.with_structured_output(PortfolioOutput)
                chain = portfolio_generator_prompt | structured_llm
                return chain.invoke(prompt_variables)
            except Exception as exc:
                last_exception = exc

        raise PortfolioGenerationError(
            "포트폴리오 생성에 실패했습니다. "
            f"LLM 호출 최대 시도({self._config.llm.call_max_retries}회) 후 중단: "
            f"{last_exception or '알 수 없는 오류'}"
        )

    def _validate_output(self, output: PortfolioOutput) -> bool:
        """생성 결과가 검증 규칙을 만족하는지 확인"""
        return len(self._get_validation_errors(output)) == 0

    def _get_validation_errors(self, output: PortfolioOutput) -> list[str]:
        return [*self._get_required_field_errors(output), *self._get_length_errors(output)]

    def _get_required_field_errors(self, output: PortfolioOutput) -> list[str]:
        errors: list[str] = []

        for field_name in _SECTION_FIELDS:
            text = getattr(output, field_name, "").strip()
            required = self._section_rules.get(field_name, {}).get("required", True)

            if required and text in _EMPTY_TEXT_VALUES:
                errors.append(f"{field_name} 섹션이 비어 있습니다.")

        return errors

    def _get_length_errors(self, output: PortfolioOutput) -> list[str]:
        errors: list[str] = []

        for field_name in _SECTION_FIELDS:
            text = getattr(output, field_name, "").strip()
            current_length = count_chars(text)
            overflow = get_char_overflow(text, _PORTFOLIO_FIELD_MAX_LENGTH)

            if overflow > 0:
                errors.append(
                    f"{field_name} 섹션이 글자수 제한을 초과했습니다. "
                    f"(현재 {current_length}자 / 최대 {_PORTFOLIO_FIELD_MAX_LENGTH}자)"
                )

        return errors

    @staticmethod
    def _build_validation_feedback(errors: list[str]) -> str:
        """일반 검증 실패 피드백 생성"""
        return "이전 출력 보완 필요:\n- " + "\n- ".join(errors)

    def _build_length_retry_feedback(self, output: PortfolioOutput) -> str:
        """길이 초과 전용 재작성 피드백 생성"""
        lines = [
            "이전 출력은 글자 수 제한을 초과했습니다. 초과한 섹션만 다시 축약하세요.",
            "핵심 사실, 역할, 성과는 유지하고 수식어, 중복 표현, 부연 설명 순으로 줄이세요.",
        ]

        for field_name in _SECTION_FIELDS:
            text = getattr(output, field_name, "").strip()
            current_length = count_chars(text)
            overflow = get_char_overflow(text, _PORTFOLIO_FIELD_MAX_LENGTH)
            if overflow == 0:
                continue

            lines.append(
                f"- {field_name}: 현재 {current_length}자, {overflow}자 초과. "
                "항목 구조와 핵심 성과를 유지한 채 더 짧게 다시 작성하세요."
            )

        return "\n".join(lines)


def _load_portfolio_config() -> PortfolioConfig:
    config_path = Path(__file__).resolve().parent / "config" / "portfolio.yaml"
    try:
        with config_path.open(encoding="utf-8") as file:
            config = yaml.safe_load(file)
    except OSError as exc:
        raise ValueError(f"포트폴리오 설정 파일을 읽을 수 없습니다: {exc}") from exc

    if config is None:
        config = {}

    if not isinstance(config, dict):
        raise ValueError("포트폴리오 설정 파일 형식이 올바르지 않습니다. (YAML 객체 필요)")

    try:
        return PortfolioConfig(**config)
    except ValidationError as exc:
        raise ValueError(f"포트폴리오 설정값 검증에 실패했습니다: {exc}") from exc


__all__ = ["PortfolioGenerationError", "PortfolioGenerator"]
