"""슬라이드 fill 상태 병합 유틸리티."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def merge_current_fills(
    current_fills: Mapping[str, Any],
    changes: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """기존 current_fills 에 부분 변경 fill 을 병합한다."""
    merged: dict[str, Any] = {}
    for shape_id, fill in current_fills.items():
        merged[str(shape_id)] = dict(fill) if isinstance(fill, Mapping) else fill

    for shape_id, fill in changes.items():
        shape_key = str(shape_id)
        if fill.get("action") == "remove":
            merged.pop(shape_key, None)
            continue
        merged[shape_key] = dict(fill)
    return merged
