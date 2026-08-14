"""구조화·정제 결과 검증과 보정 loop 제어 (에이전트 문서 5-7)."""

from pydantic import ValidationError as PydanticValidationError

from features.experience_map.config import MAX_CONTENT_LENGTH, MAX_VALIDATION_REPAIRS
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
    seen_items: set[str] = set()

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
            if item.parent_ref is not None and item.parent_ref not in aliases:
                errors.append(
                    _error(item_id, "unknown_parent", "선택 활동 밖 부모 별칭입니다.", "structure")
                )
            if item.parent_item_id is not None and item.parent_item_id not in seen_items:
                errors.append(
                    _error(
                        item_id, "parent_order", "신규 부모는 앞선 item이어야 합니다.", "structure"
                    )
                )
        elif item.target_ref not in aliases:
            errors.append(
                _error(item_id, "unknown_target", "선택 활동 밖 수정 대상 별칭입니다.", "structure")
            )

        if item.after_ref is not None and item.after_ref not in aliases:
            errors.append(
                _error(item_id, "unknown_after", "선택 활동 밖 형제 별칭입니다.", "structure")
            )
        if item.action == "update" and item.target_ref == state.get("target_experience_alias"):
            errors.append(
                _error(item_id, "activity_edit", "level 2 활동은 수정할 수 없습니다.", "structure")
            )

        text = item.text
        if text is not None and not text.strip():
            errors.append(
                _error(item_id, "empty_content", "내용은 공백만일 수 없습니다.", "refine")
            )
        elif text is not None and len(text.strip()) > MAX_CONTENT_LENGTH:
            errors.append(
                _error(item_id, "content_too_long", "내용이 최대 글자 수를 넘었습니다.", "refine")
            )
    return errors


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
