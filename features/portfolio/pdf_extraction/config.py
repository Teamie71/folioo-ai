"""PDF 추출 설정 로더"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class PdfExtractionLimitsConfig(BaseModel):
    """PDF 추출 상한 설정

    글자수 상한은 카테고리(텍스트 에어리어) 전체 합계 기준이다.
    """

    max_activity_count: int = Field(default=4, ge=1)
    detail_max_length: int = Field(default=300, ge=1)
    responsibility_max_length: int = Field(default=700, ge=1)
    problem_solving_max_length: int = Field(default=700, ge=1)
    learning_max_length: int = Field(default=300, ge=1)


class PdfExtractionConfig(BaseModel):
    """PDF 추출 설정 스키마"""

    limits: PdfExtractionLimitsConfig = Field(default_factory=PdfExtractionLimitsConfig)


@lru_cache(maxsize=1)
def load_pdf_extraction_config() -> PdfExtractionConfig:
    """PDF 추출 설정 YAML 로드 (캐싱)"""
    config_path = Path(__file__).parent / "pdf_extraction.yaml"

    try:
        with config_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"PDF 추출 설정 파일을 찾을 수 없습니다: {config_path}") from exc

    try:
        return PdfExtractionConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"PDF 추출 설정 파일 형식이 올바르지 않습니다: {exc}") from exc


def get_pdf_extraction_limits() -> PdfExtractionLimitsConfig:
    """PDF 추출 상한 설정 반환"""
    return load_pdf_extraction_config().limits


__all__ = [
    "PdfExtractionConfig",
    "PdfExtractionLimitsConfig",
    "get_pdf_extraction_limits",
    "load_pdf_extraction_config",
]
