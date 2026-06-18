"""템플릿 meta.json 검증."""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from features.visualization.text_fit import emu_to_pt

from .categories import DEFAULT_CATEGORY_SCHEMA_PATH, CategorySchema, load_category_schema
from .pptx import count_pptx_slides, extract_template_v2_from_pptx
from .v2 import SCHEMA_VERSION_V2

_REQUIRED_TOP_LEVEL_FIELDS = ("template_id", "template_file", "theme", "slides")
_REQUIRED_THEME_FIELDS = ("primary_color", "name")
_REQUIRED_SLIDE_FIELDS = ("slide_index", "id", "category", "description", "best_for")
_REQUIRED_V2_META_ARRAY_FIELDS = ("runtime_slides", "slots", "layout_groups")
_REQUIRED_V2_REFERENCE_ARRAY_FIELDS = ("slide_pairs", "shape_matches")
_TEMPLATE_FILE_NAME = "template.pptx"
_THUMBNAIL_FILE_NAME = "thumbnail.jpg"
_REFERENCE_FILE_NAME = "reference.json"
_EXACT_MARKER_COLOR = "#FF0000"
_STRICT_EXTRACTION_WARNING_SNIPPETS = (("inline_label_group", "background 신뢰도가 부족"),)
_REFERENCE_MATCH_FRESH_FIELDS = (
    "runtime_shape_id",
    "example_shape_id",
    "example_text",
    "output_text_color",
)
_MIN_EDITABLE_SLOT_WIDTH_PT = 24.0
_MIN_EDITABLE_SLOT_HEIGHT_PT = 10.0
_MIN_LINKED_BACKGROUND_MATCH_SCORE = 0.72
_PLACEHOLDER_RESIDUE_MARKERS = (
    "여기에",
    "placeholder",
    "{{",
    "}}",
)


@dataclass(frozen=True)
class TemplateValidationResult:
    """템플릿 검증 결과."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """검증 실패가 없으면 True."""
        return not self.errors


def validate_template_directory(
    template_dir: Path | str,
    *,
    category_schema_path: Path | str = DEFAULT_CATEGORY_SCHEMA_PATH,
    strict: bool = False,
) -> TemplateValidationResult:
    """템플릿 디렉터리의 template.pptx/meta.json 무결성을 검증한다."""
    root = Path(template_dir)
    errors: list[str] = []
    warnings: list[str] = []

    _validate_non_empty_file(root / _THUMBNAIL_FILE_NAME, _THUMBNAIL_FILE_NAME, errors)

    meta_path = root / "meta.json"
    metadata = _load_meta_json(meta_path, errors)
    if metadata is None:
        return TemplateValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    schema_version = metadata.get("schema_version")
    if schema_version == SCHEMA_VERSION_V2:
        _validate_v2_template(root, metadata, strict=strict, errors=errors, warnings=warnings)
        return TemplateValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    if "schema_version" in metadata:
        errors.append(f"meta.json schema_version은 2여야 합니다. 현재 값: {schema_version!r}")

    schema = _load_schema(category_schema_path, errors)
    if schema is None:
        return TemplateValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    _validate_required_schema(metadata, errors)

    template_file = metadata.get("template_file")
    pptx_slide_count: int | None = None
    if isinstance(template_file, str) and template_file.strip():
        _validate_template_file(template_file, errors)
        if template_file == _TEMPLATE_FILE_NAME:
            pptx_path = root / _TEMPLATE_FILE_NAME
            try:
                pptx_slide_count = count_pptx_slides(pptx_path)
            except ValueError as exc:
                errors.append(str(exc))

    slides = metadata.get("slides")
    if isinstance(slides, list):
        _validate_slides(slides, schema, pptx_slide_count, errors, warnings)

    return TemplateValidationResult(errors=tuple(errors), warnings=tuple(warnings))


def _validate_v2_template(
    root: Path,
    metadata: dict[str, Any],
    *,
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    _validate_non_empty_file(root / _TEMPLATE_FILE_NAME, _TEMPLATE_FILE_NAME, errors)
    _validate_v2_meta_schema(metadata, errors)

    reference = _load_optional_json(root / _REFERENCE_FILE_NAME, _REFERENCE_FILE_NAME, errors)
    if reference is not None:
        _validate_v2_reference_schema(reference, metadata, errors)

    extraction = None
    template_path = root / _TEMPLATE_FILE_NAME
    if template_path.is_file() and _file_has_content(template_path):
        try:
            extraction = extract_template_v2_from_pptx(template_path)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            errors.extend(extraction.errors)
            _append_extraction_warnings(extraction.warnings, strict, errors, warnings)

    _validate_v2_runtime_contract(metadata, reference, extraction, errors)
    _validate_v2_slot_contract(metadata, reference, extraction, strict, errors, warnings)
    _validate_v2_layout_groups(metadata, strict, errors, warnings)


def _validate_v2_meta_schema(metadata: dict[str, Any], errors: list[str]) -> None:
    if metadata.get("schema_version") != SCHEMA_VERSION_V2:
        errors.append(
            f"meta.json schema_version은 2여야 합니다. 현재 값: {metadata.get('schema_version')!r}"
        )
    _validate_required_string(metadata.get("template_id"), "meta.json.template_id", errors)
    for field in _REQUIRED_V2_META_ARRAY_FIELDS:
        if not isinstance(metadata.get(field), list):
            errors.append(f"meta.json.{field} 필드는 배열이어야 합니다.")


def _validate_v2_reference_schema(
    reference: dict[str, Any],
    metadata: dict[str, Any],
    errors: list[str],
) -> None:
    if reference.get("schema_version") != SCHEMA_VERSION_V2:
        errors.append(
            "reference.json schema_version은 2여야 합니다. "
            f"현재 값: {reference.get('schema_version')!r}"
        )
    _validate_required_string(reference.get("template_id"), "reference.json.template_id", errors)
    if (
        isinstance(reference.get("template_id"), str)
        and isinstance(metadata.get("template_id"), str)
        and reference.get("template_id") != metadata.get("template_id")
    ):
        errors.append("reference.json.template_id가 meta.json.template_id와 일치하지 않습니다.")
    for field in _REQUIRED_V2_REFERENCE_ARRAY_FIELDS:
        if not isinstance(reference.get(field), list):
            errors.append(f"reference.json.{field} 필드는 배열이어야 합니다.")
    if "shape_inferences" in reference and not isinstance(reference.get("shape_inferences"), list):
        errors.append("reference.json.shape_inferences 필드는 배열이어야 합니다.")


def _validate_v2_runtime_contract(
    metadata: dict[str, Any],
    reference: dict[str, Any] | None,
    extraction: Any,
    errors: list[str],
) -> None:
    runtime_slides = _list_value(metadata.get("runtime_slides"))
    if runtime_slides is None:
        return
    if not runtime_slides:
        errors.append("meta.json runtime_slides에 runtime 대상 슬라이드가 없습니다.")

    runtime_indexes: set[int] = set()
    runtime_parts: set[str] = set()
    for position, slide in enumerate(runtime_slides):
        label = f"meta.json.runtime_slides[{position}]"
        if not isinstance(slide, dict):
            errors.append(f"{label} 항목은 객체여야 합니다.")
            continue
        slide_index = _required_int(slide.get("slide_index"), f"{label}.slide_index", errors)
        slide_number = _required_int(slide.get("slide_number"), f"{label}.slide_number", errors)
        slide_part = _required_string(slide.get("slide_part"), f"{label}.slide_part", errors)
        _required_string(slide.get("slide_filename"), f"{label}.slide_filename", errors)
        if slide_index is not None:
            runtime_indexes.add(slide_index)
        if slide_part is not None:
            runtime_parts.add(slide_part)
        if slide_number is not None and slide_number % 2 != 0:
            errors.append(f"{label}에 example slide가 runtime 후보로 포함되어 있습니다.")

    if extraction is not None:
        extracted_runtime_parts = {
            str(slide.get("slide_part"))
            for slide in extraction.runtime_slides
            if isinstance(slide, dict) and slide.get("slide_part")
        }
        missing_parts = sorted(runtime_parts - extracted_runtime_parts)
        if missing_parts:
            errors.append(
                "meta.json.runtime_slides에 template.pptx runtime 대상이 아닌 슬라이드가 "
                f"포함되어 있습니다: {', '.join(missing_parts)}"
            )

    if reference is not None:
        _validate_v2_slide_pairs(reference, runtime_indexes, errors)


def _validate_v2_slide_pairs(
    reference: dict[str, Any],
    runtime_indexes: set[int],
    errors: list[str],
) -> None:
    slide_pairs = _list_value(reference.get("slide_pairs"))
    if slide_pairs is None:
        return

    pair_runtime_indexes: set[int] = set()
    pair_example_indexes: set[int] = set()
    for position, pair in enumerate(slide_pairs):
        label = f"reference.json.slide_pairs[{position}]"
        if not isinstance(pair, dict):
            errors.append(f"{label} 항목은 객체여야 합니다.")
            continue
        runtime_index = _required_int(
            pair.get("runtime_slide_index"), f"{label}.runtime_slide_index", errors
        )
        example_index = _required_int(
            pair.get("example_slide_index"), f"{label}.example_slide_index", errors
        )
        if runtime_index is not None:
            pair_runtime_indexes.add(runtime_index)
        if example_index is not None:
            pair_example_indexes.add(example_index)
        if (
            runtime_index is not None
            and example_index is not None
            and runtime_index == example_index
        ):
            errors.append(f"{label}의 runtime/example slide가 동일합니다.")

    missing_pairs = sorted(runtime_indexes - pair_runtime_indexes)
    if missing_pairs:
        errors.append(
            "reference.json에 필수 example slide pair가 없습니다. "
            f"runtime_slide_index: {missing_pairs}"
        )

    leaked_examples = sorted(runtime_indexes & pair_example_indexes)
    if leaked_examples:
        errors.append(
            f"example slide가 runtime 후보에 포함되어 있습니다. slide_index: {leaked_examples}"
        )


def _validate_v2_slot_contract(
    metadata: dict[str, Any],
    reference: dict[str, Any] | None,
    extraction: Any,
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    slots = _list_value(metadata.get("slots"))
    if slots is None:
        return

    slot_ids: list[str] = []
    editable_text_slot_ids: set[str] = set()
    reference_required_slot_ids: set[str] = set()
    for position, slot in enumerate(slots):
        label = f"meta.json.slots[{position}]"
        if not isinstance(slot, dict):
            errors.append(f"{label} 항목은 객체여야 합니다.")
            continue
        slot_id = _required_string(slot.get("slot_id"), f"{label}.slot_id", errors)
        _required_string(slot.get("shape_id"), f"{label}.shape_id", errors)
        if slot_id is not None:
            slot_ids.append(slot_id)

        if _is_editable_text_slot(slot):
            if slot_id is not None:
                editable_text_slot_ids.add(slot_id)
                if _requires_v2_reference_match(slot):
                    reference_required_slot_ids.add(slot_id)
            _validate_v2_editable_text_slot(slot, label, strict, errors, warnings)

    duplicate_slot_ids = sorted({slot_id for slot_id in slot_ids if slot_ids.count(slot_id) > 1})
    if duplicate_slot_ids:
        errors.append(f"meta.json slot_id 중복: {', '.join(duplicate_slot_ids)}")

    if extraction is not None:
        extracted_slot_ids = {
            str(slot.get("slot_id"))
            for slot in extraction.slots
            if isinstance(slot, dict) and slot.get("slot_id")
        }
        metadata_slot_ids = set(slot_ids)
        missing_slot_ids = sorted(extracted_slot_ids - metadata_slot_ids)
        extra_slot_ids = sorted(editable_text_slot_ids - extracted_slot_ids)
        if missing_slot_ids:
            errors.append(
                "meta.json.slots에 template.pptx editable marker slot이 누락되었습니다: "
                f"{', '.join(missing_slot_ids)}"
            )
        if extra_slot_ids:
            errors.append(
                "meta.json.slots에 template.pptx #FF0000 marker가 아닌 editable slot이 "
                f"포함되어 있습니다: {', '.join(extra_slot_ids)}"
            )

    if reference is not None:
        _validate_v2_reference_matches(reference, reference_required_slot_ids, errors)
        if extraction is not None:
            _validate_v2_reference_matches_against_extraction(reference, extraction, errors)


def _requires_v2_reference_match(slot: dict[str, Any]) -> bool:
    return slot.get("text_replacement_mode") != "marker_runs"


def _validate_v2_editable_text_slot(
    slot: dict[str, Any],
    label: str,
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    _required_string(slot.get("placeholder_text"), f"{label}.placeholder_text", errors)
    marker_color = str(slot.get("marker_color") or "").upper()
    if marker_color != _EXACT_MARKER_COLOR:
        errors.append(f"{label}.marker_color는 정확한 #FF0000 이어야 합니다.")

    replacement_mode = slot.get("text_replacement_mode")
    if replacement_mode is not None and replacement_mode not in {"marker_runs", "shape"}:
        errors.append(f"{label}.text_replacement_mode은 marker_runs 또는 shape 이어야 합니다.")

    layout_name = _editable_slot_layout_name(slot)
    if layout_name == "unknown":
        _append_warning_or_strict_error(
            f"{label} editable slot layout이 unknown입니다.",
            strict,
            errors,
            warnings,
        )
    _validate_v2_editable_slot_quality(slot, label, strict, errors, warnings)


def _validate_v2_editable_slot_quality(
    slot: dict[str, Any],
    label: str,
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    _validate_v2_editable_slot_size(slot, label, strict, errors, warnings)
    _validate_v2_placeholder_residue(slot, label, strict, errors, warnings)


def _validate_v2_editable_slot_size(
    slot: dict[str, Any],
    label: str,
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    width_emu = _numeric_emu(slot.get("w_emu"))
    height_emu = _numeric_emu(slot.get("h_emu"))
    if width_emu is None or height_emu is None:
        return

    shape_id = str(slot.get("shape_id") or "").strip() or "unknown"
    if width_emu <= 0 or height_emu <= 0:
        _append_warning_or_strict_error(
            f"{label} shape_id={shape_id} editable slot geometry가 유효하지 않습니다. "
            f"w_emu={width_emu:g}, h_emu={height_emu:g}.",
            strict,
            errors,
            warnings,
        )
        return

    width_pt = emu_to_pt(width_emu)
    height_pt = emu_to_pt(height_emu)
    if width_pt is None or height_pt is None:
        return
    if width_pt >= _MIN_EDITABLE_SLOT_WIDTH_PT and height_pt >= _MIN_EDITABLE_SLOT_HEIGHT_PT:
        return

    _append_warning_or_strict_error(
        f"{label} shape_id={shape_id} editable slot이 좁습니다. "
        f"width_pt={width_pt:.2f}, height_pt={height_pt:.2f}.",
        strict,
        errors,
        warnings,
    )


def _validate_v2_placeholder_residue(
    slot: dict[str, Any],
    label: str,
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    placeholder_text = _normalized_text(slot.get("placeholder_text"))
    example_text = _normalized_text(slot.get("example_text"))
    if not placeholder_text or not example_text:
        return
    if example_text != placeholder_text and not any(
        marker.casefold() in example_text.casefold() for marker in _PLACEHOLDER_RESIDUE_MARKERS
    ):
        return

    shape_id = str(slot.get("shape_id") or "").strip() or "unknown"
    _append_warning_or_strict_error(
        f"{label} shape_id={shape_id} placeholder 잔존 위험이 있습니다.",
        strict,
        errors,
        warnings,
    )


def _validate_v2_reference_matches(
    reference: dict[str, Any],
    editable_text_slot_ids: set[str],
    errors: list[str],
) -> None:
    shape_matches = _list_value(reference.get("shape_matches"))
    if shape_matches is None:
        return

    matched_slot_ids: set[str] = set()
    for position, match in enumerate(shape_matches):
        label = f"reference.json.shape_matches[{position}]"
        if not isinstance(match, dict):
            errors.append(f"{label} 항목은 객체여야 합니다.")
            continue
        slot_id = _required_string(match.get("slot_id"), f"{label}.slot_id", errors)
        _required_string(match.get("example_shape_id"), f"{label}.example_shape_id", errors)
        _required_string(match.get("example_text"), f"{label}.example_text", errors)
        if slot_id is not None:
            matched_slot_ids.add(slot_id)

    unmatched_slot_ids = sorted(editable_text_slot_ids - matched_slot_ids)
    if unmatched_slot_ids:
        errors.append(
            "editable slot의 example shape 매칭에 실패했습니다. "
            f"slot_id: {', '.join(unmatched_slot_ids)}"
        )


def _validate_v2_reference_matches_against_extraction(
    reference: dict[str, Any],
    extraction: Any,
    errors: list[str],
) -> None:
    shape_matches = _list_value(reference.get("shape_matches"))
    if shape_matches is None:
        return

    extracted_by_slot_id: dict[str, dict[str, Any]] = {}
    for extracted_match in extraction.shape_matches:
        if not isinstance(extracted_match, dict):
            continue
        slot_id = extracted_match.get("slot_id")
        if not isinstance(slot_id, str) or not slot_id.strip():
            continue
        extracted_by_slot_id[slot_id] = extracted_match

    for position, match in enumerate(shape_matches):
        label = f"reference.json.shape_matches[{position}]"
        if not isinstance(match, dict):
            continue
        slot_id = match.get("slot_id")
        if not isinstance(slot_id, str) or not slot_id.strip():
            continue

        extracted_match = extracted_by_slot_id.get(slot_id)
        if extracted_match is None:
            errors.append(f"{label}.slot_id {slot_id!r}는 template.pptx 추출 결과에 없습니다.")
            continue

        for field in _REFERENCE_MATCH_FRESH_FIELDS:
            reference_value = match.get(field)
            extracted_value = extracted_match.get(field)
            if reference_value != extracted_value:
                errors.append(
                    f"{label}.{field}가 template.pptx 추출 결과와 일치하지 않습니다. "
                    f"reference.json={reference_value!r}, template.pptx={extracted_value!r}"
                )


def _validate_v2_layout_groups(
    metadata: dict[str, Any],
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    layout_groups = _list_value(metadata.get("layout_groups"))
    if layout_groups is None:
        return

    for position, group in enumerate(layout_groups):
        label = f"meta.json.layout_groups[{position}]"
        if not isinstance(group, dict):
            errors.append(f"{label} 항목은 객체여야 합니다.")
            continue
        if str(group.get("layout_type") or "") != "inline_label_group":
            continue
        if not _inline_label_group_linked_backgrounds_are_confident(group):
            _append_warning_or_strict_error(
                f"{label} inline_label_group linked background 신뢰도가 낮습니다.",
                strict,
                errors,
                warnings,
            )


def _inline_label_group_linked_backgrounds_are_confident(group: dict[str, Any]) -> bool:
    item_shape_ids = _string_list(group.get("item_shape_ids"))
    linked_background_by_item = group.get("linked_background_by_item")
    if not item_shape_ids or not isinstance(linked_background_by_item, dict):
        return False
    for item_shape_id in item_shape_ids:
        linked = linked_background_by_item.get(item_shape_id)
        if not isinstance(linked, dict):
            return False
        if linked.get("resize_linked") is not True:
            return False
        if not str(linked.get("background_shape_id") or "").strip():
            return False
        match_score = linked.get("match_score")
        if isinstance(match_score, bool) or not isinstance(match_score, int | float):
            return False
        if match_score < _MIN_LINKED_BACKGROUND_MATCH_SCORE:
            return False
    return True


def _append_extraction_warnings(
    extraction_warnings: Sequence[str],
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    for warning in extraction_warnings:
        if strict and _is_strict_extraction_warning(warning):
            errors.append(warning)
            continue
        warnings.append(warning)


def _is_strict_extraction_warning(warning: str) -> bool:
    return any(
        all(snippet in warning for snippet in snippets)
        for snippets in _STRICT_EXTRACTION_WARNING_SNIPPETS
    )


def _load_optional_json(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"{label}을 읽을 수 없습니다: {exc}")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"{label} JSON 형식이 올바르지 않습니다: {exc}")
        return None

    if not isinstance(loaded, dict):
        errors.append(f"{label} 최상위 값은 객체여야 합니다.")
        return None
    return loaded


def _load_meta_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"meta.json을 읽을 수 없습니다: {exc}")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"meta.json JSON 형식이 올바르지 않습니다: {exc}")
        return None

    if not isinstance(loaded, dict):
        errors.append("meta.json 최상위 값은 객체여야 합니다.")
        return None
    return loaded


def _load_schema(path: Path | str, errors: list[str]) -> CategorySchema | None:
    try:
        return load_category_schema(path)
    except ValueError as exc:
        errors.append(str(exc))
        return None


def _validate_non_empty_file(path: Path, label: str, errors: list[str]) -> None:
    try:
        if not path.is_file():
            errors.append(f"{label} 파일을 찾을 수 없습니다: {path}")
            return
        if path.stat().st_size == 0:
            errors.append(f"{label} 파일이 비어 있습니다: {path}")
    except OSError as exc:
        errors.append(f"{label} 파일을 확인할 수 없습니다: {exc}")


def _file_has_content(path: Path) -> bool:
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _validate_required_schema(metadata: dict[str, Any], errors: list[str]) -> None:
    for field in _REQUIRED_TOP_LEVEL_FIELDS:
        if field not in metadata:
            errors.append(f"필수 필드 누락: {field}")

    theme = metadata.get("theme")
    if "theme" in metadata and not isinstance(theme, dict):
        errors.append("theme 필드는 객체여야 합니다.")
    elif isinstance(theme, dict):
        for field in _REQUIRED_THEME_FIELDS:
            _validate_required_string(theme.get(field), f"theme.{field}", errors)

    slides = metadata.get("slides")
    if "slides" in metadata and not isinstance(slides, list):
        errors.append("slides 필드는 배열이어야 합니다.")

    for field in ("template_id", "template_file"):
        if field in metadata:
            _validate_required_string(metadata.get(field), field, errors)


def _validate_template_file(value: str, errors: list[str]) -> None:
    if value != _TEMPLATE_FILE_NAME:
        errors.append("template_file은 경로 없이 template.pptx여야 합니다.")


def _validate_slides(
    slides: list[Any],
    schema: CategorySchema,
    pptx_slide_count: int | None,
    errors: list[str],
    warnings: list[str],
) -> None:
    slide_indexes: list[int] = []
    ids: list[str] = []
    categories: list[str] = []

    for position, raw_slide in enumerate(slides):
        label = f"slides[{position}]"
        if not isinstance(raw_slide, dict):
            errors.append(f"{label} 항목은 객체여야 합니다.")
            continue

        for field in _REQUIRED_SLIDE_FIELDS:
            if field not in raw_slide or raw_slide.get(field) is None:
                errors.append(f"필수 필드 누락: {label}.{field}")

        for field in ("id", "category", "description", "best_for"):
            _validate_required_string(raw_slide.get(field), f"{label}.{field}", errors)

        slide_index = raw_slide.get("slide_index")
        if isinstance(slide_index, bool) or not isinstance(slide_index, int):
            errors.append(f"{label}.slide_index는 정수여야 합니다.")
        else:
            slide_indexes.append(slide_index)

        slide_id = raw_slide.get("id")
        if isinstance(slide_id, str):
            ids.append(slide_id)

        category = raw_slide.get("category")
        if isinstance(category, str):
            categories.append(category)
            if category == "unknown":
                errors.append(f"{label}.category는 unknown일 수 없습니다.")
            elif category not in schema.key_set:
                errors.append(f"{label}.category가 표준 Enum 밖입니다: {category}")

    expected_indexes = list(range(len(slides)))
    if slide_indexes != expected_indexes:
        errors.append(
            "slide_index는 slides 배열 순서대로 0..N-1 연속 값이어야 합니다. "
            f"(기대: {expected_indexes}, 실제: {slide_indexes})"
        )

    if pptx_slide_count is not None and pptx_slide_count != len(slides):
        errors.append(
            "meta.json slides 수가 PPTX 슬라이드 수와 일치하지 않습니다. "
            f"(meta.json: {len(slides)}, PPTX: {pptx_slide_count})"
        )

    duplicate_ids = sorted({slide_id for slide_id in ids if ids.count(slide_id) > 1})
    if duplicate_ids:
        errors.append(f"템플릿 내 id 중복: {', '.join(duplicate_ids)}")

    _append_distribution_warnings(categories, schema, warnings)


def _append_distribution_warnings(
    categories: list[str],
    schema: CategorySchema,
    warnings: list[str],
) -> None:
    counts = Counter(category for category in categories if category in schema.key_set)
    for definition in schema.definitions:
        count = counts.get(definition.key, 0)
        if definition.recommended_min is not None and count < definition.recommended_min:
            warnings.append(
                f"{definition.key} 카테고리 수가 권장 최소보다 적습니다. "
                f"(현재: {count}, 권장: {definition.recommended_min} 이상)"
            )
        if definition.recommended_max is not None and count > definition.recommended_max:
            warnings.append(
                f"{definition.key} 카테고리 수가 권장 최대보다 많습니다. "
                f"(현재: {count}, 권장: {definition.recommended_max} 이하)"
            )


def _validate_required_string(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}는 비어 있지 않은 문자열이어야 합니다.")


def _required_string(value: Any, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}는 비어 있지 않은 문자열이어야 합니다.")
        return None
    return value


def _required_int(value: Any, label: str, errors: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label}는 정수여야 합니다.")
        return None
    return value


def _list_value(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _normalized_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _numeric_emu(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number == number or number in (float("inf"), float("-inf")):
        return None
    return number


def _is_editable_text_slot(slot: dict[str, Any]) -> bool:
    if slot.get("editable") is False:
        return False
    return str(slot.get("kind") or "text").casefold() == "text"


def _editable_slot_layout_name(slot: dict[str, Any]) -> str:
    for field in ("layout_type", "fit_policy"):
        value = slot.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return ""


def _append_warning_or_strict_error(
    message: str,
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    if strict:
        errors.append(message)
        return
    warnings.append(message)
