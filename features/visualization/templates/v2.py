"""PPTX 템플릿 v2 metadata/reference JSON 계약."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION_V2 = 2
META_JSON_NAME = "meta.json"
REFERENCE_JSON_NAME = "reference.json"
TEMPLATE_PPTX_NAME = "template.pptx"


@dataclass(frozen=True, slots=True)
class TemplateV2Extraction:
    """v2 compiler 추출 결과.

    PPTX에서 추출한 runtime slide, editable marker slot, reference skeleton 정보를 담는다.
    reference shape 매칭과 layout group 추론은 후속 task 가 이 구조를 확장한다.
    """

    runtime_slides: Sequence[Mapping[str, Any]] = ()
    slots: Sequence[Mapping[str, Any]] = ()
    layout_groups: Sequence[Mapping[str, Any]] = ()
    slide_pairs: Sequence[Mapping[str, Any]] = ()
    shape_matches: Sequence[Mapping[str, Any]] = ()
    errors: Sequence[str] = ()
    warnings: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class TemplateV2Payloads:
    """v2 metadata/reference payload 묶음."""

    metadata: dict[str, Any]
    reference: dict[str, Any]


def build_template_v2_payloads(
    template_id: str,
    extraction: TemplateV2Extraction | None = None,
) -> TemplateV2Payloads:
    """template_id 와 추출 결과로 v2 meta/reference payload 를 만든다."""
    normalized_template_id = _normalize_template_id(template_id)
    source = extraction or TemplateV2Extraction()
    metadata = {
        "schema_version": SCHEMA_VERSION_V2,
        "template_id": normalized_template_id,
        "runtime_slides": _json_array(source.runtime_slides, "runtime_slides"),
        "slots": _json_array(source.slots, "slots"),
        "layout_groups": _json_array(source.layout_groups, "layout_groups"),
    }
    reference = {
        "schema_version": SCHEMA_VERSION_V2,
        "template_id": normalized_template_id,
        "slide_pairs": _json_array(source.slide_pairs, "slide_pairs"),
        "shape_matches": _json_array(source.shape_matches, "shape_matches"),
    }
    return TemplateV2Payloads(metadata=metadata, reference=reference)


def canonical_json_text(payload: Any) -> str:
    """payload 를 deterministic JSON 문자열로 직렬화한다."""
    return (
        json.dumps(
            normalize_json(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def write_json_payload(path: Path | str, payload: Any) -> Path:
    """payload 를 sort-key JSON 파일로 작성한다."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(canonical_json_text(payload), encoding="utf-8")
    return output_path


def read_json_payload(path: Path | str) -> Any:
    """JSON 파일을 읽어 Python 값으로 반환한다."""
    source_path = Path(path)
    try:
        return json.loads(source_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"JSON 파일을 읽을 수 없습니다: {source_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 형식이 올바르지 않습니다: {source_path}") from exc


def normalize_json(value: Any) -> Any:
    """JSON 값을 비교 가능한 canonical 구조로 정규화한다."""
    if isinstance(value, Mapping):
        normalized_items = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"JSON 객체 key는 문자열이어야 합니다: {key!r}")
            normalized_items.append((key, normalize_json(item)))
        return {key: item for key, item in sorted(normalized_items)}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [normalize_json(item) for item in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise ValueError(f"JSON으로 직렬화할 수 없는 값입니다: {type(value).__name__}")


def json_normalized_equal(left: Any, right: Any) -> bool:
    """두 JSON payload 의 의미가 같은지 비교한다."""
    return normalize_json(left) == normalize_json(right)


def json_file_normalized_equal(path: Path | str, expected_payload: Any) -> bool:
    """JSON 파일과 예상 payload 를 normalize 비교한다."""
    return json_normalized_equal(read_json_payload(path), expected_payload)


def _normalize_template_id(template_id: str) -> str:
    normalized = template_id.strip()
    if not normalized:
        raise ValueError("template_id는 비어 있을 수 없습니다.")
    return normalized


def _json_array(items: Sequence[Mapping[str, Any]], field_name: str) -> list[Any]:
    normalized = normalize_json(list(items))
    if not isinstance(normalized, list):
        raise ValueError(f"{field_name} 값은 배열이어야 합니다.")
    return normalized
