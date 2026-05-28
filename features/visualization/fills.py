"""슬라이드 fill 상태 병합 유틸리티."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def merge_current_fills(
    current_fills: Mapping[str, Any],
    changes: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """기존 current_fills 에 부분 변경 fill 을 병합한다.

    Args:
        current_fills: 현재 슬라이드에 저장된 shape_id 별 fill 상태.
        changes: 이번 단계에서 적용할 shape_id 별 부분 fill 변경 사항.

    Returns:
        기존 상태에 변경 사항을 반영한 최신 fill 상태.
    """
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
