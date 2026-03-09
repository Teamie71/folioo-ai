"""첨삭 설정 로더"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class CorrectionLLMConfig(BaseModel):
    """첨삭 LLM 설정"""

    model: str = "openai/gpt-oss-120b"
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    timeout: float = Field(default=300.0, gt=0.0)


class CorrectionValidationConfig(BaseModel):
    """첨삭 출력 검증 설정"""

    min_lines_per_field: int = Field(default=1, ge=1)
    max_retries: int = Field(default=2, ge=0)
    allow_null_comment_for_keep: bool = True


class CorrectionRAGConfig(BaseModel):
    """첨삭 RAG 설정"""

    keyword_count: int = Field(default=4, ge=1)
    max_results_per_keyword: int = Field(default=5, ge=1, le=20)


class CorrectionConfig(BaseModel):
    """첨삭 설정 스키마"""

    llm: CorrectionLLMConfig = Field(default_factory=CorrectionLLMConfig)
    validation: CorrectionValidationConfig = Field(default_factory=CorrectionValidationConfig)
    rag: CorrectionRAGConfig = Field(default_factory=CorrectionRAGConfig)


@lru_cache(maxsize=1)
def load_correction_config() -> CorrectionConfig:
    """첨삭 설정 YAML 로드 (캐싱)"""
    config_path = Path(__file__).parent / "correction.yaml"

    try:
        with config_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"첨삭 설정 파일을 찾을 수 없습니다: {config_path}") from exc

    try:
        return CorrectionConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"첨삭 설정 파일 형식이 올바르지 않습니다: {exc}") from exc


def get_correction_llm_config() -> CorrectionLLMConfig:
    """첨삭 LLM 설정 반환"""
    return load_correction_config().llm


def get_correction_validation_config() -> CorrectionValidationConfig:
    """첨삭 출력 검증 설정 반환"""
    return load_correction_config().validation


def get_correction_rag_config() -> CorrectionRAGConfig:
    """첨삭 RAG 설정 반환"""
    return load_correction_config().rag


__all__ = [
    "CorrectionConfig",
    "CorrectionLLMConfig",
    "CorrectionRAGConfig",
    "CorrectionValidationConfig",
    "get_correction_llm_config",
    "get_correction_rag_config",
    "get_correction_validation_config",
    "load_correction_config",
]
