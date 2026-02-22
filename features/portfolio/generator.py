"""포트폴리오 생성기"""

import re
from pathlib import Path

import yaml

from common.llm.client import get_llm

from .prompts.portfolio_generator import (
    format_collected_data_for_prompt,
    portfolio_generator_prompt,
)
from .schemas import PortfolioOutput

_MAX_ATTEMPTS = 3
_SECTION_FIELDS = ("detail_info", "assigned_task", "problem_solving", "lessons_learned")
_BULLET_PATTERN = re.compile(r"(?m)^\s*[-*•]\s+")


class PortfolioGenerationError(Exception):
    """포트폴리오 생성 실패 예외"""


class PortfolioGenerator:
    """LLM 기반 포트폴리오 생성기"""

    def __init__(self) -> None:
        self._section_rules = _load_section_rules()

    def generate(self, collected_data: dict, experience_name: str) -> PortfolioOutput:
        """수집 데이터를 바탕으로 포트폴리오 텍스트 생성"""
        validation_feedback = "없음"
        last_failure_reason: str | None = None

        for _ in range(_MAX_ATTEMPTS):
            prompt_variables = {
                "experience_name": experience_name,
                "collected_data_text": format_collected_data_for_prompt(collected_data),
                "validation_feedback": validation_feedback,
            }

            try:
                llm = get_llm(temperature=0.7)
                structured_llm = llm.with_structured_output(PortfolioOutput)
                chain = portfolio_generator_prompt | structured_llm
                output: PortfolioOutput = chain.invoke(prompt_variables)
            except Exception as exc:
                last_failure_reason = f"LLM 호출/파싱 실패: {exc}"
                validation_feedback = last_failure_reason
                continue

            if self._validate_output(output):
                return output

            validation_errors = self._get_validation_errors(output)
            last_failure_reason = "; ".join(validation_errors)
            validation_feedback = f"이전 출력 보완 필요: {last_failure_reason}"

        raise PortfolioGenerationError(
            "포트폴리오 생성에 실패했습니다. "
            f"최대 시도({_MAX_ATTEMPTS}회) 후 중단: {last_failure_reason or '알 수 없는 오류'}"
        )

    def _validate_output(self, output: PortfolioOutput) -> bool:
        """생성 결과가 검증 규칙을 만족하는지 확인"""
        return len(self._get_validation_errors(output)) == 0

    def _get_validation_errors(self, output: PortfolioOutput) -> list[str]:
        errors: list[str] = []

        for field_name in _SECTION_FIELDS:
            text = getattr(output, field_name, "").strip()
            min_length = self._section_rules.get(field_name, {}).get("min_length", 0)

            if not text:
                errors.append(f"{field_name} 섹션이 비어 있습니다.")
                continue

            if len(text) < min_length:
                errors.append(
                    f"{field_name} 섹션 길이가 최소 길이({min_length})보다 짧습니다. (현재: {len(text)})"
                )

            bullet_count = len(_BULLET_PATTERN.findall(text))
            if bullet_count >= 2:
                errors.append(f"{field_name} 섹션이 불릿 포인트 중심 형식입니다.")

        return errors


def _load_section_rules() -> dict[str, dict]:
    config_path = Path(__file__).resolve().parent / "config" / "portfolio.yaml"
    with config_path.open(encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    return config.get("sections", {})


__all__ = ["PortfolioGenerationError", "PortfolioGenerator"]
