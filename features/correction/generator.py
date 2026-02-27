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
        """
        첨삭 결과 생성 및 검증

        Args:
            company_name: 회사명
            job_title: 지원 직무명
            job_description: 채용 공고 본문
            company_insight: 기업 분석 텍스트
            portfolio_data: 원본 포트폴리오 데이터
                (description/contributions/achievements/insights 키 기준,
                값은 문자열 또는 dict(lines/text/content/value) 지원)
            emphasis_points: 강조 포인트 텍스트

        Returns:
            CorrectionOutput: 검증을 통과한 첨삭 결과 또는 재시도 소진 시 마지막 결과

        Raises:
            CorrectionGenerationError: LLM 호출/파싱이 연속 실패해 결과를 만들지 못한 경우
        """
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
        """
        첨삭 출력이 비즈니스 규칙을 만족하는지 검증

        Args:
            output: LLM이 생성한 첨삭 결과
            portfolio_data: 원본 포트폴리오 데이터

        Returns:
            list[str]: 검증 실패 사유 목록 (빈 리스트면 검증 통과)
        """
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
    """
    필드별 원본 라인 수 계산 (최소 1라인 보장)

    Args:
        portfolio_data: 원본 포트폴리오 데이터
        field_name: 라인 수를 구할 필드명

        Returns:
        int: 문자열 또는 dict(lines/text/content/value) 기반 라인 수(항상 1 이상)
    """
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
    """
    포트폴리오 입력을 프롬프트용 섹션 텍스트로 변환

    Args:
        portfolio_data: 필드별 문자열 또는 dict(lines/text/content/value) 형태 데이터

    Returns:
        str: [field] 헤더와 본문을 포함한 멀티라인 문자열
            예) [description]\\n...\\n\\n[contributions]\\n...
    """
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
    """
    CorrectionGenerator 싱글톤 반환

    Returns:
        CorrectionGenerator: 프로세스 내에서 재사용되는 단일 생성기 인스턴스
            (LLM 클라이언트 초기화 비용 절감 목적)
    """
    global _generator

    if _generator is None:
        _generator = CorrectionGenerator()

    return _generator


def reset_correction_generator() -> None:
    """
    CorrectionGenerator 싱글톤 초기화 (테스트용)

    테스트 간 상태 격리를 위해 생성기 캐시를 비운다.
    """
    global _generator
    _generator = None


__all__ = [
    "CorrectionGenerationError",
    "CorrectionGenerator",
    "get_correction_generator",
    "reset_correction_generator",
]
