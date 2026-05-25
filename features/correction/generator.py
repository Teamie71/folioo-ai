"""첨삭 결과 생성기"""

import logging
import re

from common.llm.client import get_llm

from .config import get_correction_llm_config, get_correction_validation_config
from .prompts.correction_prompt import correction_generator_prompt, format_portfolio_for_correction
from .prompts.generator import overall_summary_prompt
from .schemas import REQUIRED_CORRECTION_FIELDS, PortfolioCorrectionResult, SingleCorrectionOutput

_generator: "CorrectionGenerator | None" = None
_ALLOWED_TYPES = {"reduce", "keep", "emphasize"}
_FIELD_HEADER_PATTERN = re.compile(
    r"^\[[^\]]+ - (?P<field_name>description|contributions|achievements|insights)\]$"
)
_NUMBERED_LINE_PATTERN = re.compile(r"^\d+\.\s+")
_CODE_FENCE_PATTERN = re.compile(
    r"^\s*```(?:[a-zA-Z0-9_-]+)?\s*\n(?P<body>.*?)\n?\s*```\s*$",
    re.DOTALL,
)
logger = logging.getLogger(__name__)


class CorrectionGenerationError(Exception):
    """첨삭 생성 실패 예외"""


class CorrectionGenerator:
    """LLM 기반 첨삭 결과 생성기"""

    def __init__(self, max_retries: int | None = None) -> None:
        validation_config = get_correction_validation_config()
        llm_config = get_correction_llm_config()

        configured_retries = validation_config.max_retries
        self._max_retries = max_retries if max_retries is not None else configured_retries
        self._min_lines_per_field = validation_config.min_lines_per_field
        self._allow_null_comment_for_keep = validation_config.allow_null_comment_for_keep

        self._llm = get_llm(
            model=llm_config.model,
            temperature=llm_config.temperature,
            timeout=llm_config.timeout,
        )

    def generate(
        self,
        company_name: str,
        job_title: str,
        job_description: str,
        company_insight: str,
        portfolio_data: dict,
        emphasis_points: str,
    ) -> SingleCorrectionOutput:
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
            SingleCorrectionOutput: 검증을 통과한 첨삭 결과 또는 재시도 소진 시 마지막 결과

        Raises:
            CorrectionGenerationError: LLM 호출/파싱이 연속 실패해 결과를 만들지 못한 경우
        """
        validation_feedback = "없음"
        last_output: SingleCorrectionOutput | None = None
        last_error_message: str | None = None
        portfolio_data_text = _format_portfolio_data_for_prompt(portfolio_data)
        field_line_counts = _get_field_line_counts_from_formatted_text(portfolio_data_text)
        total_attempts = self._max_retries + 1

        logger.debug(
            "첨삭 프롬프트 변수 요약 (company: %s, job_title: %s, portfolio_data_length: %s, "
            "company_insight_length: %s, emphasis_points_length: %s)",
            company_name,
            job_title,
            len(portfolio_data_text),
            len(company_insight),
            len(emphasis_points),
        )

        for attempt in range(1, total_attempts + 1):
            prompt_variables = {
                "company_name": company_name,
                "job_title": job_title,
                "job_description": job_description,
                "company_insight": company_insight,
                "portfolio_data_text": portfolio_data_text,
                "emphasis_points": emphasis_points,
                "validation_feedback": validation_feedback,
            }

            try:
                logger.info("LLM 첨삭 생성 시도 %s/%s 시작", attempt, total_attempts)
                chain = correction_generator_prompt | self._llm
                raw_response = chain.invoke(prompt_variables)
                response_text = _coerce_llm_text_response(raw_response)
                output: SingleCorrectionOutput = _parse_single_correction_output(response_text)
                logger.info("LLM 첨삭 생성 시도 %s/%s 완료", attempt, total_attempts)
            except Exception as exc:
                last_error_message = f"LLM 호출/파싱 실패: {exc}"
                validation_feedback = last_error_message
                logger.warning(
                    "LLM 첨삭 생성 시도 %s/%s 실패: %s",
                    attempt,
                    total_attempts,
                    exc,
                )
                continue

            last_output = output
            validation_errors = self._validate(output=output, field_line_counts=field_line_counts)
            if not validation_errors:
                logger.info("검증 통과 (시도 %s/%s)", attempt, total_attempts)
                return output

            last_error_message = "; ".join(validation_errors)
            validation_feedback = f"이전 출력 보완 필요: {last_error_message}"
            logger.warning(
                "검증 실패 (시도 %s/%s): %s",
                attempt,
                total_attempts,
                last_error_message,
            )

        if last_output is not None:
            logger.warning(
                "재시도 소진 후 마지막 출력 반환 (%s회 시도): %s",
                total_attempts,
                last_error_message or "검증 실패",
            )
            return last_output

        raise CorrectionGenerationError(
            "첨삭 생성에 실패했습니다. "
            f"최대 시도({self._max_retries + 1}회) 후 중단: {last_error_message or '알 수 없는 오류'}"
        )

    def generate_overall_summary(
        self,
        company_name: str,
        job_title: str,
        job_description: str,
        company_insight: str,
        portfolio_corrections: list[PortfolioCorrectionResult],
        emphasis_points: str,
    ) -> str:
        """모든 포트폴리오 첨삭 결과를 종합한 총평을 생성한다."""
        logger.info("총평 생성 시작")
        chain = overall_summary_prompt | self._llm
        response = chain.invoke(
            {
                "company_name": company_name,
                "job_title": job_title,
                "job_description": job_description,
                "company_insight": company_insight,
                "emphasis_points": emphasis_points,
                "portfolio_corrections_text": _format_portfolio_corrections_for_summary(
                    portfolio_corrections
                ),
            }
        )
        overall_summary = _coerce_llm_text_response(response).strip()
        if not overall_summary:
            raise CorrectionGenerationError("총평 생성 결과가 비어 있습니다.")
        logger.info("총평 생성 완료 (길이: %s자)", len(overall_summary))
        return overall_summary

    def _validate(
        self,
        output: SingleCorrectionOutput,
        portfolio_data: dict | None = None,
        field_line_counts: dict[str, int] | None = None,
    ) -> list[str]:
        """
        첨삭 출력이 비즈니스 규칙을 만족하는지 검증

        Args:
            output: LLM이 생성한 첨삭 결과
            portfolio_data: 원본 포트폴리오 데이터 (line count를 내부 계산할 때 사용)
            field_line_counts: 이미 계산된 필드별 번호 라인 수 (선택)

        Returns:
            list[str]: 검증 실패 사유 목록 (빈 리스트면 검증 통과)
        """
        errors: list[str] = []
        if field_line_counts is None:
            if portfolio_data is None:
                raise ValueError(
                    "portfolio_data 또는 field_line_counts 중 하나는 반드시 필요합니다."
                )
            field_line_counts = _get_field_line_counts(portfolio_data)

        field_map = {field.field_name: field for field in output.fields}
        missing_fields = [name for name in REQUIRED_CORRECTION_FIELDS if name not in field_map]
        if missing_fields:
            errors.append(f"필수 필드 누락: {', '.join(missing_fields)}")

        for field_name in REQUIRED_CORRECTION_FIELDS:
            field = field_map.get(field_name)
            if field is None:
                continue

            if len(field.lines) < self._min_lines_per_field:
                errors.append(
                    f"{field_name} 필드는 최소 {self._min_lines_per_field}개 라인이 필요합니다."
                )

            line_count = field_line_counts.get(field_name, 0)
            for line in field.lines:
                if line.type not in _ALLOWED_TYPES:
                    errors.append(f"{field_name}의 type 값이 유효하지 않습니다: {line.type}")

                if line.type == "keep":
                    if line.comment is None and not self._allow_null_comment_for_keep:
                        errors.append(
                            f"{field_name}의 {line.line_number}번 라인 comment가 비어 있습니다."
                        )
                    elif line.comment is not None and not line.comment.strip():
                        errors.append(
                            f"{field_name}의 {line.line_number}번 라인 comment가 비어 있습니다."
                        )
                else:
                    if line.comment is None or not line.comment.strip():
                        errors.append(
                            f"{field_name}의 {line.line_number}번 라인 comment가 비어 있습니다."
                        )

                if line.line_number < 1 or line.line_number > line_count:
                    errors.append(
                        f"{field_name}의 line_number {line.line_number}가 원본 라인 수({line_count})를 벗어났습니다."
                    )

        return errors


def _strip_json_code_fence(text: str) -> str:
    """LLM 응답을 감싼 마크다운 코드펜스(```json ... ```)를 제거한다.

    OpenRouter를 통한 일부 모델(예: ``openai/gpt-oss-120b``)이 function/tool calling
    대신 메시지 본문에 펜스로 감싼 JSON을 반환하는 사례를 흡수하기 위한 정규화 헬퍼다.

    Args:
        text: LLM이 반환한 원본 텍스트

    Returns:
        str: 펜스가 제거되고 양끝 공백이 정리된 문자열. 펜스가 없으면 원본을 strip한 값.
    """
    stripped = text.strip()
    match = _CODE_FENCE_PATTERN.match(stripped)
    if match is not None:
        return match.group("body").strip()
    return stripped


def _parse_single_correction_output(text: str) -> SingleCorrectionOutput:
    """LLM 텍스트 응답을 ``SingleCorrectionOutput``으로 파싱한다.

    Args:
        text: LLM이 반환한 JSON 문자열 (마크다운 코드펜스 포함 가능)

    Returns:
        SingleCorrectionOutput: 스키마 검증을 통과한 첨삭 결과

    Raises:
        ValueError: 응답이 비어 있는 경우
        pydantic.ValidationError: JSON 파싱은 됐지만 스키마 검증에 실패한 경우
    """
    cleaned = _strip_json_code_fence(text)
    if not cleaned:
        raise ValueError("LLM 응답이 비어 있습니다.")
    return SingleCorrectionOutput.model_validate_json(cleaned)


def _get_field_line_counts(portfolio_data: dict) -> dict[str, int]:
    """필드별 번호 매김 대상 라인 수를 계산"""
    formatted_text = _format_portfolio_data_for_prompt(portfolio_data)
    return _get_field_line_counts_from_formatted_text(formatted_text)


def _get_field_line_counts_from_formatted_text(formatted_text: str) -> dict[str, int]:
    """포맷팅된 텍스트에서 필드별 번호 라인 수를 추출"""
    line_counts = {field_name: 0 for field_name in REQUIRED_CORRECTION_FIELDS}
    current_field_name: str | None = None

    for raw_line in formatted_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header_match = _FIELD_HEADER_PATTERN.match(line)
        if header_match is not None:
            current_field_name = header_match.group("field_name")
            continue

        if current_field_name is None:
            continue

        if _NUMBERED_LINE_PATTERN.match(line) is not None:
            line_counts[current_field_name] += 1

    return line_counts


def _coerce_field_value_to_text(value: object) -> str:
    """
    포트폴리오 필드 값을 문자열로 정규화

    Args:
        value: 문자열 또는 dict(lines/text/content/value) 또는 리스트 값

    Returns:
        str: 줄바꿈을 유지한 문자열 텍스트
    """
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item is not None)

    if isinstance(value, dict):
        if isinstance(value.get("lines"), list):
            return "\n".join(str(line) for line in value["lines"])

        for key in ("text", "content", "value"):
            candidate = value.get(key)
            if candidate is not None:
                return str(candidate)

        return ""

    if value is None:
        return ""

    return str(value)


def _format_portfolio_data_for_prompt(portfolio_data: dict) -> str:
    """
    포트폴리오 입력을 첨삭 프롬프트용 텍스트로 변환

    Args:
        portfolio_data: 필드별 문자열 또는 dict(lines/text/content/value) 형태 데이터

    Returns:
        str: format_portfolio_for_correction() 규칙을 따른 멀티라인 문자열
    """
    if not isinstance(portfolio_data, dict):
        raise TypeError("portfolio_data는 dict 타입이어야 합니다.")

    normalized_portfolio: dict[str, str] = {}
    for field_name in REQUIRED_CORRECTION_FIELDS:
        normalized_portfolio[field_name] = _coerce_field_value_to_text(
            portfolio_data.get(field_name)
        )

    return format_portfolio_for_correction(normalized_portfolio)


def _format_portfolio_corrections_for_summary(
    portfolio_corrections: list[PortfolioCorrectionResult],
) -> str:
    """총평 생성용 포트폴리오 첨삭 요약 텍스트를 생성한다."""
    sections: list[str] = []

    for correction in portfolio_corrections:
        sections.append(f"[포트폴리오 ID: {correction.portfolio_id}]")
        for field in correction.fields:
            sections.append(f"- {field.field_name}")
            for line in field.lines:
                comment = line.comment if line.comment is not None else "없음"
                sections.append(
                    f"  - {line.line_number}번 | {line.type} | 원문: {line.original_text} | 코멘트: {comment}"
                )
        sections.append("")

    return "\n".join(sections).strip()


def _coerce_llm_text_response(response: object) -> str:
    """LangChain 응답 객체를 문자열로 정규화한다."""
    if isinstance(response, str):
        return response

    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(parts)

    return str(content)


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
