"""단계별 설정 로더"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError, field_validator


class StageConfig(BaseModel):
    """단계별 설정 스키마"""

    name: str
    description: str
    fixed_questions: list[str]
    required_fields: dict[str, dict[str, str]]
    max_generated_questions: int
    force_all_generated_questions: bool

    @field_validator("max_generated_questions")
    @classmethod
    def validate_max_generated_questions(cls, value: int) -> int:
        """단계별 생성 질문 수는 0 이상이어야 한다."""
        if value < 0:
            raise ValueError("max_generated_questions는 0 이상이어야 합니다.")
        return value


class GlobalConfig(BaseModel):
    """전역 설정 스키마"""

    max_retries_per_question: int
    enable_dynamic_followup: bool
    context_window_size: int
    extension_turns_per_session: int
    max_extensions: int

    @field_validator("extension_turns_per_session")
    @classmethod
    def validate_extension_turns_per_session(cls, value: int) -> int:
        """연장 1회당 질문 횟수는 1 이상이어야 한다."""
        if value < 1:
            raise ValueError("extension_turns_per_session은 1 이상이어야 합니다.")
        return value

    @field_validator("max_extensions")
    @classmethod
    def validate_max_extensions(cls, value: int) -> int:
        """최대 연장 횟수는 1 이상이어야 한다."""
        if value < 1:
            raise ValueError("max_extensions는 1 이상이어야 합니다.")
        return value


class StagesConfig(BaseModel):
    """전체 단계 설정 스키마"""

    stages: dict[int, StageConfig]
    global_config: GlobalConfig


@lru_cache(maxsize=1)
def _load_stages_yaml() -> StagesConfig:
    """YAML 파일 로드 (캐싱)"""
    yaml_path = Path(__file__).parent / "stages.yaml"

    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as exc:
        raise ValueError(f"인터뷰 설정 파일을 읽을 수 없습니다: {exc}") from exc

    if data is None:
        data = {}

    if not isinstance(data, dict):
        raise ValueError("인터뷰 설정 파일 형식이 올바르지 않습니다. (YAML 객체 필요)")

    try:
        return StagesConfig(**data)
    except ValidationError as exc:
        raise ValueError(f"인터뷰 설정값 검증에 실패했습니다: {exc}") from exc


def load_stage_config(stage: Literal[1, 2, 3, 4]) -> StageConfig:
    """특정 단계의 설정 로드"""
    config = _load_stages_yaml()

    if stage not in config.stages:
        raise ValueError(f"Invalid stage: {stage}. Must be 1-4.")

    return config.stages[stage]


def get_all_stages() -> dict[int, StageConfig]:
    """모든 단계의 설정 반환"""
    return _load_stages_yaml().stages


def get_global_config() -> GlobalConfig:
    """전역 설정 반환"""
    return _load_stages_yaml().global_config
