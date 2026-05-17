"""단계별 설정 로더"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


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


class AdditionalQuestionTarget(BaseModel):
    """추가 대화에서 보완 질문을 생성할 target 설정"""

    target: str
    stage: int
    field_name: str
    label: str
    question_hint: str
    field_description: str

    @field_validator("target", "field_name", "label", "question_hint", "field_description")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        """target 설정의 문자열 필드는 비어 있을 수 없다."""
        if not value.strip():
            raise ValueError("추가 질문 target 문자열 필드는 비어 있을 수 없습니다.")
        return value


class AdditionalQuestionPriorityGroup(BaseModel):
    """우선순위별 추가 질문 target 묶음"""

    priority: int
    targets: list[AdditionalQuestionTarget]

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: int) -> int:
        """추가 질문 우선순위는 1 이상이어야 한다."""
        if value < 1:
            raise ValueError("additional_question_priorities.priority는 1 이상이어야 합니다.")
        return value

    @field_validator("targets")
    @classmethod
    def validate_targets(cls, value: list[AdditionalQuestionTarget]) -> list[AdditionalQuestionTarget]:
        """우선순위 그룹에는 target이 1개 이상 있어야 한다."""
        if not value:
            raise ValueError("additional_question_priorities.targets는 1개 이상이어야 합니다.")
        return value


class GlobalConfig(BaseModel):
    """전역 설정 스키마"""

    max_retries_per_question: int
    enable_dynamic_followup: bool
    context_window_size: int
    extension_turns_per_session: int
    max_extensions: int
    additional_question_priorities: list[AdditionalQuestionPriorityGroup] = Field(default_factory=list)

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

    @model_validator(mode="after")
    def validate_additional_question_priorities(self) -> Self:
        """추가 질문 target이 실제 stage/field 설정과 일치하는지 검증한다."""
        priorities_seen: set[int] = set()
        targets_seen: set[str] = set()
        sorted_groups = sorted(
            self.global_config.additional_question_priorities,
            key=lambda group: group.priority,
        )

        for group in sorted_groups:
            if group.priority in priorities_seen:
                raise ValueError("additional_question_priorities.priority는 중복될 수 없습니다.")
            priorities_seen.add(group.priority)

            for target in group.targets:
                if target.target in targets_seen:
                    raise ValueError("additional_question_priorities.target은 중복될 수 없습니다.")
                targets_seen.add(target.target)

                stage_config = self.stages.get(target.stage)
                if stage_config is None:
                    raise ValueError(
                        f"additional_question_priorities.stage가 존재하지 않습니다: {target.stage}"
                    )

                if target.field_name not in stage_config.required_fields:
                    raise ValueError(
                        "additional_question_priorities.field_name이 해당 stage에 존재하지 않습니다: "
                        f"stage={target.stage}, field_name={target.field_name}"
                    )

        self.global_config.additional_question_priorities = sorted_groups
        return self


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
