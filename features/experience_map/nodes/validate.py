"""구조화·정제 결과 검증과 보정 loop 제어 (에이전트 문서 5-7)."""

from pydantic import ValidationError as PydanticValidationError

from features.experience_map.config import (
    MAX_BLOCK_LEVEL,
    MAX_CONTENT_LENGTH,
    MAX_VALIDATION_REPAIRS,
)
from features.experience_map.schemas import StructuredItem
from features.experience_map.state import ExperienceMapState, ValidationError


def validate_operations(state: ExperienceMapState) -> ExperienceMapState:
    """정제 결과를 operation metadata와 합치고 위반을 분류한다.

    보정은 최대 두 번만 허용한다. 세 번째 검증에도 남은 위반 item은 개별적으로
    ``dropped_items``에 옮겨 정상 item의 커밋을 막지 않는다.
    """
    updated = dict(state)
    updated["current_node"] = "validate"
    operations, build_errors = _build_operations(state)
    errors = build_errors + _validate_operations(operations, state)

    if not errors:
        updated["validation_errors"] = []
        updated["commit_items"] = operations
        return updated  # type: ignore[return-value]

    if state.get("repair_count", 0) >= MAX_VALIDATION_REPAIRS:
        invalid_ids = {error["item_id"] for error in errors}
        updated["validation_errors"] = []
        updated["commit_items"] = [
            item for item in operations if item["item_id"] not in invalid_ids
        ]
        existing = list(state.get("dropped_items", []))
        existing_ids = {item["item_id"] for item in existing}
        existing.extend(
            {"item_id": item_id, "reason": "validation_retry_exceeded"}
            for item_id in sorted(invalid_ids - existing_ids)
        )
        updated["dropped_items"] = existing
        return updated  # type: ignore[return-value]

    updated["validation_errors"] = errors
    updated["repair_count"] = state.get("repair_count", 0) + 1
    return updated  # type: ignore[return-value]


def _build_operations(state: ExperienceMapState) -> tuple[list[dict], list[ValidationError]]:
    """정제 text를 구조화 metadata에 덮어쓴 뒤 item 집합을 대조한다."""
    metadata = list(state.get("structured_items", []))
    gap_update = state.get("gap_update_item")
    if gap_update:
        metadata.append(gap_update)

    refined = {
        item.get("item_id"): item.get("refined_text") for item in state.get("refined_items", [])
    }
    metadata_ids = [item.get("item_id") for item in metadata]
    errors: list[ValidationError] = []
    if len(metadata_ids) != len(set(metadata_ids)):
        errors.append(
            _error(
                "__operations__",
                "duplicate_item",
                "operation item_id가 중복되었습니다.",
                "structure",
            )
        )
    if set(metadata_ids) != set(refined):
        errors.append(
            _error(
                "__operations__",
                "item_set_mismatch",
                "정제 전후 item 집합이 다릅니다.",
                "structure",
            )
        )

    operations = []
    for item in metadata:
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or item_id not in refined:
            continue
        operation = dict(item)
        operation["text"] = refined[item_id]
        operations.append(operation)
    return operations, errors


def _validate_operations(
    operations: list[dict], state: ExperienceMapState
) -> list[ValidationError]:
    """operation별 schema·alias·권한·content 제약을 검사한다."""
    errors: list[ValidationError] = []
    aliases = state.get("alias_to_block_id", {})
    alias_metadata = state.get("alias_metadata", {})
    seen_items: set[str] = set()
    new_item_levels: dict[str, int] = {}

    for index, raw in enumerate(operations):
        item_id = str(raw.get("item_id", f"__item_{index}"))
        if item_id in seen_items:
            errors.append(
                _error(item_id, "duplicate_item", "item_id가 중복되었습니다.", "structure")
            )
            continue
        seen_items.add(item_id)

        try:
            item = StructuredItem.model_validate(raw)
        except PydanticValidationError:
            errors.append(
                _error(
                    item_id, "operation_schema", "operation 형식이 올바르지 않습니다.", "structure"
                )
            )
            continue

        if item.action == "add":
            parent_level = _validate_add_parent(
                item,
                item_id=item_id,
                aliases=aliases,
                alias_metadata=alias_metadata,
                seen_items=seen_items,
                new_item_levels=new_item_levels,
                errors=errors,
            )
            if parent_level is not None:
                item_level = parent_level + 1
                new_item_levels[item.item_id] = item_level
                _validate_add_level(item, item_id=item_id, item_level=item_level, errors=errors)
            _validate_after_ref(
                item,
                item_id=item_id,
                aliases=aliases,
                alias_metadata=alias_metadata,
                errors=errors,
            )
        else:
            _validate_update_target(
                item,
                item_id=item_id,
                aliases=aliases,
                alias_metadata=alias_metadata,
                errors=errors,
            )

        if item.after_ref is not None and item.after_ref not in aliases:
            errors.append(
                _error(item_id, "unknown_after", "선택 활동 밖 형제 별칭입니다.", "structure")
            )
        text = item.text
        if item.action == "update" and text is None:
            errors.append(
                _error(item_id, "missing_update_content", "수정할 내용이 필요합니다.", "refine")
            )
        elif text is not None and not text.strip():
            errors.append(
                _error(item_id, "empty_content", "내용은 공백만일 수 없습니다.", "refine")
            )
        elif text is not None and len(text.strip()) > MAX_CONTENT_LENGTH:
            errors.append(
                _error(item_id, "content_too_long", "내용이 최대 글자 수를 넘었습니다.", "refine")
            )
    return errors


def _validate_add_parent(
    item: StructuredItem,
    *,
    item_id: str,
    aliases: dict,
    alias_metadata: dict,
    seen_items: set[str],
    new_item_levels: dict[str, int],
    errors: list[ValidationError],
) -> int | None:
    """기존·신규 부모의 존재와 위계를 확인한다."""
    if item.parent_ref is not None:
        if item.parent_ref not in aliases:
            errors.append(
                _error(item_id, "unknown_parent", "선택 활동 밖 부모 별칭입니다.", "structure")
            )
            return None
        parent_metadata = alias_metadata.get(item.parent_ref)
        if parent_metadata is None:
            errors.append(
                _error(
                    item_id,
                    "missing_parent_metadata",
                    "부모 블록의 위계 정보를 확인할 수 없습니다.",
                    "structure",
                )
            )
            return None
        return int(parent_metadata["level"])

    parent_item_id = item.parent_item_id
    if parent_item_id not in seen_items:
        errors.append(
            _error(item_id, "parent_order", "신규 부모는 앞선 item이어야 합니다.", "structure")
        )
        return None
    parent_level = new_item_levels.get(parent_item_id or "")
    if parent_level is None:
        errors.append(
            _error(
                item_id,
                "invalid_new_parent",
                "신규 부모의 위계를 확인할 수 없습니다.",
                "structure",
            )
        )
    return parent_level


def _validate_add_level(
    item: StructuredItem,
    *,
    item_id: str,
    item_level: int,
    errors: list[ValidationError],
) -> None:
    """생성 블록의 최대 위계와 slot·section 위계를 확인한다."""
    if item_level > MAX_BLOCK_LEVEL:
        errors.append(
            _error(
                item_id,
                "max_level_exceeded",
                "level 5 아래에는 블록을 추가할 수 없습니다.",
                "structure",
            )
        )
    if item.section_kind is not None and item_level != 3:
        errors.append(
            _error(
                item_id,
                "invalid_section_level",
                "section_kind는 level 3 카테고리 생성에만 사용할 수 있습니다.",
                "structure",
            )
        )
    if item.slot_id is not None and item_level != item.slot_id.count(".") + 3:
        errors.append(
            _error(
                item_id,
                "slot_level_mismatch",
                "slot_id 형식과 생성 블록의 위계가 일치하지 않습니다.",
                "structure",
            )
        )


def _validate_after_ref(
    item: StructuredItem,
    *,
    item_id: str,
    aliases: dict,
    alias_metadata: dict,
    errors: list[ValidationError],
) -> None:
    """after_ref가 같은 기존 부모 아래의 형제인지 확인한다."""
    if item.after_ref is None or item.after_ref not in aliases:
        return
    after_metadata = alias_metadata.get(item.after_ref)
    if after_metadata is None:
        errors.append(
            _error(
                item_id,
                "missing_after_metadata",
                "after_ref 블록의 부모 정보를 확인할 수 없습니다.",
                "structure",
            )
        )
        return
    if item.parent_ref is None or after_metadata["parent_alias"] != item.parent_ref:
        errors.append(
            _error(
                item_id,
                "after_not_sibling",
                "after_ref는 같은 부모의 기존 형제 블록이어야 합니다.",
                "structure",
            )
        )


def _validate_update_target(
    item: StructuredItem,
    *,
    item_id: str,
    aliases: dict,
    alias_metadata: dict,
    errors: list[ValidationError],
) -> None:
    """수정 대상의 소유권, 위계, 텍스트 편집 권한을 확인한다."""
    if item.target_ref not in aliases:
        errors.append(
            _error(item_id, "unknown_target", "선택 활동 밖 수정 대상 별칭입니다.", "structure")
        )
        return
    target_metadata = alias_metadata.get(item.target_ref or "")
    if target_metadata is None:
        errors.append(
            _error(
                item_id,
                "missing_target_metadata",
                "수정 대상의 위계·편집 권한을 확인할 수 없습니다.",
                "structure",
            )
        )
        return
    if target_metadata["level"] <= 3:
        errors.append(
            _error(
                item_id,
                "protected_level_update",
                "level 1~3 블록은 수정할 수 없습니다.",
                "structure",
            )
        )
    if not target_metadata["is_text_editable"]:
        errors.append(
            _error(
                item_id,
                "not_text_editable",
                "텍스트 편집이 허용되지 않은 블록입니다.",
                "structure",
            )
        )


def _error(item_id: str, code: str, message: str, repair_target: str) -> ValidationError:
    """검증 오류 TypedDict를 한 형식으로 만든다."""
    return {
        "item_id": item_id,
        "code": code,
        "message": message,
        "repair_target": repair_target,  # type: ignore[typeddict-item]
    }


def next_node(state: ExperienceMapState) -> str:
    """검증 결과로 보정·coordinator·fallback 경로를 고른다."""
    errors = state.get("validation_errors", [])
    if errors:
        targets = {error["repair_target"] for error in errors}
        return "structure" if "structure" in targets else "refine"
    return "coordinator" if state.get("commit_items") else "fallback"
