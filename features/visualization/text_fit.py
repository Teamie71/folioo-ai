"""PPTX 텍스트 fit preflight 휴리스틱."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

EMU_PER_PT = 12_700.0
DEFAULT_SAFETY_MARGIN_RATIO = 0.12
MIN_SAFETY_MARGIN_RATIO = 0.10
MAX_SAFETY_MARGIN_RATIO = 0.15
DEFAULT_HORIZONTAL_PADDING_PT = 4.0
DEFAULT_VERTICAL_PADDING_PT = 2.0
DEFAULT_FONT_PT = 12.0
DEFAULT_MIN_FONT_PT = 10.0
ABSOLUTE_MIN_FONT_PT = 8.0
LINE_HEIGHT_MULTIPLIER = 1.2
SHRINK_STEP_PT = 0.5
LOG_LINE_WIDTH_LIMIT = 8
DEFAULT_INLINE_LABEL_MIN_GAP_EMU = 50_000

_BASIC_TEXT_TYPES = {"basic_text_area"}
_BASIC_TEXT_POLICIES = {"basic_text_area"}
_INLINE_LABEL_TYPES = {"inline_label_group"}
_INLINE_LABEL_POLICIES = {"resize_label", "inline_label_group"}

TextFitStatus = Literal["fit", "shrunk", "summarize_needed", "failed"]
InlineLabelFitStatus = Literal["fit", "resized", "abbreviation_needed", "failed"]


@dataclass(frozen=True, slots=True)
class TextWidthBreakdown:
    """문자군별 폭 계산 근거."""

    hangul: int = 0
    latin: int = 0
    digit: int = 0
    space: int = 0
    symbol: int = 0
    wide: int = 0
    newline: int = 0

    def to_log_dict(self) -> dict[str, int]:
        """structured log 에 넣을 dict 로 변환한다."""
        return {
            "hangul": self.hangul,
            "latin": self.latin,
            "digit": self.digit,
            "space": self.space,
            "symbol": self.symbol,
            "wide": self.wide,
            "newline": self.newline,
        }


@dataclass(frozen=True, slots=True)
class TextMeasurement:
    """휴리스틱 텍스트 폭 측정 결과."""

    text: str
    font_size_pt: float
    width_pt: float
    line_widths_pt: tuple[float, ...]
    breakdown: TextWidthBreakdown

    @property
    def line_count(self) -> int:
        """명시적 줄바꿈 기준 줄 수."""
        return len(self.line_widths_pt)

    def to_log_dict(self) -> dict[str, Any]:
        """structured log 에 넣을 dict 로 변환한다."""
        return {
            "font_size_pt": self.font_size_pt,
            "width_pt": self.width_pt,
            "line_widths_pt": _limited_log_line_widths(self.line_widths_pt),
            "line_widths_truncated": len(self.line_widths_pt) > LOG_LINE_WIDTH_LIMIT,
            "line_count": self.line_count,
            "breakdown": self.breakdown.to_log_dict(),
        }


@dataclass(frozen=True, slots=True)
class TextLayoutEstimate:
    """줄바꿈 적용 후 텍스트 박스 안에서의 예상 배치."""

    font_size_pt: float
    nowrap: bool
    line_widths_pt: tuple[float, ...]
    line_count: int
    max_line_width_pt: float
    height_pt: float
    overflow_reasons: tuple[str, ...]

    @property
    def fits(self) -> bool:
        """현재 제약 안에 들어가는지 여부."""
        return not self.overflow_reasons

    def to_log_dict(self) -> dict[str, Any]:
        """structured log 에 넣을 dict 로 변환한다."""
        return {
            "font_size_pt": self.font_size_pt,
            "nowrap": self.nowrap,
            "line_widths_pt": _limited_log_line_widths(self.line_widths_pt),
            "line_widths_truncated": len(self.line_widths_pt) > LOG_LINE_WIDTH_LIMIT,
            "line_count": self.line_count,
            "max_line_width_pt": self.max_line_width_pt,
            "height_pt": self.height_pt,
            "overflow_reasons": list(self.overflow_reasons),
        }


@dataclass(frozen=True, slots=True)
class TextBoxConstraints:
    """텍스트 fit 검사에 사용하는 박스 제약."""

    width_pt: float
    height_pt: float
    content_width_pt: float
    content_height_pt: float
    padding_left_pt: float
    padding_right_pt: float
    padding_top_pt: float
    padding_bottom_pt: float
    safety_margin_ratio: float
    max_lines: int
    nowrap: bool

    @property
    def available_width_pt(self) -> float:
        """safety margin 을 뺀 실제 사용 가능 폭."""
        return self.content_width_pt / (1.0 + self.safety_margin_ratio)

    @property
    def available_height_pt(self) -> float:
        """safety margin 을 뺀 실제 사용 가능 높이."""
        return self.content_height_pt / (1.0 + self.safety_margin_ratio)

    def to_log_dict(self) -> dict[str, Any]:
        """structured log 에 넣을 dict 로 변환한다."""
        return {
            "width_pt": self.width_pt,
            "height_pt": self.height_pt,
            "content_width_pt": self.content_width_pt,
            "content_height_pt": self.content_height_pt,
            "available_width_pt": self.available_width_pt,
            "available_height_pt": self.available_height_pt,
            "padding_left_pt": self.padding_left_pt,
            "padding_right_pt": self.padding_right_pt,
            "padding_top_pt": self.padding_top_pt,
            "padding_bottom_pt": self.padding_bottom_pt,
            "safety_margin_ratio": self.safety_margin_ratio,
            "max_lines": self.max_lines,
            "nowrap": self.nowrap,
        }


@dataclass(frozen=True, slots=True)
class BasicTextFitResult:
    """`basic_text_area` preflight 판정 결과."""

    shape_id: str
    status: TextFitStatus
    reason: str | None
    original_font_pt: float
    applied_font_pt: float
    min_font_pt: float
    max_lines: int
    nowrap: bool
    measurement: TextMeasurement
    initial_layout: TextLayoutEstimate
    final_layout: TextLayoutEstimate
    constraints: TextBoxConstraints

    @property
    def is_blocking(self) -> bool:
        """OOXML 적용을 중단해야 하는 결과인지 반환한다."""
        return self.status in {"summarize_needed", "failed"}

    def to_log_dict(self) -> dict[str, Any]:
        """structured log 에 넣을 dict 로 변환한다."""
        return {
            "shape_id": self.shape_id,
            "fit_policy": "basic_text_area",
            "status": self.status,
            "reason": self.reason,
            "original_font_pt": self.original_font_pt,
            "applied_font_pt": self.applied_font_pt,
            "min_font_pt": self.min_font_pt,
            "max_lines": self.max_lines,
            "nowrap": self.nowrap,
            "measurement": self.measurement.to_log_dict(),
            "initial_layout": self.initial_layout.to_log_dict(),
            "final_layout": self.final_layout.to_log_dict(),
            "constraints": self.constraints.to_log_dict(),
        }


@dataclass(frozen=True, slots=True)
class InlineLabelItemFitResult:
    """`inline_label_group` 단일 item 의 resize/relayout 계획."""

    shape_id: str
    linked_shape_id: str | None
    text_length: int
    original_x_emu: int
    applied_x_emu: int
    original_w_emu: int
    required_w_emu: int
    applied_w_emu: int
    linked_original_x_emu: int | None
    linked_applied_x_emu: int | None
    linked_original_w_emu: int | None
    linked_applied_w_emu: int | None
    measurement: TextMeasurement

    @property
    def resized(self) -> bool:
        """text/background 폭이 실제 변경되는지 반환한다."""
        return self.applied_w_emu != self.original_w_emu or (
            self.linked_original_w_emu is not None
            and self.linked_applied_w_emu != self.linked_original_w_emu
        )

    @property
    def moved(self) -> bool:
        """text/background x 좌표가 실제 변경되는지 반환한다."""
        return self.applied_x_emu != self.original_x_emu or (
            self.linked_original_x_emu is not None
            and self.linked_applied_x_emu != self.linked_original_x_emu
        )

    @property
    def right_emu(self) -> int:
        """적용 후 text box 오른쪽 좌표."""
        return self.applied_x_emu + self.applied_w_emu

    def to_log_dict(self) -> dict[str, Any]:
        """structured log 에 넣을 dict 로 변환한다."""
        return {
            "shape_id": self.shape_id,
            "linked_shape_id": self.linked_shape_id,
            "text_length": self.text_length,
            "original_x_emu": self.original_x_emu,
            "applied_x_emu": self.applied_x_emu,
            "original_w_emu": self.original_w_emu,
            "required_w_emu": self.required_w_emu,
            "applied_w_emu": self.applied_w_emu,
            "linked_original_x_emu": self.linked_original_x_emu,
            "linked_applied_x_emu": self.linked_applied_x_emu,
            "linked_original_w_emu": self.linked_original_w_emu,
            "linked_applied_w_emu": self.linked_applied_w_emu,
            "measurement": self.measurement.to_log_dict(),
        }


@dataclass(frozen=True, slots=True)
class InlineLabelGroupFitResult:
    """`inline_label_group` row fit 판정과 layout action 계획."""

    group_id: str
    status: InlineLabelFitStatus
    reason: str | None
    row_left_emu: int
    row_right_bound_emu: int
    current_row_width_emu: int
    required_row_width_emu: int
    desired_gap_emu: int
    min_gap_emu: int
    applied_gap_emu: int
    overflow_emu: int
    item_results: tuple[InlineLabelItemFitResult, ...]
    layout_actions: tuple[dict[str, Any], ...]

    @property
    def is_blocking(self) -> bool:
        """OOXML 적용을 중단해야 하는 결과인지 반환한다."""
        return self.status in {"abbreviation_needed", "failed"}

    def to_log_dict(self) -> dict[str, Any]:
        """structured log 에 넣을 dict 로 변환한다."""
        return {
            "group_id": self.group_id,
            "fit_policy": "resize_label",
            "status": self.status,
            "reason": self.reason,
            "row_left_emu": self.row_left_emu,
            "row_right_bound_emu": self.row_right_bound_emu,
            "current_row_width_emu": self.current_row_width_emu,
            "required_row_width_emu": self.required_row_width_emu,
            "desired_gap_emu": self.desired_gap_emu,
            "min_gap_emu": self.min_gap_emu,
            "applied_gap_emu": self.applied_gap_emu,
            "overflow_emu": self.overflow_emu,
            "layout_action_count": len(self.layout_actions),
            "items": [item.to_log_dict() for item in self.item_results],
        }


TextFitResultEntry = BasicTextFitResult | InlineLabelGroupFitResult


@dataclass(frozen=True, slots=True)
class TextFitPreflightResult:
    """fill 맵에 대한 text fit preflight 전체 결과."""

    fills: dict[str, dict[str, Any]]
    results: tuple[BasicTextFitResult, ...]
    inline_label_results: tuple[InlineLabelGroupFitResult, ...] = ()
    layout_actions: tuple[dict[str, Any], ...] = ()


class TextFitPreflightError(ValueError):
    """텍스트 fit preflight 가 차단 결과로 끝났을 때 발생한다."""

    def __init__(self, results: Sequence[TextFitResultEntry]) -> None:
        self.results = tuple(results)
        first = next((result for result in self.results if result.is_blocking), self.results[0])
        target_id = getattr(first, "shape_id", None) or getattr(first, "group_id", "")
        super().__init__(
            "PPTX 텍스트가 slot 용량을 초과했습니다. "
            f"target_id={target_id}, status={first.status}, reason={first.reason}"
        )


def measure_text_width(text: str, *, font_size_pt: float) -> TextMeasurement:
    """
    문자군별 계수로 텍스트 폭을 deterministic 하게 추정한다.

    실제 font metric 이 아니라 한글/CJK, 영문, 숫자, 공백, 기호에 고정 계수를 곱한다.
    """
    font_size = _positive_float(font_size_pt) or DEFAULT_FONT_PT
    line_widths: list[float] = []
    current_width = 0.0
    counts = {
        "hangul": 0,
        "latin": 0,
        "digit": 0,
        "space": 0,
        "symbol": 0,
        "wide": 0,
        "newline": 0,
    }
    for char in text:
        if char == "\n":
            line_widths.append(_round_pt(current_width))
            current_width = 0.0
            counts["newline"] += 1
            continue
        group, unit = _character_width_unit(char)
        counts[group] += 1
        current_width += unit * font_size
    line_widths.append(_round_pt(current_width))

    return TextMeasurement(
        text=text,
        font_size_pt=_round_pt(font_size),
        width_pt=max(line_widths) if line_widths else 0.0,
        line_widths_pt=tuple(line_widths),
        breakdown=TextWidthBreakdown(**counts),
    )


def estimate_text_layout(
    text: str,
    *,
    font_size_pt: float,
    constraints: TextBoxConstraints,
) -> TextLayoutEstimate:
    """텍스트가 주어진 제약 안에서 몇 줄로 배치될지 추정한다."""
    font_size = _positive_float(font_size_pt) or DEFAULT_FONT_PT
    available_width = constraints.available_width_pt
    if constraints.nowrap:
        line_widths = measure_text_width(text, font_size_pt=font_size).line_widths_pt
    else:
        line_widths = _wrap_text_line_widths(
            text,
            font_size_pt=font_size,
            available_width_pt=available_width,
        )

    max_line_width = max(line_widths) if line_widths else 0.0
    line_count = len(line_widths)
    height = _round_pt(line_count * font_size * LINE_HEIGHT_MULTIPLIER)
    overflow_reasons: list[str] = []
    if constraints.content_width_pt <= 0:
        overflow_reasons.append("content_width_too_small")
    if constraints.content_height_pt <= 0:
        overflow_reasons.append("content_height_too_small")
    if constraints.nowrap and any(width > available_width for width in line_widths):
        overflow_reasons.append("nowrap_width_overflow")
    if line_count > constraints.max_lines:
        overflow_reasons.append("max_lines_overflow")
    if max_line_width > available_width:
        overflow_reasons.append("width_overflow")
    if height > constraints.available_height_pt:
        overflow_reasons.append("height_overflow")

    return TextLayoutEstimate(
        font_size_pt=_round_pt(font_size),
        nowrap=constraints.nowrap,
        line_widths_pt=tuple(_round_pt(width) for width in line_widths),
        line_count=line_count,
        max_line_width_pt=_round_pt(max_line_width),
        height_pt=height,
        overflow_reasons=tuple(dict.fromkeys(overflow_reasons)),
    )


def evaluate_basic_text_area_fit(
    *,
    slot: Mapping[str, Any],
    fill: Mapping[str, Any],
) -> BasicTextFitResult:
    """단일 `basic_text_area` fill 을 측정하고 shrink/요약/실패 상태를 판정한다."""
    shape_id = str(slot.get("shape_id") or "")
    text = str(fill.get("text") or "")
    original_font = _fill_font_size(slot, fill)
    min_font = _min_font_size(slot, original_font)
    constraints = _constraints_from_slot(slot)
    effective_font = max(original_font, min_font)
    measurement = measure_text_width(text, font_size_pt=effective_font)

    if constraints.content_width_pt <= 0 or constraints.content_height_pt <= 0:
        layout = estimate_text_layout(text, font_size_pt=effective_font, constraints=constraints)
        return BasicTextFitResult(
            shape_id=shape_id,
            status="failed",
            reason=_first_reason(layout, "invalid_text_box"),
            original_font_pt=_round_pt(original_font),
            applied_font_pt=_round_pt(effective_font),
            min_font_pt=min_font,
            max_lines=constraints.max_lines,
            nowrap=constraints.nowrap,
            measurement=measurement,
            initial_layout=layout,
            final_layout=layout,
            constraints=constraints,
        )

    initial_layout = estimate_text_layout(
        text,
        font_size_pt=effective_font,
        constraints=constraints,
    )
    if initial_layout.fits:
        return BasicTextFitResult(
            shape_id=shape_id,
            status="fit",
            reason=None if original_font >= min_font else "font_below_min",
            original_font_pt=_round_pt(original_font),
            applied_font_pt=_round_pt(effective_font),
            min_font_pt=min_font,
            max_lines=constraints.max_lines,
            nowrap=constraints.nowrap,
            measurement=measurement,
            initial_layout=initial_layout,
            final_layout=initial_layout,
            constraints=constraints,
        )

    for candidate_font in _shrink_candidates(effective_font, min_font):
        candidate_layout = estimate_text_layout(
            text,
            font_size_pt=candidate_font,
            constraints=constraints,
        )
        if candidate_layout.fits:
            return BasicTextFitResult(
                shape_id=shape_id,
                status="shrunk",
                reason=None,
                original_font_pt=_round_pt(original_font),
                applied_font_pt=_round_pt(candidate_font),
                min_font_pt=min_font,
                max_lines=constraints.max_lines,
                nowrap=constraints.nowrap,
                measurement=measurement,
                initial_layout=initial_layout,
                final_layout=candidate_layout,
                constraints=constraints,
            )

    min_layout = estimate_text_layout(text, font_size_pt=min_font, constraints=constraints)
    status: TextFitStatus = "summarize_needed"
    reason = _first_reason(min_layout, "text_overflow")
    if "content_width_too_small" in min_layout.overflow_reasons:
        status = "failed"
    elif "content_height_too_small" in min_layout.overflow_reasons:
        status = "failed"
    elif min_font * LINE_HEIGHT_MULTIPLIER > constraints.available_height_pt:
        status = "failed"
        reason = "min_font_height_overflow"

    return BasicTextFitResult(
        shape_id=shape_id,
        status=status,
        reason=reason,
        original_font_pt=_round_pt(original_font),
        applied_font_pt=min_font,
        min_font_pt=min_font,
        max_lines=constraints.max_lines,
        nowrap=constraints.nowrap,
        measurement=measurement,
        initial_layout=initial_layout,
        final_layout=min_layout,
        constraints=constraints,
    )


def evaluate_inline_label_group_fit(
    *,
    group_id: str,
    slots: Sequence[Mapping[str, Any]],
    fills: Mapping[str, Mapping[str, Any]],
) -> InlineLabelGroupFitResult:
    """단일 `inline_label_group` row 의 resize/relayout action 을 계산한다."""
    sorted_slots = _sort_slots_by_x(slots)
    if len(sorted_slots) < 2:
        return _inline_label_group_failure(
            group_id=group_id,
            reason="inline_label_group_item_count_too_small",
        )

    item_plans = [
        _inline_label_item_plan(slot=slot, fill=fills.get(str(slot.get("shape_id") or ""), {}))
        for slot in sorted_slots
    ]
    if any(plan is None for plan in item_plans):
        return _inline_label_group_failure(
            group_id=group_id,
            reason="inline_label_group_invalid_item_geometry",
        )

    plans = [plan for plan in item_plans if plan is not None]
    row_left = min(plan["item_left_emu"] for plan in plans)
    row_right_bound = _group_row_right_bound_emu(sorted_slots) or max(
        plan["item_right_emu"] for plan in plans
    )
    current_width = max(0, row_right_bound - row_left)
    desired_gap = _group_gap_emu(sorted_slots, plans)
    min_gap = _group_min_gap_emu(sorted_slots, desired_gap)
    required_width_at_desired_gap = _required_inline_label_row_width(plans, desired_gap)
    required_width_at_min_gap = _required_inline_label_row_width(plans, min_gap)

    if current_width <= 0:
        return _inline_label_group_failure(
            group_id=group_id,
            reason="inline_label_group_row_width_too_small",
            row_left_emu=row_left,
            row_right_bound_emu=row_right_bound,
            current_row_width_emu=current_width,
            required_row_width_emu=required_width_at_min_gap,
            desired_gap_emu=desired_gap,
            min_gap_emu=min_gap,
            overflow_emu=max(0, required_width_at_min_gap - current_width),
        )

    if required_width_at_desired_gap <= current_width:
        applied_gap = desired_gap
        required_row_width = required_width_at_desired_gap
        overflow = 0
    elif required_width_at_min_gap <= current_width:
        applied_gap = _largest_gap_that_fits(
            current_width=current_width,
            required_item_width=sum(plan["applied_item_width_emu"] for plan in plans),
            item_count=len(plans),
            desired_gap=desired_gap,
            min_gap=min_gap,
        )
        required_row_width = _required_inline_label_row_width(plans, applied_gap)
        overflow = 0
    else:
        return _inline_label_group_failure(
            group_id=group_id,
            reason="inline_label_group_row_overflow",
            row_left_emu=row_left,
            row_right_bound_emu=row_right_bound,
            current_row_width_emu=current_width,
            required_row_width_emu=required_width_at_min_gap,
            desired_gap_emu=desired_gap,
            min_gap_emu=min_gap,
            overflow_emu=max(0, required_width_at_min_gap - current_width),
            status="abbreviation_needed",
            item_results=_inline_label_item_results(plans, row_left, min_gap),
        )

    item_results = _inline_label_item_results(plans, row_left, applied_gap)
    layout_actions = _inline_label_layout_actions(group_id, item_results, applied_gap, min_gap)
    status: InlineLabelFitStatus = "fit"
    reason: str | None = None
    if layout_actions:
        status = "resized"
        if applied_gap < desired_gap:
            reason = "gap_shrunk"

    return InlineLabelGroupFitResult(
        group_id=group_id,
        status=status,
        reason=reason,
        row_left_emu=row_left,
        row_right_bound_emu=row_right_bound,
        current_row_width_emu=current_width,
        required_row_width_emu=required_row_width,
        desired_gap_emu=desired_gap,
        min_gap_emu=min_gap,
        applied_gap_emu=applied_gap,
        overflow_emu=overflow,
        item_results=tuple(item_results),
        layout_actions=tuple(layout_actions),
    )


def apply_text_fit_preflight(
    *,
    slots: Sequence[Mapping[str, Any]],
    fills: Mapping[str, Mapping[str, Any]],
) -> TextFitPreflightResult:
    """
    fill 맵에 `basic_text_area` preflight 를 적용한다.

    성공한 shrink 는 `font_size_override` 로 반영하고, 요약/실패가 필요한 결과는
    `TextFitPreflightError` 로 구조화해 호출자가 렌더 전 중단할 수 있게 한다.
    """
    slots_by_id = {str(slot.get("shape_id")): slot for slot in slots if slot.get("shape_id")}
    adjusted = {str(shape_id): dict(fill) for shape_id, fill in fills.items()}
    results: list[BasicTextFitResult] = []
    inline_results: list[InlineLabelGroupFitResult] = []
    layout_actions: list[dict[str, Any]] = []
    blocking: list[TextFitResultEntry] = []

    for group_id, group_slots in _inline_label_groups(slots).items():
        result = evaluate_inline_label_group_fit(
            group_id=group_id,
            slots=group_slots,
            fills=adjusted,
        )
        inline_results.append(result)
        if result.is_blocking:
            blocking.append(result)
            continue
        layout_actions.extend(result.layout_actions)

    for shape_id, fill in adjusted.items():
        slot = slots_by_id.get(shape_id)
        if slot is None or not _uses_basic_text_area(slot):
            continue
        if str(fill.get("action") or "text") != "text":
            continue
        if not _has_geometry(slot):
            continue

        result = evaluate_basic_text_area_fit(slot=slot, fill=fill)
        results.append(result)
        if result.is_blocking:
            blocking.append(result)
            continue
        if _should_apply_font_override(fill, result):
            fill["font_size_override"] = result.applied_font_pt

    if blocking:
        raise TextFitPreflightError((*results, *inline_results))
    return TextFitPreflightResult(
        fills=adjusted,
        results=tuple(results),
        inline_label_results=tuple(inline_results),
        layout_actions=tuple(layout_actions),
    )


def emu_to_pt(value: Any) -> float | None:
    """EMU 값을 pt 로 변환한다."""
    number = _positive_float(value)
    if number is None:
        return None
    return _round_pt(number / EMU_PER_PT)


def pt_to_emu(value: Any) -> int | None:
    """pt 값을 EMU 로 변환한다."""
    number = _positive_float(value)
    if number is None:
        return None
    return round(number * EMU_PER_PT)


def _inline_label_groups(
    slots: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for slot in slots:
        if not _uses_inline_label_group(slot):
            continue
        group_id = _slot_group_id(slot)
        if not group_id:
            continue
        groups.setdefault(group_id, []).append(slot)
    return {
        group_id: tuple(_sort_slots_by_x(group_slots))
        for group_id, group_slots in groups.items()
        if len(group_slots) >= 2
    }


def _uses_inline_label_group(slot: Mapping[str, Any]) -> bool:
    if str(slot.get("kind") or "text").casefold() != "text":
        return False
    layout_type = str(slot.get("layout_type") or "").strip().casefold()
    layout_group_type = str(slot.get("layout_group_type") or "").strip().casefold()
    fit_policy = str(slot.get("fit_policy") or "").strip().casefold()
    if fit_policy:
        return fit_policy in _INLINE_LABEL_POLICIES
    return layout_type in _INLINE_LABEL_TYPES or layout_group_type in _INLINE_LABEL_TYPES


def _slot_group_id(slot: Mapping[str, Any]) -> str:
    for field in ("layout_group_id", "group_id"):
        value = slot.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _sort_slots_by_x(slots: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(sorted(slots, key=_slot_x_sort_key))


def _slot_x_sort_key(slot: Mapping[str, Any]) -> tuple[int, str]:
    return (_int_value(slot.get("x_emu")) or 0, str(slot.get("shape_id") or ""))


def _inline_label_item_plan(
    *,
    slot: Mapping[str, Any],
    fill: Mapping[str, Any],
) -> dict[str, Any] | None:
    shape_id = str(slot.get("shape_id") or "").strip()
    if not shape_id:
        return None

    slot_box = _slot_box(slot)
    if slot_box is None:
        return None
    slot_x, slot_y, slot_w, slot_h = slot_box
    text = str(fill.get("text") or slot.get("current_text") or slot.get("placeholder_text") or "")
    font_size = _fill_font_size(slot, fill)
    measurement = measure_text_width(text, font_size_pt=font_size)
    required_w = max(slot_w, _required_inline_text_width_emu(slot, measurement))

    background = _slot_item_background(slot)
    linked_shape_id = str(background.get("shape_id") or "").strip() if background else ""
    linked_box = _background_box(background) if background else None
    item_left = slot_x
    item_right = slot_x + slot_w
    linked_margin_w = 0
    if linked_box is not None:
        bg_x, _bg_y, bg_w, _bg_h = linked_box
        item_left = min(item_left, bg_x)
        item_right = max(item_right, bg_x + bg_w)
        linked_margin_w = max(0, bg_w - slot_w)

    linked_required_w = None
    if linked_box is not None:
        linked_required_w = max(linked_box[2], required_w + linked_margin_w)

    text_offset_x = slot_x - item_left
    linked_offset_x = linked_box[0] - item_left if linked_box is not None else None
    applied_item_width = max(text_offset_x + required_w, item_right - item_left)
    if linked_box is not None and linked_required_w is not None and linked_offset_x is not None:
        applied_item_width = max(applied_item_width, linked_offset_x + linked_required_w)

    return {
        "shape_id": shape_id,
        "linked_shape_id": linked_shape_id or None,
        "text": text,
        "text_length": len(text),
        "measurement": measurement,
        "slot_x_emu": slot_x,
        "slot_y_emu": slot_y,
        "slot_w_emu": slot_w,
        "slot_h_emu": slot_h,
        "required_w_emu": required_w,
        "linked_box": linked_box,
        "linked_required_w_emu": linked_required_w,
        "text_offset_x_emu": text_offset_x,
        "linked_offset_x_emu": linked_offset_x,
        "item_left_emu": item_left,
        "item_right_emu": item_right,
        "item_width_emu": item_right - item_left,
        "applied_item_width_emu": applied_item_width,
    }


def _required_inline_text_width_emu(
    slot: Mapping[str, Any],
    measurement: TextMeasurement,
) -> int:
    padding_left = _slot_padding_pt(slot, "left", DEFAULT_HORIZONTAL_PADDING_PT)
    padding_right = _slot_padding_pt(slot, "right", DEFAULT_HORIZONTAL_PADDING_PT)
    safety_margin = _safety_margin_ratio(slot.get("safety_margin_ratio"))
    width_pt = (measurement.width_pt * (1.0 + safety_margin)) + padding_left + padding_right
    return pt_to_emu(width_pt) or 0


def _slot_box(slot: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
    x_emu = _int_value(slot.get("x_emu"))
    y_emu = _int_value(slot.get("y_emu"))
    w_emu = _positive_int(slot.get("w_emu"))
    h_emu = _positive_int(slot.get("h_emu"))
    if x_emu is None or y_emu is None or w_emu is None or h_emu is None:
        return None
    return (x_emu, y_emu, w_emu, h_emu)


def _slot_item_background(slot: Mapping[str, Any]) -> Mapping[str, Any]:
    value = slot.get("item_background")
    if isinstance(value, Mapping):
        return value
    return {}


def _background_box(background: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
    x_emu = _int_value(background.get("x_emu"))
    y_emu = _int_value(background.get("y_emu"))
    w_emu = _positive_int(background.get("w_emu"))
    h_emu = _positive_int(background.get("h_emu"))
    if x_emu is None or y_emu is None or w_emu is None or h_emu is None:
        return None
    return (x_emu, y_emu, w_emu, h_emu)


def _group_row_right_bound_emu(slots: Sequence[Mapping[str, Any]]) -> int | None:
    for field in ("row_right_bound_emu", "max_x_emu"):
        value = _first_positive_int(slots, field)
        if value is not None:
            return value

    row_width = _first_positive_int(slots, "row_width_emu")
    if row_width is None:
        return None
    row_left = min((_int_value(slot.get("x_emu")) or 0 for slot in slots), default=0)
    return row_left + row_width


def _group_gap_emu(slots: Sequence[Mapping[str, Any]], plans: Sequence[Mapping[str, Any]]) -> int:
    configured = _first_positive_int(slots, "gap_emu")
    if configured is not None:
        return configured

    gaps = _current_inline_label_gaps(plans)
    if not gaps:
        return DEFAULT_INLINE_LABEL_MIN_GAP_EMU
    return _median_int(gaps)


def _group_min_gap_emu(slots: Sequence[Mapping[str, Any]], desired_gap: int) -> int:
    configured = _first_positive_int(slots, "min_gap_emu")
    if configured is not None:
        return min(configured, desired_gap)
    return min(DEFAULT_INLINE_LABEL_MIN_GAP_EMU, desired_gap)


def _first_positive_int(slots: Sequence[Mapping[str, Any]], field: str) -> int | None:
    for slot in slots:
        value = _positive_int(slot.get(field))
        if value is not None:
            return value
    return None


def _current_inline_label_gaps(plans: Sequence[Mapping[str, Any]]) -> tuple[int, ...]:
    gaps: list[int] = []
    for left, right in zip(plans, plans[1:], strict=False):
        gaps.append(max(0, int(right["item_left_emu"]) - int(left["item_right_emu"])))
    return tuple(gaps)


def _required_inline_label_row_width(
    plans: Sequence[Mapping[str, Any]],
    gap_emu: int,
) -> int:
    if not plans:
        return 0
    item_width = sum(int(plan["applied_item_width_emu"]) for plan in plans)
    return item_width + (gap_emu * (len(plans) - 1))


def _largest_gap_that_fits(
    *,
    current_width: int,
    required_item_width: int,
    item_count: int,
    desired_gap: int,
    min_gap: int,
) -> int:
    if item_count <= 1:
        return 0
    remaining = current_width - required_item_width
    if remaining <= 0:
        return min_gap
    return min(desired_gap, max(min_gap, remaining // (item_count - 1)))


def _inline_label_item_results(
    plans: Sequence[Mapping[str, Any]],
    row_left: int,
    applied_gap: int,
) -> list[InlineLabelItemFitResult]:
    results: list[InlineLabelItemFitResult] = []
    item_left = row_left
    for plan in plans:
        applied_x = item_left + int(plan["text_offset_x_emu"])
        linked_box = plan["linked_box"]
        linked_offset_x = plan["linked_offset_x_emu"]
        linked_applied_x = None
        linked_original_x = None
        linked_original_w = None
        linked_applied_w = None
        if linked_box is not None and linked_offset_x is not None:
            linked_original_x = linked_box[0]
            linked_original_w = linked_box[2]
            linked_applied_x = item_left + int(linked_offset_x)
            linked_applied_w = int(plan["linked_required_w_emu"] or linked_original_w)

        results.append(
            InlineLabelItemFitResult(
                shape_id=str(plan["shape_id"]),
                linked_shape_id=plan["linked_shape_id"],
                text_length=int(plan["text_length"]),
                original_x_emu=int(plan["slot_x_emu"]),
                applied_x_emu=applied_x,
                original_w_emu=int(plan["slot_w_emu"]),
                required_w_emu=int(plan["required_w_emu"]),
                applied_w_emu=int(plan["required_w_emu"]),
                linked_original_x_emu=linked_original_x,
                linked_applied_x_emu=linked_applied_x,
                linked_original_w_emu=linked_original_w,
                linked_applied_w_emu=linked_applied_w,
                measurement=plan["measurement"],
            )
        )
        item_left += int(plan["applied_item_width_emu"]) + applied_gap
    return results


def _inline_label_layout_actions(
    group_id: str,
    item_results: Sequence[InlineLabelItemFitResult],
    applied_gap: int,
    min_gap: int,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in item_results:
        if not item.resized:
            continue
        if item.linked_shape_id:
            action = {
                "action": "resize_linked_shape",
                "shape_id": item.shape_id,
                "linked_shape_ids": [item.linked_shape_id],
                "w_emu": item.applied_w_emu,
            }
            if item.linked_applied_w_emu is not None:
                action["linked_w_emu"] = item.linked_applied_w_emu
        else:
            action = {
                "action": "resize_shape",
                "shape_id": item.shape_id,
                "w_emu": item.applied_w_emu,
            }
        actions.append(action)

    if any(item.moved for item in item_results):
        actions.append(
            {
                "action": "relayout_row",
                "group_id": group_id,
                "gap_emu": applied_gap,
                "min_gap_emu": min_gap,
                "items": [
                    _relayout_row_item_payload(item)
                    for item in item_results
                    if item.moved or item.resized or item.linked_shape_id is not None
                ],
            }
        )
    return actions


def _relayout_row_item_payload(item: InlineLabelItemFitResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "shape_id": item.shape_id,
        "x_emu": item.applied_x_emu,
        "w_emu": item.applied_w_emu,
    }
    if item.linked_shape_id:
        payload["linked_shape_ids"] = [item.linked_shape_id]
    if item.linked_applied_x_emu is not None:
        payload["linked_x_emu"] = item.linked_applied_x_emu
    if item.linked_applied_w_emu is not None:
        payload["linked_w_emu"] = item.linked_applied_w_emu
    return payload


def _inline_label_group_failure(
    *,
    group_id: str,
    reason: str,
    row_left_emu: int = 0,
    row_right_bound_emu: int = 0,
    current_row_width_emu: int = 0,
    required_row_width_emu: int = 0,
    desired_gap_emu: int = 0,
    min_gap_emu: int = 0,
    overflow_emu: int = 0,
    status: InlineLabelFitStatus = "failed",
    item_results: Sequence[InlineLabelItemFitResult] = (),
) -> InlineLabelGroupFitResult:
    return InlineLabelGroupFitResult(
        group_id=group_id,
        status=status,
        reason=reason,
        row_left_emu=row_left_emu,
        row_right_bound_emu=row_right_bound_emu,
        current_row_width_emu=current_row_width_emu,
        required_row_width_emu=required_row_width_emu,
        desired_gap_emu=desired_gap_emu,
        min_gap_emu=min_gap_emu,
        applied_gap_emu=min_gap_emu,
        overflow_emu=overflow_emu,
        item_results=tuple(item_results),
        layout_actions=(),
    )


def _median_int(values: Sequence[int]) -> int:
    sorted_values = sorted(values)
    if not sorted_values:
        return 0
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return sorted_values[middle]
    return round((sorted_values[middle - 1] + sorted_values[middle]) / 2)


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _character_width_unit(char: str) -> tuple[str, float]:
    if _is_hangul(char):
        return "hangul", 1.0
    if char.isascii() and char.isalpha():
        return "latin", 0.65 if char.isupper() else 0.55
    if char.isascii() and char.isdigit():
        return "digit", 0.55
    if char.isspace():
        return "space", 0.33
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return "wide", 1.0
    return "symbol", 0.45


def _is_hangul(char: str) -> bool:
    codepoint = ord(char)
    return (
        0xAC00 <= codepoint <= 0xD7A3
        or 0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xD7B0 <= codepoint <= 0xD7FF
    )


def _wrap_text_line_widths(
    text: str,
    *,
    font_size_pt: float,
    available_width_pt: float,
) -> tuple[float, ...]:
    if not text:
        return (0.0,)

    widths: list[float] = []
    for explicit_line in text.split("\n"):
        if explicit_line == "":
            widths.append(0.0)
            continue

        current_width = 0.0
        for char in explicit_line:
            _group, unit = _character_width_unit(char)
            char_width = unit * font_size_pt
            if current_width > 0 and current_width + char_width > available_width_pt:
                widths.append(_round_pt(current_width))
                current_width = char_width
            else:
                current_width += char_width
        widths.append(_round_pt(current_width))
    return tuple(widths) or (0.0,)


def _constraints_from_slot(slot: Mapping[str, Any]) -> TextBoxConstraints:
    width_pt = emu_to_pt(slot.get("w_emu")) or 0.0
    height_pt = emu_to_pt(slot.get("h_emu")) or 0.0
    padding_left = _slot_padding_pt(slot, "left", DEFAULT_HORIZONTAL_PADDING_PT)
    padding_right = _slot_padding_pt(slot, "right", DEFAULT_HORIZONTAL_PADDING_PT)
    padding_top = _slot_padding_pt(slot, "top", DEFAULT_VERTICAL_PADDING_PT)
    padding_bottom = _slot_padding_pt(slot, "bottom", DEFAULT_VERTICAL_PADDING_PT)
    max_lines = _positive_int(slot.get("max_lines")) or _fallback_max_lines(slot)
    nowrap = slot.get("nowrap")
    if not isinstance(nowrap, bool):
        nowrap = max_lines == 1
    safety_margin = _safety_margin_ratio(slot.get("safety_margin_ratio"))
    content_width = max(0.0, width_pt - padding_left - padding_right)
    content_height = max(0.0, height_pt - padding_top - padding_bottom)
    return TextBoxConstraints(
        width_pt=width_pt,
        height_pt=height_pt,
        content_width_pt=_round_pt(content_width),
        content_height_pt=_round_pt(content_height),
        padding_left_pt=padding_left,
        padding_right_pt=padding_right,
        padding_top_pt=padding_top,
        padding_bottom_pt=padding_bottom,
        safety_margin_ratio=safety_margin,
        max_lines=max_lines,
        nowrap=nowrap,
    )


def _slot_padding_pt(slot: Mapping[str, Any], side: str, default: float) -> float:
    side_value = _non_negative_float(slot.get(f"padding_{side}_pt"))
    if side_value is not None:
        return _round_pt(side_value)

    axis = "x" if side in {"left", "right"} else "y"
    axis_value = _non_negative_float(slot.get(f"padding_{axis}_pt"))
    if axis_value is not None:
        return _round_pt(axis_value)

    all_value = _non_negative_float(slot.get("padding_pt"))
    if all_value is not None:
        return _round_pt(all_value)

    return default


def _safety_margin_ratio(value: Any) -> float:
    ratio = _positive_float(value)
    if ratio is None:
        return DEFAULT_SAFETY_MARGIN_RATIO
    return _round_ratio(min(max(ratio, MIN_SAFETY_MARGIN_RATIO), MAX_SAFETY_MARGIN_RATIO))


def _fill_font_size(slot: Mapping[str, Any], fill: Mapping[str, Any]) -> float:
    return (
        _positive_float(fill.get("font_size_override"))
        or _positive_float(slot.get("font_size_pt"))
        or _positive_float(slot.get("max_font_pt"))
        or DEFAULT_FONT_PT
    )


def _min_font_size(slot: Mapping[str, Any], original_font: float) -> float:
    inferred = max(DEFAULT_MIN_FONT_PT, original_font * 0.6)
    min_font = _positive_float(slot.get("min_font_pt")) or inferred
    if min_font <= ABSOLUTE_MIN_FONT_PT:
        min_font = ABSOLUTE_MIN_FONT_PT + SHRINK_STEP_PT
    return _round_pt(max(min_font, ABSOLUTE_MIN_FONT_PT))


def _fallback_max_lines(slot: Mapping[str, Any]) -> int:
    for field in ("example_text", "placeholder_text", "current_text"):
        value = slot.get(field)
        if isinstance(value, str) and value:
            return max(1, len(value.splitlines()))
    return 1


def _shrink_candidates(original_font: float, min_font: float) -> tuple[float, ...]:
    candidates: list[float] = []
    current = _round_pt(original_font - SHRINK_STEP_PT)
    while current > min_font:
        candidates.append(current)
        current = _round_pt(current - SHRINK_STEP_PT)
    if original_font > min_font:
        candidates.append(min_font)
    return tuple(dict.fromkeys(candidates))


def _uses_basic_text_area(slot: Mapping[str, Any]) -> bool:
    if str(slot.get("kind") or "text").casefold() != "text":
        return False
    layout_type = str(slot.get("layout_type") or "").strip().casefold()
    layout_group_type = str(slot.get("layout_group_type") or "").strip().casefold()
    fit_policy = str(slot.get("fit_policy") or "").strip().casefold()
    if fit_policy:
        return fit_policy in _BASIC_TEXT_POLICIES
    if layout_type or layout_group_type:
        return layout_type in _BASIC_TEXT_TYPES or layout_group_type in _BASIC_TEXT_TYPES
    return True


def _has_geometry(slot: Mapping[str, Any]) -> bool:
    return (
        _positive_float(slot.get("w_emu")) is not None
        and _positive_float(slot.get("h_emu")) is not None
    )


def _should_apply_font_override(
    fill: Mapping[str, Any],
    result: BasicTextFitResult,
) -> bool:
    existing = _positive_float(fill.get("font_size_override"))
    if existing is None:
        return result.status == "shrunk"
    return _round_pt(existing) != result.applied_font_pt


def _first_reason(layout: TextLayoutEstimate, fallback: str) -> str:
    if layout.overflow_reasons:
        return layout.overflow_reasons[0]
    return fallback


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _non_negative_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _round_pt(value: float) -> float:
    return round(value, 2)


def _round_ratio(value: float) -> float:
    return round(value, 4)


def _limited_log_line_widths(line_widths: Sequence[float]) -> list[float]:
    return list(line_widths[:LOG_LINE_WIDTH_LIMIT])


__all__ = [
    "ABSOLUTE_MIN_FONT_PT",
    "BasicTextFitResult",
    "DEFAULT_INLINE_LABEL_MIN_GAP_EMU",
    "InlineLabelFitStatus",
    "InlineLabelGroupFitResult",
    "InlineLabelItemFitResult",
    "TextFitPreflightError",
    "TextFitPreflightResult",
    "TextFitResultEntry",
    "TextLayoutEstimate",
    "TextMeasurement",
    "TextWidthBreakdown",
    "apply_text_fit_preflight",
    "emu_to_pt",
    "estimate_text_layout",
    "evaluate_basic_text_area_fit",
    "evaluate_inline_label_group_fit",
    "measure_text_width",
    "pt_to_emu",
]
