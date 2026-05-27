"""Source Slide 표준 카테고리 스키마 로더."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CATEGORY_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "templates" / "_schema" / "categories.json"
)


@dataclass(frozen=True)
class CategoryDefinition:
    """Source Slide 카테고리 정의."""

    key: str
    description: str = ""
    recommended_min: int | None = None
    recommended_max: int | None = None


@dataclass(frozen=True)
class CategorySchema:
    """카테고리 Enum 과 권장 분포 정보를 담는 스키마."""

    definitions: tuple[CategoryDefinition, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        """스키마에 등록된 카테고리 키 목록."""
        return tuple(definition.key for definition in self.definitions)

    @property
    def key_set(self) -> frozenset[str]:
        """카테고리 키 set."""
        return frozenset(self.keys)

    def definition_for(self, key: str) -> CategoryDefinition | None:
        """카테고리 키에 맞는 정의를 반환한다."""
        for definition in self.definitions:
            if definition.key == key:
                return definition
        return None


def load_category_schema(path: Path | str = DEFAULT_CATEGORY_SCHEMA_PATH) -> CategorySchema:
    """카테고리 스키마 JSON 파일을 로드한다."""
    schema_path = Path(path)
    try:
        raw = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"카테고리 스키마 파일을 읽을 수 없습니다: {schema_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"카테고리 스키마 JSON 형식이 올바르지 않습니다: {schema_path}") from exc

    raw_categories = raw.get("categories") if isinstance(raw, dict) else raw
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ValueError("카테고리 스키마에는 비어 있지 않은 categories 배열이 필요합니다.")

    definitions = tuple(_parse_category(item) for item in raw_categories)
    keys = [definition.key for definition in definitions]
    duplicate_keys = sorted({key for key in keys if keys.count(key) > 1})
    if duplicate_keys:
        raise ValueError(f"카테고리 스키마에 중복 key가 있습니다: {', '.join(duplicate_keys)}")

    return CategorySchema(definitions=definitions)


def _parse_category(item: Any) -> CategoryDefinition:
    if isinstance(item, str):
        key = item.strip()
        if not key:
            raise ValueError("카테고리 key는 비어 있을 수 없습니다.")
        return CategoryDefinition(key=key)

    if not isinstance(item, dict):
        raise ValueError("categories 항목은 문자열 또는 객체여야 합니다.")

    key = str(item.get("key", "")).strip()
    if not key:
        raise ValueError("카테고리 key는 비어 있을 수 없습니다.")

    recommended_min = _optional_int(item.get("recommended_min"), field_name="recommended_min")
    recommended_max = _optional_int(item.get("recommended_max"), field_name="recommended_max")
    if (
        recommended_min is not None
        and recommended_max is not None
        and recommended_min > recommended_max
    ):
        raise ValueError(f"{key} 카테고리의 recommended_min이 recommended_max보다 큽니다.")

    return CategoryDefinition(
        key=key,
        description=str(item.get("description", "")).strip(),
        recommended_min=recommended_min,
        recommended_max=recommended_max,
    )


def _optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} 값은 정수여야 합니다.")
    if value < 0:
        raise ValueError(f"{field_name} 값은 0 이상이어야 합니다.")
    return value
