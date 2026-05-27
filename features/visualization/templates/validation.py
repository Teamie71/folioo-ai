"""템플릿 meta.json 검증."""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .categories import DEFAULT_CATEGORY_SCHEMA_PATH, CategorySchema, load_category_schema
from .pptx import count_pptx_slides

_REQUIRED_TOP_LEVEL_FIELDS = ("template_id", "template_file", "theme", "slides")
_REQUIRED_THEME_FIELDS = ("primary_color", "name")
_REQUIRED_SLIDE_FIELDS = ("slide_index", "id", "category", "description", "best_for")
_TEMPLATE_FILE_NAME = "template.pptx"
_THUMBNAIL_FILE_NAME = "thumbnail.jpg"


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
) -> TemplateValidationResult:
    """템플릿 디렉터리의 template.pptx/meta.json 무결성을 검증한다."""
    root = Path(template_dir)
    errors: list[str] = []
    warnings: list[str] = []

    _validate_non_empty_file(root / _THUMBNAIL_FILE_NAME, _THUMBNAIL_FILE_NAME, errors)

    meta_path = root / "meta.json"
    metadata = _load_meta_json(meta_path, errors)
    schema = _load_schema(category_schema_path, errors)
    if metadata is None or schema is None:
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
