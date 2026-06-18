"""PPTX 텍스트 fit 휴리스틱 테스트."""

import pytest

from features.visualization.text_fit import (
    EMU_PER_PT,
    TextBoxConstraints,
    TextFitPreflightError,
    apply_text_fit_preflight,
    estimate_text_layout,
    evaluate_basic_text_area_fit,
    evaluate_inline_label_group_fit,
    measure_text_width,
)


def test_measure_text_width_is_deterministic_for_mixed_character_groups() -> None:
    """한글/영문/숫자/공백/기호 혼합 텍스트 폭을 고정 계수로 계산한다."""
    first = measure_text_width("한A1 !%", font_size_pt=10)
    second = measure_text_width("한A1 !%", font_size_pt=10)

    assert first == second
    assert first.width_pt == 34.3
    assert first.breakdown.hangul == 1
    assert first.breakdown.latin == 1
    assert first.breakdown.digit == 1
    assert first.breakdown.space == 1
    assert first.breakdown.symbol == 2


def test_measure_text_width_counts_uppercase_latin_as_wider_than_lowercase() -> None:
    """대문자는 소문자보다 넓은 고정 계수로 추정한다."""
    uppercase = measure_text_width("AAA", font_size_pt=10)
    lowercase = measure_text_width("aaa", font_size_pt=10)

    assert uppercase.width_pt == 19.5
    assert lowercase.width_pt == 16.5
    assert uppercase.width_pt > lowercase.width_pt


def test_text_fit_log_dict_caps_line_width_payloads() -> None:
    """structured log payload 의 line_widths_pt 배열은 최대 개수를 제한한다."""
    text = "\n".join("A" for _ in range(12))
    measurement = measure_text_width(text, font_size_pt=10)
    constraints = TextBoxConstraints(
        width_pt=80.0,
        height_pt=200.0,
        content_width_pt=80.0,
        content_height_pt=200.0,
        padding_left_pt=0.0,
        padding_right_pt=0.0,
        padding_top_pt=0.0,
        padding_bottom_pt=0.0,
        safety_margin_ratio=0.12,
        max_lines=20,
        nowrap=True,
    )
    layout = estimate_text_layout(text, font_size_pt=10, constraints=constraints)

    measurement_log = measurement.to_log_dict()
    layout_log = layout.to_log_dict()
    assert len(measurement_log["line_widths_pt"]) == 8
    assert measurement_log["line_widths_truncated"] is True
    assert measurement_log["line_count"] == 12
    assert len(layout_log["line_widths_pt"]) == 8
    assert layout_log["line_widths_truncated"] is True
    assert layout_log["line_count"] == 12


def test_nowrap_overflow_keeps_single_line_without_arbitrary_wrap() -> None:
    """nowrap 텍스트는 공백이 있어도 임의 줄바꿈하지 않고 폭 overflow 로 판정한다."""
    constraints = TextBoxConstraints(
        width_pt=50.0,
        height_pt=30.0,
        content_width_pt=50.0,
        content_height_pt=30.0,
        padding_left_pt=0.0,
        padding_right_pt=0.0,
        padding_top_pt=0.0,
        padding_bottom_pt=0.0,
        safety_margin_ratio=0.12,
        max_lines=1,
        nowrap=True,
    )

    layout = estimate_text_layout("OpenAI API", font_size_pt=12, constraints=constraints)

    assert layout.line_count == 1
    assert "nowrap_width_overflow" in layout.overflow_reasons


def test_max_lines_overflow_is_reported_after_wrapping() -> None:
    """자동 줄바꿈 결과가 max_lines 를 넘으면 overflow 로 판정한다."""
    constraints = TextBoxConstraints(
        width_pt=60.0,
        height_pt=80.0,
        content_width_pt=60.0,
        content_height_pt=80.0,
        padding_left_pt=0.0,
        padding_right_pt=0.0,
        padding_top_pt=0.0,
        padding_bottom_pt=0.0,
        safety_margin_ratio=0.12,
        max_lines=1,
        nowrap=False,
    )

    layout = estimate_text_layout("가나다라마바사", font_size_pt=12, constraints=constraints)

    assert layout.line_count == 2
    assert "max_lines_overflow" in layout.overflow_reasons


def test_basic_text_area_shrinks_only_until_min_font_pt() -> None:
    """basic_text_area 는 fit 가능한 경우에도 min_font_pt 아래로 줄이지 않는다."""
    result = evaluate_basic_text_area_fit(
        slot=_slot(width_pt=75, min_font_pt=10, font_size_pt=12),
        fill={"action": "text", "text": "OpenAI API", "font_size_override": 12},
    )

    assert result.status == "shrunk"
    assert result.applied_font_pt == 10
    assert result.applied_font_pt == result.min_font_pt
    assert result.final_layout.fits is True


def test_basic_text_area_allows_explicit_zero_padding() -> None:
    """padding_pt=0 은 기본 padding 으로 대체하지 않고 그대로 적용한다."""
    result = evaluate_basic_text_area_fit(
        slot={**_slot(width_pt=80, min_font_pt=10, font_size_pt=12), "padding_pt": 0},
        fill={"action": "text", "text": "OpenAI", "font_size_override": 12},
    )

    assert result.constraints.padding_left_pt == 0
    assert result.constraints.padding_right_pt == 0
    assert result.constraints.content_width_pt == 80


def test_preflight_expands_text_box_before_font_shrink() -> None:
    """확장 가능한 text box 는 폰트 축소 전에 resize_shape action 으로 먼저 넓힌다."""
    result = apply_text_fit_preflight(
        slots=[_expandable_slot(width_pt=75, max_width_pt=100, min_font_pt=10, font_size_pt=12)],
        fills={"2": {"action": "text", "text": "OpenAI API"}},
    )

    assert result.results[0].status == "fit"
    assert result.results[0].reason == "text_box_expanded"
    assert result.results[0].applied_font_pt == 12
    assert "font_size_override" not in result.fills["2"]
    assert result.layout_actions == (
        {
            "action": "resize_shape",
            "shape_id": "2",
            "w_emu": int(100 * EMU_PER_PT),
        },
    )


def test_preflight_shrinks_after_text_box_expansion_if_still_needed() -> None:
    """확장 후에도 넘치는 경우에만 마지막 단계로 font_size_override 를 적용한다."""
    result = apply_text_fit_preflight(
        slots=[_expandable_slot(width_pt=50, max_width_pt=75, min_font_pt=10, font_size_pt=12)],
        fills={"2": {"action": "text", "text": "OpenAI API"}},
    )

    assert result.results[0].status == "shrunk"
    assert result.results[0].reason == "text_box_expanded"
    assert result.fills["2"]["font_size_override"] == 10
    assert result.layout_actions == (
        {
            "action": "resize_shape",
            "shape_id": "2",
            "w_emu": int(75 * EMU_PER_PT),
        },
    )


def test_basic_text_area_does_not_hide_overflow_by_shrinking_to_8pt_or_below() -> None:
    """8pt 이하로 숨길 수 있는 overflow 도 구조적 요약 필요 결과로 남긴다."""
    with pytest.raises(TextFitPreflightError) as exc_info:
        apply_text_fit_preflight(
            slots=[_slot(width_pt=58.4, min_font_pt=8, font_size_pt=12)],
            fills={"2": {"action": "text", "text": "OpenAI API", "font_size_override": 12}},
        )

    result = exc_info.value.results[0]
    assert result.status == "summarize_needed"
    assert result.applied_font_pt == 8.5
    assert result.applied_font_pt > 8
    assert result.reason in {"nowrap_width_overflow", "width_overflow"}


def test_preflight_skips_explicit_non_basic_text_fit_policy() -> None:
    """inline_label_group 은 후속 geometry action 범위라 basic_text_area 정책을 적용하지 않는다."""
    slot = {
        **_slot(width_pt=30, min_font_pt=10, font_size_pt=12),
        "layout_type": "inline_label_group",
        "fit_policy": "resize_label",
    }

    result = apply_text_fit_preflight(
        slots=[slot],
        fills={"2": {"action": "text", "text": "OpenAI API OpenAI API"}},
    )

    assert result.results == ()
    assert result.fills["2"] == {"action": "text", "text": "OpenAI API OpenAI API"}


def test_inline_label_group_resizes_text_and_linked_background() -> None:
    """공백 포함 label 은 nowrap 한 줄을 유지하며 text/background width 를 함께 늘린다."""
    slots = [
        _inline_slot("2", x_pt=0, width_pt=45, background_shape_id="12", row_right_pt=400),
        _inline_slot("3", x_pt=120, width_pt=45, background_shape_id="13", row_right_pt=400),
        _inline_slot("4", x_pt=240, width_pt=45, background_shape_id="14", row_right_pt=400),
    ]

    result = apply_text_fit_preflight(
        slots=slots,
        fills={
            "2": {"action": "text", "text": "OpenAI API", "font_size_override": 12},
            "3": {"action": "text", "text": "FastAPI", "font_size_override": 12},
            "4": {"action": "text", "text": "RAG", "font_size_override": 12},
        },
    )

    inline_result = result.inline_label_results[0]
    first_item = inline_result.item_results[0]
    assert result.results == ()
    assert inline_result.status == "resized"
    assert first_item.measurement.line_count == 1
    assert first_item.applied_w_emu > first_item.original_w_emu
    assert first_item.linked_applied_w_emu > first_item.linked_original_w_emu
    assert any(
        action["action"] == "resize_linked_shape" and action["shape_id"] == "2"
        for action in result.layout_actions
    )
    assert any(action["action"] == "relayout_row" for action in result.layout_actions)
    assert "layout_actions" not in result.fills["2"]


def test_inline_label_group_shrinks_gap_without_overlap() -> None:
    """row 폭이 부족하면 min_gap 이상으로 gap 을 줄여 item overlap 을 피한다."""
    slots = [
        _inline_slot("2", x_pt=0, width_pt=40, background_shape_id="12", row_right_pt=260),
        _inline_slot("3", x_pt=100, width_pt=40, background_shape_id="13", row_right_pt=260),
        _inline_slot("4", x_pt=200, width_pt=40, background_shape_id="14", row_right_pt=260),
    ]

    result = evaluate_inline_label_group_fit(
        group_id="group-1",
        slots=slots,
        fills={
            "2": {"action": "text", "text": "OpenAI", "font_size_override": 12},
            "3": {"action": "text", "text": "FastAPI", "font_size_override": 12},
            "4": {"action": "text", "text": "RAG", "font_size_override": 12},
        },
    )

    assert result.status == "resized"
    assert result.reason == "gap_shrunk"
    assert result.min_gap_emu <= result.applied_gap_emu < result.desired_gap_emu
    for previous, current in zip(
        result.item_results,
        result.item_results[1:],
        strict=False,
    ):
        assert current.applied_x_emu - previous.right_emu >= result.applied_gap_emu


def test_inline_label_group_overflow_requests_abbreviation_before_render() -> None:
    """min gap 까지 줄여도 row 를 넘으면 렌더 전 약칭 fallback 대상으로 분류한다."""
    slots = [
        _inline_slot("2", x_pt=0, width_pt=40, background_shape_id="12", row_right_pt=160),
        _inline_slot("3", x_pt=70, width_pt=40, background_shape_id="13", row_right_pt=160),
        _inline_slot("4", x_pt=140, width_pt=40, background_shape_id="14", row_right_pt=160),
    ]

    result = evaluate_inline_label_group_fit(
        group_id="group-1",
        slots=slots,
        fills={
            "2": {"action": "text", "text": "OpenAI API Platform", "font_size_override": 12},
            "3": {"action": "text", "text": "FastAPI Worker", "font_size_override": 12},
            "4": {"action": "text", "text": "Vector Search", "font_size_override": 12},
        },
    )

    assert result.status == "abbreviation_needed"
    assert result.reason == "inline_label_group_row_overflow"
    assert result.overflow_emu > 0
    assert result.layout_actions == ()


def test_preflight_raises_for_inline_label_group_overflow() -> None:
    """inline_label_group overflow 는 preflight 에서 구조화된 차단 결과로 raise 된다."""
    slots = [
        _inline_slot("2", x_pt=0, width_pt=40, background_shape_id="12", row_right_pt=160),
        _inline_slot("3", x_pt=70, width_pt=40, background_shape_id="13", row_right_pt=160),
        _inline_slot("4", x_pt=140, width_pt=40, background_shape_id="14", row_right_pt=160),
    ]

    with pytest.raises(TextFitPreflightError) as exc_info:
        apply_text_fit_preflight(
            slots=slots,
            fills={
                "2": {"action": "text", "text": "OpenAI API Platform", "font_size_override": 12},
                "3": {"action": "text", "text": "FastAPI Worker", "font_size_override": 12},
                "4": {"action": "text", "text": "Vector Search", "font_size_override": 12},
            },
        )

    inline_result = next(
        result for result in exc_info.value.results if getattr(result, "group_id", "") == "group-1"
    )
    assert inline_result.status == "abbreviation_needed"
    assert "target_id=group-1" in str(exc_info.value)


def _slot(
    *,
    width_pt: float,
    min_font_pt: float,
    font_size_pt: float,
) -> dict[str, object]:
    """테스트용 basic_text_area slot 을 생성한다."""
    return {
        "shape_id": "2",
        "kind": "text",
        "fit_policy": "basic_text_area",
        "w_emu": int(width_pt * EMU_PER_PT),
        "h_emu": int(30 * EMU_PER_PT),
        "font_size_pt": font_size_pt,
        "min_font_pt": min_font_pt,
        "max_lines": 1,
        "nowrap": True,
        "allowed_actions": ["text", "remove"],
    }


def _expandable_slot(
    *,
    width_pt: float,
    max_width_pt: float,
    min_font_pt: float,
    font_size_pt: float,
) -> dict[str, object]:
    """테스트용 expandable basic_text_area slot 을 생성한다."""
    return {
        **_slot(width_pt=width_pt, min_font_pt=min_font_pt, font_size_pt=font_size_pt),
        "text_box_policy": {
            "mode": "expandable",
            "anchor": "left_top",
            "directions": ["right"],
            "max_w_emu": int(max_width_pt * EMU_PER_PT),
            "max_h_emu": int(30 * EMU_PER_PT),
            "confidence": 0.9,
        },
    }


def _inline_slot(
    shape_id: str,
    *,
    x_pt: float,
    width_pt: float,
    background_shape_id: str,
    row_right_pt: float,
) -> dict[str, object]:
    """테스트용 inline_label_group slot 을 생성한다."""
    x_emu = int(x_pt * EMU_PER_PT)
    width_emu = int(width_pt * EMU_PER_PT)
    background_x_emu = int((x_pt - 4) * EMU_PER_PT)
    background_width_emu = int((width_pt + 8) * EMU_PER_PT)
    return {
        "shape_id": shape_id,
        "kind": "text",
        "fit_policy": "resize_label",
        "layout_group_id": "group-1",
        "x_emu": x_emu,
        "y_emu": int(100 * EMU_PER_PT),
        "w_emu": width_emu,
        "h_emu": int(18 * EMU_PER_PT),
        "font_size_pt": 12,
        "max_lines": 1,
        "nowrap": True,
        "padding_pt": 4,
        "row_right_bound_emu": int(row_right_pt * EMU_PER_PT),
        "min_gap_emu": int(10 * EMU_PER_PT),
        "allowed_actions": ["text", "remove"],
        "item_background": {
            "shape_id": background_shape_id,
            "x_emu": background_x_emu,
            "y_emu": int(96 * EMU_PER_PT),
            "w_emu": background_width_emu,
            "h_emu": int(26 * EMU_PER_PT),
            "resize_linked": True,
        },
    }
