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

_BASIC_TEXT_TYPES = {"basic_text_area"}
_BASIC_TEXT_POLICIES = {"basic_text_area"}

TextFitStatus = Literal["fit", "shrunk", "summarize_needed", "failed"]


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
class TextFitPreflightResult:
    """fill 맵에 대한 text fit preflight 전체 결과."""

    fills: dict[str, dict[str, Any]]
    results: tuple[BasicTextFitResult, ...]


class TextFitPreflightError(ValueError):
    """텍스트 fit preflight 가 차단 결과로 끝났을 때 발생한다."""

    def __init__(self, results: Sequence[BasicTextFitResult]) -> None:
        self.results = tuple(results)
        first = next((result for result in self.results if result.is_blocking), self.results[0])
        super().__init__(
            "basic_text_area 텍스트가 slot 용량을 초과했습니다. "
            f"shape_id={first.shape_id}, status={first.status}, reason={first.reason}"
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
    blocking: list[BasicTextFitResult] = []

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
        raise TextFitPreflightError(results)
    return TextFitPreflightResult(fills=adjusted, results=tuple(results))


def emu_to_pt(value: Any) -> float | None:
    """EMU 값을 pt 로 변환한다."""
    number = _positive_float(value)
    if number is None:
        return None
    return _round_pt(number / EMU_PER_PT)


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
    "TextFitPreflightError",
    "TextFitPreflightResult",
    "TextLayoutEstimate",
    "TextMeasurement",
    "TextWidthBreakdown",
    "apply_text_fit_preflight",
    "emu_to_pt",
    "estimate_text_layout",
    "evaluate_basic_text_area_fit",
    "measure_text_width",
]
