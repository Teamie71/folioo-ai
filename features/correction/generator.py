"""첨삭 결과 생성기"""

from pathlib import Path

import yaml

from common.llm.client import get_llm

from .prompts.generator import correction_generator_prompt
from .schemas import REQUIRED_CORRECTION_FIELDS, CorrectionOutput

_generator: "CorrectionGenerator | None" = None
_DEFAULT_MAX_RETRIES = 2
_ALLOWED_TYPES = {"reduce", "keep", "emphasize"}


class CorrectionGenerationError(Exception):
    """첨삭 생성 실패 예외"""


class CorrectionGenerator:
    """LLM 기반 첨삭 결과 생성기"""

    def __init__(self, max_retries: int | None = None) -> None:
        configured_retries = _load_max_retries()
        self._max_retries = max_retries if max_retries is not None else configured_retries
        llm = get_llm(temperature=0.2)
        self._structured_llm = llm.with_structured_output(CorrectionOutput)

    def generate(
        self,
        company_name: str,
        job_title: str,
        job_description: str,
        company_insight: str,
        portfolio_data: dict,
        emphasis_points: str,
    ) -> CorrectionOutput:
        """첨삭 결과 생성 및 검증"""
        validation_feedback = "없음"
        last_output: CorrectionOutput | None = None
        last_error_message: str | None = None

        for _ in range(self._max_retries + 1):
            prompt_variables = {
                "company_name": company_name,
                "job_title": job_title,
                "job_description": job_description,
                "company_insight": company_insight,
                "portfolio_data_text": _format_portfolio_data_for_prompt(portfolio_data),
                "emphasis_points": emphasis_points,
                "validation_feedback": validation_feedback,
            }

            try:
                chain = correction_generator_prompt | self._structured_llm
                output: CorrectionOutput = chain.invoke(prompt_variables)
            except Exception as exc:
                last_error_message = f"LLM 호출/파싱 실패: {exc}"
                validation_feedback = last_error_message
                continue

            last_output = output
            validation_errors = self._validate(output=output, portfolio_data=portfolio_data)
            if not validation_errors:
                return output

            last_error_message = "; ".join(validation_errors)
            validation_feedback = f"이전 출력 보완 필요: {last_error_message}"

        if last_output is not None:
            return last_output

        raise CorrectionGenerationError(
            "첨삭 생성에 실패했습니다. "
            f"최대 시도({self._max_retries + 1}회) 후 중단: {last_error_message or '알 수 없는 오류'}"
        )

    def _validate(self, output: CorrectionOutput, portfolio_data: dict) -> list[str]:
        """출력 검증"""
        errors: list[str] = []

        field_map = {field.field_name: field for field in output.fields}
        missing_fields = [name for name in REQUIRED_CORRECTION_FIELDS if name not in field_map]
        if missing_fields:
            errors.append(f"필수 필드 누락: {', '.join(missing_fields)}")

        if not output.overall_summary.strip():
            errors.append("overall_summary가 비어 있습니다.")

        for field_name in REQUIRED_CORRECTION_FIELDS:
            field = field_map.get(field_name)
            if field is None:
                continue

            line_count = _get_line_count(portfolio_data, field_name)
            for line in field.lines:
                if line.type not in _ALLOWED_TYPES:
                    errors.append(f"{field_name}의 type 값이 유효하지 않습니다: {line.type}")
                if not line.comment.strip():
                    errors.append(f"{field_name}의 {line.line_number}번 라인 comment가 비어 있습니다.")
                if line.line_number < 1 or line.line_number > line_count:
                    errors.append(
                        f"{field_name}의 line_number {line.line_number}가 원본 라인 수({line_count})를 벗어났습니다."
                    )

        return errors


def _load_max_retries() -> int:
    config_path = Path(__file__).resolve().parent / "config" / "generator.yaml"

    try:
        with config_path.open(encoding="utf-8") as file:
            config = yaml.safe_load(file) or {}
        value = config.get("generator", {}).get("max_retries", _DEFAULT_MAX_RETRIES)
        return int(value)
    except Exception:
        return _DEFAULT_MAX_RETRIES


def _get_line_count(portfolio_data: dict, field_name: str) -> int:
    source = portfolio_data.get(field_name)
    if isinstance(source, str):
        return max(len(source.splitlines()), 1)

    if isinstance(source, dict):
        if isinstance(source.get("lines"), list):
            return max(len(source["lines"]), 1)

        for key in ("text", "content", "value"):
            value = source.get(key)
            if isinstance(value, str):
                return max(len(value.splitlines()), 1)

    return 1


def _format_portfolio_data_for_prompt(portfolio_data: dict) -> str:
    lines: list[str] = []

    for field_name in REQUIRED_CORRECTION_FIELDS:
        value = portfolio_data.get(field_name, "")

        if isinstance(value, dict):
            if isinstance(value.get("lines"), list):
                text = "\n".join(str(line) for line in value["lines"])
            else:
                text = str(value.get("text") or value.get("content") or value.get("value") or "")
        else:
            text = str(value)

        lines.append(f"[{field_name}]")
        lines.append(text.strip() or "(내용 없음)")
        lines.append("")

    return "\n".join(lines).strip()


def get_correction_generator() -> CorrectionGenerator:
    """CorrectionGenerator 싱글톤 반환"""
    global _generator

    if _generator is None:
        _generator = CorrectionGenerator()

    return _generator


def reset_correction_generator() -> None:
    """CorrectionGenerator 싱글톤 초기화 (테스트용)"""
    global _generator
    _generator = None


__all__ = [
    "CorrectionGenerationError",
    "CorrectionGenerator",
    "get_correction_generator",
    "reset_correction_generator",
]
