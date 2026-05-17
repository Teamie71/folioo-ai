"""설정 로더 테스트"""

import copy
from pathlib import Path

import pytest
import yaml

from features.interview.config.loader import (
    StageConfig,
    get_all_stages,
    get_global_config,
    load_stage_config,
)


def _load_raw_stages_yaml() -> dict:
    """실제 stages.yaml 원본을 로드한다."""
    from features.interview.config import loader

    yaml_path = Path(loader.__file__).parent / "stages.yaml"
    with open(yaml_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_load_stage_config_stage_1():
    """1단계 설정 로드 테스트"""
    config = load_stage_config(1)

    assert isinstance(config, StageConfig)
    assert config.name == "프로젝트 개요 및 구조화"
    assert config.description
    assert len(config.fixed_questions) == 3
    assert "project_background" in config.required_fields
    assert "problem_definition" in config.required_fields
    assert "project_duration" in config.required_fields
    assert config.max_generated_questions == 0


def test_load_all_stages():
    """전체 단계 로드 테스트"""
    stages = get_all_stages()

    assert len(stages) == 4
    assert all(stage in stages for stage in [1, 2, 3, 4])

    # 각 단계가 올바른 타입인지 확인
    for stage_num, stage_config in stages.items():
        assert isinstance(stage_num, int)
        assert hasattr(stage_config, "name")
        assert hasattr(stage_config, "description")
        assert hasattr(stage_config, "fixed_questions")


def test_invalid_stage_raises_error():
    """잘못된 단계 번호 에러 테스트"""
    with pytest.raises(ValueError, match="Invalid stage"):
        load_stage_config(5)

    with pytest.raises(ValueError, match="Invalid stage"):
        load_stage_config(0)


@pytest.mark.parametrize("stage", [1, 2, 3, 4])
def test_all_stages_have_required_fields(stage):
    """모든 단계에 필수 필드가 있는지 테스트"""
    config = load_stage_config(stage)

    assert config.name
    assert config.description
    assert len(config.fixed_questions) >= 1
    assert len(config.required_fields) >= 1
    assert config.max_generated_questions >= 0
    assert isinstance(config.force_all_generated_questions, bool)


def test_regular_flow_fixed_question_counts():
    """정규 플로우는 단계별 고정 질문 3, 2, 3, 3개로 구성된다."""
    stages = get_all_stages()
    expected_counts = {1: 3, 2: 2, 3: 3, 4: 3}

    assert {stage: len(config.fixed_questions) for stage, config in stages.items()} == expected_counts
    assert sum(len(config.fixed_questions) for config in stages.values()) == 11


@pytest.mark.parametrize("stage", [1, 2, 3, 4])
def test_regular_flow_disables_generated_questions(stage):
    """정규 단계에서는 생성 질문을 사용하지 않는다."""
    config = load_stage_config(stage)

    assert config.max_generated_questions == 0


def test_get_global_config():
    """global_config 로드 테스트"""
    global_config = get_global_config()

    assert global_config.max_retries_per_question >= 0
    assert isinstance(global_config.enable_dynamic_followup, bool)
    assert global_config.context_window_size >= 1
    assert global_config.extension_turns_per_session == 18
    assert global_config.max_extensions == 1


def test_additional_question_priorities_load_in_order():
    """추가 질문 target 우선순위를 설정 순서대로 로드한다."""
    global_config = get_global_config()
    priority_groups = global_config.additional_question_priorities
    targets = [target for group in priority_groups for target in group.targets]

    assert [group.priority for group in priority_groups] == [1, 2, 3, 4]
    assert sum(len(group.targets) for group in priority_groups) == 18
    assert targets[0].target == "stage_3_episode_1_strategy_rationale"
    assert targets[-1].target == "stage_4_final_deliverable"
    assert all(target.label for target in targets)
    assert all(target.question_hint for target in targets)
    assert all(target.field_description for target in targets)


def test_additional_question_target_text_is_trimmed(monkeypatch: pytest.MonkeyPatch):
    """추가 질문 target 문자열은 앞뒤 공백을 제거해 로드한다."""
    from features.interview.config import loader

    loader._load_stages_yaml.cache_clear()
    data = copy.deepcopy(_load_raw_stages_yaml())
    target = data["global_config"]["additional_question_priorities"][0]["targets"][0]
    target["target"] = "  stage_3_episode_1_strategy_rationale  "
    target["label"] = "  Episode 1 해결 전략과 선택 근거  "
    monkeypatch.setattr(loader.yaml, "safe_load", lambda _: data)

    config = loader._load_stages_yaml()
    loaded_target = config.global_config.additional_question_priorities[0].targets[0]

    assert loaded_target.target == "stage_3_episode_1_strategy_rationale"
    assert loaded_target.label == "Episode 1 해결 전략과 선택 근거"

    loader._load_stages_yaml.cache_clear()


def test_missing_additional_question_priorities_raises_error(monkeypatch: pytest.MonkeyPatch):
    """추가 질문 target 우선순위 설정은 필수다."""
    from features.interview.config import loader

    loader._load_stages_yaml.cache_clear()
    data = copy.deepcopy(_load_raw_stages_yaml())
    data["global_config"].pop("additional_question_priorities")
    monkeypatch.setattr(loader.yaml, "safe_load", lambda _: data)

    with pytest.raises(ValueError, match="additional_question_priorities"):
        loader._load_stages_yaml()

    loader._load_stages_yaml.cache_clear()


def test_invalid_yaml_type_raises_korean_error(monkeypatch: pytest.MonkeyPatch):
    """YAML 루트 타입이 잘못되면 한국어 예외 메시지를 반환한다."""
    from features.interview.config import loader

    loader._load_stages_yaml.cache_clear()
    monkeypatch.setattr(loader.yaml, "safe_load", lambda _: [])

    with pytest.raises(ValueError, match="인터뷰 설정 파일 형식이 올바르지 않습니다"):
        loader._load_stages_yaml()

    loader._load_stages_yaml.cache_clear()


def test_negative_max_generated_questions_raises_error(monkeypatch: pytest.MonkeyPatch):
    """단계별 생성 질문 수는 0 이상이어야 한다."""
    from features.interview.config import loader

    loader._load_stages_yaml.cache_clear()
    data = copy.deepcopy(_load_raw_stages_yaml())
    data["stages"][1]["max_generated_questions"] = -1
    monkeypatch.setattr(loader.yaml, "safe_load", lambda _: data)

    with pytest.raises(ValueError, match="max_generated_questions는 0 이상이어야 합니다"):
        loader._load_stages_yaml()

    loader._load_stages_yaml.cache_clear()


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "error_message"),
    [
        ("extension_turns_per_session", 0, "extension_turns_per_session은 1 이상이어야 합니다"),
        ("extension_turns_per_session", -1, "extension_turns_per_session은 1 이상이어야 합니다"),
        ("max_extensions", 0, "max_extensions는 1 이상이어야 합니다"),
        ("max_extensions", -1, "max_extensions는 1 이상이어야 합니다"),
    ],
)
def test_invalid_extension_global_values_raise_error(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    invalid_value: int,
    error_message: str,
):
    """연장 전역 설정이 1 미만이면 명확한 예외를 반환한다."""
    from features.interview.config import loader

    loader._load_stages_yaml.cache_clear()
    data = copy.deepcopy(_load_raw_stages_yaml())
    data["global_config"][field_name] = invalid_value
    monkeypatch.setattr(loader.yaml, "safe_load", lambda _: data)

    with pytest.raises(ValueError, match=error_message):
        loader._load_stages_yaml()

    loader._load_stages_yaml.cache_clear()


@pytest.mark.parametrize(
    ("mutate", "error_message"),
    [
        (
            lambda data: data["global_config"]["additional_question_priorities"][0].__setitem__(
                "priority", 2
            ),
            "additional_question_priorities.priority는 중복될 수 없습니다",
        ),
        (
            lambda data: data["global_config"]["additional_question_priorities"][0][
                "targets"
            ][0].__setitem__("target", "stage_3_episode_2_strategy_rationale"),
            "additional_question_priorities.target은 중복될 수 없습니다",
        ),
        (
            lambda data: data["global_config"]["additional_question_priorities"][0][
                "targets"
            ][0].__setitem__("stage", 9),
            "additional_question_priorities.stage가 존재하지 않습니다",
        ),
        (
            lambda data: data["global_config"]["additional_question_priorities"][0][
                "targets"
            ][0].__setitem__("field_name", "unknown_field"),
            "additional_question_priorities.field_name이 해당 stage에 존재하지 않습니다",
        ),
        (
            lambda data: data["global_config"]["additional_question_priorities"][0].__setitem__(
                "targets", []
            ),
            "additional_question_priorities.targets는 1개 이상이어야 합니다",
        ),
        (
            lambda data: data["global_config"]["additional_question_priorities"][0][
                "targets"
            ][0].__setitem__("label", ""),
            "추가 질문 target 문자열 필드는 비어 있을 수 없습니다",
        ),
    ],
)
def test_invalid_additional_question_priorities_raise_error(
    monkeypatch: pytest.MonkeyPatch,
    mutate,
    error_message: str,
):
    """잘못된 추가 질문 target 설정은 명확한 예외를 반환한다."""
    from features.interview.config import loader

    loader._load_stages_yaml.cache_clear()
    data = copy.deepcopy(_load_raw_stages_yaml())
    mutate(data)
    monkeypatch.setattr(loader.yaml, "safe_load", lambda _: data)

    with pytest.raises(ValueError, match=error_message):
        loader._load_stages_yaml()

    loader._load_stages_yaml.cache_clear()


@pytest.mark.parametrize("missing_field", ["extension_turns_per_session", "max_extensions"])
def test_missing_extension_global_field_raises_error(
    monkeypatch: pytest.MonkeyPatch,
    missing_field: str,
):
    """연장 전역 설정 필드는 필수이며 누락 시 예외를 반환한다."""
    from features.interview.config import loader

    loader._load_stages_yaml.cache_clear()
    data = copy.deepcopy(_load_raw_stages_yaml())
    data["global_config"].pop(missing_field)
    monkeypatch.setattr(loader.yaml, "safe_load", lambda _: data)

    with pytest.raises(ValueError, match=missing_field):
        loader._load_stages_yaml()

    loader._load_stages_yaml.cache_clear()
