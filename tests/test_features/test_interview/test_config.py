"""설정 로더 테스트"""

import pytest

from features.interview.config.loader import (
    StageConfig,
    get_all_stages,
    load_stage_config,
)


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
    assert config.max_generated_questions == 2
    assert config.force_all_generated_questions is False


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
    assert config.max_generated_questions >= 1
    assert isinstance(config.force_all_generated_questions, bool)
