"""경험 맵 LLM 컨텍스트 변환 테스트"""

import pytest

from features.experience_map.map_context import MapBlockRow, build_map_snapshot


def row(
    block_id: str,
    parent_id: str | None,
    level: int,
    position: int,
    content: str | None,
    placeholder: str | None = None,
) -> MapBlockRow:
    """테스트 블록 행 생성"""
    return MapBlockRow(
        block_id=block_id,
        parent_id=parent_id,
        level=level,
        kind="CONTENT",
        position=position,
        content=content,
        placeholder=placeholder,
        is_text_editable=True,
        is_deletable=False,
    )


@pytest.fixture
def snapshot():
    """그룹 1개와 활동 2개를 가진 정렬되지 않은 flat 목록"""
    return build_map_snapshot(
        [
            row("30", "20", 3, 2, "문제해결"),
            row("11", "1", 2, 2, "두 번째 활동"),
            row("1", None, 1, 1, "프로젝트"),
            row("40", "20", 3, 1, "상세정보"),
            row("20", "1", 2, 1, "첫 번째 활동"),
            row("41", "40", 4, 1, None, "어떤 계기로 시작했나요?"),
            row("31", "30", 4, 1, "결제 오류를 해결했다"),
        ],
        map_version=7,
    )


def test_outline_uses_deterministic_activity_aliases(snapshot):
    """활동은 position 순으로 안정적인 exp alias를 받는다."""
    assert snapshot.map_version == 7
    assert snapshot.outline() == [
        {
            "level": 1,
            "title": "프로젝트",
            "children": [
                {"alias": "exp_1", "level": 2, "title": "첫 번째 활동"},
                {"alias": "exp_2", "level": 2, "title": "두 번째 활동"},
            ],
        }
    ]


def test_activity_context_only_exposes_selected_activity(snapshot):
    """다른 활동의 실제 ID는 선택 활동의 alias map에 절대 들어가지 않는다."""
    context = snapshot.get_activity_context("exp_1")

    assert context is not None
    assert context.resolve_alias("exp_1") == "20"
    assert set(context.alias_to_block_id.values()) == {"20", "30", "31", "40", "41"}
    assert "11" not in context.alias_to_block_id.values()
    other_context = snapshot.get_activity_context("exp_2")
    assert other_context is not None
    assert other_context.resolve_alias("exp_2") == "11"
    assert other_context.resolve_alias("exp_1") is None


def test_activity_tree_renders_content_and_placeholder_separately(snapshot):
    """빈 블록 가이드가 사용자 작성 내용처럼 렌더링되지 않는다."""
    context = snapshot.get_activity_context("exp_1")

    assert context is not None
    assert context.tree_text == (
        "[exp_1] 첫 번째 활동\n"
        "  [b_1] 상세정보\n"
        "    [b_2] (빈 블록 — 가이드: 어떤 계기로 시작했나요?)\n"
        "  [b_3] 문제해결\n"
        "    [b_4] 결제 오류를 해결했다"
    )


def test_unknown_alias_is_not_resolved(snapshot):
    """LLM이 지어낸 alias는 역변환되지 않는다."""
    context = snapshot.get_activity_context("exp_1")

    assert context is not None
    assert context.resolve_alias("b_999") is None
    assert snapshot.get_activity_context("exp_999") is None


@pytest.mark.parametrize(
    "rows",
    [
        [row("1", None, 2, 1, "잘못된 루트")],
        [row("1", "404", 2, 1, "고아")],
        [row("1", None, 1, 1, "부모"), row("2", "1", 3, 1, "level 건너뜀")],
    ],
)
def test_invalid_tree_is_rejected(rows):
    """손상된 트리를 그럴듯한 컨텍스트로 보정하지 않는다."""
    with pytest.raises(ValueError):
        build_map_snapshot(rows, map_version=1)
