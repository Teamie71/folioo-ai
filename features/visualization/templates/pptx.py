"""PPTX 패키지에서 슬라이드 순서와 텍스트를 추출하는 유틸리티."""

import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from .v2 import TemplateV2Extraction

_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SLIDE_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)
_EXACT_MARKER_RGB = "FF0000"
_EXACT_MARKER_COLOR = "#FF0000"
_REFERENCE_COLOR_FALLBACK = "#000000"
_REFERENCE_MATCH_FAIL_SCORE = 0.3
_REFERENCE_MATCH_LOW_CONFIDENCE_SCORE = 0.75
_ITEM_BACKGROUND_MIN_SCORE = 0.72
_ITEM_BACKGROUND_MIN_SLOT_COVERAGE = 0.75
_ITEM_BACKGROUND_AMBIGUOUS_SLOT_COVERAGE = 0.45
_ITEM_BACKGROUND_MAX_WIDTH_RATIO = 2.5
_ITEM_BACKGROUND_MAX_HEIGHT_RATIO = 2.5
_ITEM_BACKGROUND_MAX_AREA_RATIO = 5.0
_CONTAINER_MIN_SLOT_COVERAGE = 0.85
_CONTAINER_MIN_SLOT_COUNT = 2
_CONTAINER_MIN_MAX_SLOT_AREA_RATIO = 3.0
_CONTAINER_MIN_UNION_AREA_RATIO = 1.05
_CONTAINER_MAX_UNION_AREA_RATIO = 8.0
_INLINE_LABEL_GROUP_MIN_ITEMS = 3
_INLINE_LABEL_GROUP_MAX_PLACEHOLDER_CHARS = 28
_INLINE_LABEL_GROUP_MIN_HEIGHT_SIMILARITY = 0.65
_INLINE_LABEL_GROUP_MAX_WIDTH_RATIO = 5.0
_INLINE_LABEL_GROUP_CENTER_Y_TOLERANCE_RATIO = 0.35
_INLINE_LABEL_GROUP_MAX_GAP_HEIGHT_RATIO = 2.0
_INLINE_LABEL_GROUP_MAX_GAP_WIDTH_RATIO = 0.75
_INLINE_LABEL_GROUP_MAX_GAP_VARIANCE_RATIO = 3.0
_INLINE_LABEL_GROUP_MIN_BACKGROUND_ITEMS = 2


@dataclass(frozen=True)
class SlideText:
    """PPTX 슬라이드별 임시 텍스트."""

    slide_index: int
    text: str


@dataclass(frozen=True, slots=True)
class _MarkerShapeExtraction:
    """단일 runtime slide의 marker shape 추출 결과."""

    slots: tuple[dict, ...]
    errors: tuple[str, ...]
    has_marker_candidate: bool


@dataclass(frozen=True, slots=True)
class _TextShapeExtraction:
    """예시 슬라이드 text shape 추출 결과."""

    shape_id: str
    shape_name: str
    x_emu: int | None
    y_emu: int | None
    w_emu: int | None
    h_emu: int | None
    text: str
    line_count: int
    char_count: int
    output_text_color: str | None


@dataclass(frozen=True, slots=True)
class _RuntimeShapeGeometry:
    """runtime slide의 무텍스트 shape geometry."""

    shape_id: str
    shape_name: str
    x_emu: int
    y_emu: int
    w_emu: int
    h_emu: int


def count_pptx_slides(pptx_path: Path | str) -> int:
    """PPTX 내부 Source Slide 개수를 반환한다."""
    return len(_ordered_slide_part_names(Path(pptx_path)))


def extract_slide_texts(pptx_path: Path | str) -> tuple[SlideText, ...]:
    """PPTX 슬라이드 XML에서 슬라이드별 텍스트를 순서대로 추출한다."""
    source = Path(pptx_path)
    slide_names = _ordered_slide_part_names(source)
    slide_texts: list[SlideText] = []

    with _open_pptx(source) as zf:
        for index, slide_name in enumerate(slide_names):
            try:
                slide_xml = zf.read(slide_name)
            except KeyError as exc:
                raise ValueError(f"PPTX 슬라이드 XML을 찾을 수 없습니다: {slide_name}") from exc

            root = _parse_xml(slide_xml, f"{source}:{slide_name}")
            texts = [
                (text_node.text or "").strip()
                for text_node in root.findall(f".//{{{_DRAWINGML_NS}}}t")
                if (text_node.text or "").strip()
            ]
            slide_texts.append(SlideText(slide_index=index, text="\n".join(texts)))

    return tuple(slide_texts)


def extract_template_v2_from_pptx(pptx_path: Path | str) -> TemplateV2Extraction:
    """PPTX convention에서 v2 runtime slide pair와 marker slot을 추출한다.

    1-based 짝수 슬라이드를 runtime 유형 슬라이드로 보고, 바로 뒤 1-based 홀수
    슬라이드를 example slide로 매칭한다. 첫 안내 슬라이드나 example slide의 red text는
    runtime editable slot 후보로 보지 않는다.
    """
    source = Path(pptx_path)
    slide_names = _ordered_slide_part_names(source)
    slide_size = _slide_size_emu(source)
    slide_width_emu = slide_size[0] if slide_size is not None else None
    runtime_slides: list[dict] = []
    slide_pairs: list[dict] = []
    shape_matches: list[dict] = []
    shape_inferences: list[dict] = []
    layout_groups: list[dict] = []
    slots: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []

    with _open_pptx(source) as zf:
        for slide_position, slide_name in enumerate(slide_names):
            slide_number = slide_position + 1
            if slide_number % 2 != 0:
                continue

            example_position = slide_position + 1
            if example_position >= len(slide_names):
                errors.append(
                    f"runtime slide {slide_number}의 example slide pair를 찾을 수 없습니다."
                )
                continue

            example_name = slide_names[example_position]
            runtime_slide = {
                "slide_index": slide_position,
                "slide_number": slide_number,
                "slide_filename": Path(slide_name).name,
                "slide_part": slide_name,
            }
            runtime_slides.append(runtime_slide)
            slide_pairs.append(
                {
                    "runtime_slide_index": slide_position,
                    "runtime_slide_number": slide_number,
                    "runtime_slide_filename": Path(slide_name).name,
                    "runtime_slide_part": slide_name,
                    "example_slide_index": example_position,
                    "example_slide_number": example_position + 1,
                    "example_slide_filename": Path(example_name).name,
                    "example_slide_part": example_name,
                }
            )

            try:
                slide_xml = zf.read(slide_name)
            except KeyError as exc:
                raise ValueError(f"PPTX 슬라이드 XML을 찾을 수 없습니다: {slide_name}") from exc

            root = _parse_xml(slide_xml, f"{source}:{slide_name}")
            marker_result = _extract_marker_slots_from_slide(
                root,
                slide_index=slide_position,
                slide_number=slide_number,
                slide_part=slide_name,
            )
            slots.extend(marker_result.slots)
            errors.extend(marker_result.errors)
            inferred_shapes, shape_warnings = _infer_runtime_shape_relationships(
                marker_result.slots,
                _extract_non_text_shapes_from_slide(root),
                runtime_slide_number=slide_number,
            )
            shape_inferences.extend(inferred_shapes)
            warnings.extend(shape_warnings)
            inferred_groups, group_warnings = _infer_inline_label_groups(
                marker_result.slots,
                inferred_shapes,
                runtime_slide_number=slide_number,
                slide_width_emu=slide_width_emu,
            )
            layout_groups.extend(inferred_groups)
            warnings.extend(group_warnings)
            if not marker_result.slots and not marker_result.has_marker_candidate:
                errors.append(
                    f"runtime slide {slide_number}에 정확한 #FF0000 editable marker가 없습니다."
                )

            try:
                example_xml = zf.read(example_name)
            except KeyError as exc:
                raise ValueError(f"PPTX 슬라이드 XML을 찾을 수 없습니다: {example_name}") from exc

            example_root = _parse_xml(example_xml, f"{source}:{example_name}")
            match_results = _match_reference_shapes(
                marker_result.slots,
                _extract_text_shapes_from_slide(example_root),
                runtime_slide_number=slide_number,
                example_slide_index=example_position,
                example_slide_number=example_position + 1,
                example_slide_part=example_name,
            )
            shape_matches.extend(match_results[0])
            errors.extend(match_results[1])
            warnings.extend(match_results[2])

    if not runtime_slides:
        errors.append("runtime 대상 슬라이드가 없습니다.")

    return TemplateV2Extraction(
        runtime_slides=tuple(runtime_slides),
        slots=tuple(slots),
        layout_groups=tuple(layout_groups),
        slide_pairs=tuple(slide_pairs),
        shape_matches=tuple(shape_matches),
        shape_inferences=tuple(shape_inferences),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _ordered_slide_part_names(pptx_path: Path) -> tuple[str, ...]:
    if pptx_path.suffix.lower() != ".pptx":
        raise ValueError(f"PPTX 파일만 처리할 수 있습니다: {pptx_path}")

    with _open_pptx(pptx_path) as zf:
        names = set(zf.namelist())
        if "ppt/presentation.xml" not in names:
            raise ValueError("PPTX에 ppt/presentation.xml이 없습니다.")

        try:
            presentation_root = _parse_xml(
                zf.read("ppt/presentation.xml"),
                f"{pptx_path}:ppt/presentation.xml",
            )
        except KeyError as exc:
            raise ValueError("PPTX에 ppt/presentation.xml이 없습니다.") from exc

        rid_to_target = _load_slide_relationships(zf, pptx_path)
        slide_names: list[str] = []
        for slide_id in presentation_root.findall(f".//{{{_PRESENTATION_NS}}}sldId"):
            rid = slide_id.attrib.get(f"{{{_REL_NS}}}id")
            if not rid:
                raise ValueError("PPTX slide id에 relationship id가 없습니다.")
            target = rid_to_target.get(rid)
            if target is None:
                raise ValueError(f"PPTX presentation relationship을 찾을 수 없습니다: {rid}")
            slide_names.append(_resolve_slide_part_name(target, names))

        if slide_names:
            return tuple(slide_names)

        raise ValueError("PPTX 슬라이드 순서를 확인할 presentation relationship이 없습니다.")


def _slide_size_emu(pptx_path: Path) -> tuple[int, int] | None:
    with _open_pptx(pptx_path) as zf:
        try:
            presentation_root = _parse_xml(
                zf.read("ppt/presentation.xml"),
                f"{pptx_path}:ppt/presentation.xml",
            )
        except KeyError:
            return None

    slide_size = presentation_root.find(f"{{{_PRESENTATION_NS}}}sldSz")
    if slide_size is None:
        return None

    width = _positive_int(slide_size.attrib.get("cx"))
    height = _positive_int(slide_size.attrib.get("cy"))
    if width is None or height is None:
        return None
    return (width, height)


def _load_slide_relationships(
    zf: zipfile.ZipFile,
    pptx_path: Path,
) -> dict[str, str]:
    try:
        rels_xml = zf.read("ppt/_rels/presentation.xml.rels")
    except KeyError:
        return {}

    rels_root = _parse_xml(rels_xml, f"{pptx_path}:ppt/_rels/presentation.xml.rels")
    return {
        rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
        for rel in rels_root.findall(f".//{{{_PACKAGE_RELATIONSHIPS_NS}}}Relationship")
        if rel.attrib.get("Type") == _SLIDE_RELATIONSHIP_TYPE
    }


def _normalize_presentation_target(target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join("ppt", target))


def _resolve_slide_part_name(target: str, names: set[str]) -> str:
    slide_name = _normalize_presentation_target(target)
    if not _is_slide_part_name(slide_name):
        raise ValueError(
            f"PPTX slide relationship target은 ppt/slides/*.xml이어야 합니다: {target}"
        )
    if slide_name not in names:
        raise ValueError(f"PPTX 슬라이드 XML을 찾을 수 없습니다: {slide_name}")
    return slide_name


def _extract_marker_slots_from_slide(
    slide_root: Element,
    *,
    slide_index: int,
    slide_number: int,
    slide_part: str,
) -> _MarkerShapeExtraction:
    slots: list[dict] = []
    errors: list[str] = []
    has_marker_candidate = False

    for shape in slide_root.findall(f".//{{{_PRESENTATION_NS}}}sp"):
        result = _marker_slot_from_shape(
            shape,
            slide_index=slide_index,
            slide_number=slide_number,
            slide_part=slide_part,
        )
        if result is None:
            continue

        has_marker_candidate = has_marker_candidate or result.has_marker_candidate
        slots.extend(result.slots)
        errors.extend(result.errors)

    return _MarkerShapeExtraction(
        slots=tuple(slots),
        errors=tuple(errors),
        has_marker_candidate=has_marker_candidate,
    )


def _marker_slot_from_shape(
    shape: Element,
    *,
    slide_index: int,
    slide_number: int,
    slide_part: str,
) -> _MarkerShapeExtraction | None:
    tx_body = shape.find(f"{{{_PRESENTATION_NS}}}txBody")
    if tx_body is None:
        return None

    red_text_parts: list[str] = []
    non_red_text_parts: list[str] = []
    full_text_parts: list[str] = []
    red_segment_count = 0
    in_red_segment = False
    red_font_size_pt: float | None = None

    for paragraph_index, paragraph in enumerate(tx_body.findall(f"{{{_DRAWINGML_NS}}}p")):
        if paragraph_index > 0 and full_text_parts and full_text_parts[-1] != "\n":
            full_text_parts.append("\n")
        if paragraph_index > 0 and red_text_parts and red_text_parts[-1] != "\n":
            red_text_parts.append("\n")

        for child in list(paragraph):
            if child.tag == f"{{{_DRAWINGML_NS}}}br":
                full_text_parts.append("\n")
                if red_text_parts and not non_red_text_parts and red_text_parts[-1] != "\n":
                    red_text_parts.append("\n")
                continue

            if child.tag != f"{{{_DRAWINGML_NS}}}r":
                continue

            run_text = _run_text(child)
            if not run_text:
                continue

            color = _run_srgb_color(child)
            full_text_parts.append(run_text)
            if color == _EXACT_MARKER_RGB:
                red_text_parts.append(run_text)
                if run_text.strip() and not in_red_segment:
                    red_segment_count += 1
                    in_red_segment = True
                if red_font_size_pt is None:
                    red_font_size_pt = _run_font_size_pt(child)
            elif run_text.strip():
                non_red_text_parts.append(run_text)
                in_red_segment = False

    if not red_text_parts and not non_red_text_parts:
        return None

    shape_id = _shape_id(shape) or ""
    shape_name = _shape_name(shape)
    has_marker_candidate = bool(red_text_parts)

    if not red_text_parts:
        return _MarkerShapeExtraction(slots=(), errors=(), has_marker_candidate=False)

    replacement_mode = _mixed_text_replacement_mode(
        has_non_red_text=bool(non_red_text_parts),
        red_segment_count=red_segment_count,
    )
    placeholder_text = _marker_placeholder_text(
        red_text_parts=red_text_parts,
        full_text_parts=full_text_parts,
        replacement_mode=replacement_mode,
    )
    if not placeholder_text:
        return _MarkerShapeExtraction(slots=(), errors=(), has_marker_candidate=False)

    slot = {
        "slot_id": f"slide{slide_number}_shape{shape_id}",
        "slide_index": slide_index,
        "slide_number": slide_number,
        "slide_filename": Path(slide_part).name,
        "slide_part": slide_part,
        "shape_id": shape_id,
        "shape_name": shape_name,
        **_coordinates(shape),
        "placeholder_text": placeholder_text,
        "marker_color": _EXACT_MARKER_COLOR,
        "font_size_pt": red_font_size_pt,
        "kind": "text",
        "editable": True,
        "required": True,
        "allowed_actions": ["text", "remove"],
    }
    if replacement_mode is not None:
        slot["text_replacement_mode"] = replacement_mode
    return _MarkerShapeExtraction(
        slots=(slot,),
        errors=(),
        has_marker_candidate=has_marker_candidate,
    )


def _mixed_text_replacement_mode(
    *,
    has_non_red_text: bool,
    red_segment_count: int,
) -> str | None:
    if not has_non_red_text:
        return None
    if red_segment_count <= 1:
        return "marker_runs"
    return "shape"


def _marker_placeholder_text(
    *,
    red_text_parts: list[str],
    full_text_parts: list[str],
    replacement_mode: str | None,
) -> str:
    if replacement_mode == "shape":
        return "".join(full_text_parts).strip()
    return "".join(red_text_parts).strip()


def _extract_text_shapes_from_slide(slide_root: Element) -> tuple[_TextShapeExtraction, ...]:
    shapes: list[_TextShapeExtraction] = []
    for shape in slide_root.findall(f".//{{{_PRESENTATION_NS}}}sp"):
        extracted = _text_shape_from_shape(shape)
        if extracted is not None:
            shapes.append(extracted)
    return tuple(shapes)


def _text_shape_from_shape(shape: Element) -> _TextShapeExtraction | None:
    tx_body = shape.find(f"{{{_PRESENTATION_NS}}}txBody")
    if tx_body is None:
        return None

    paragraph_texts: list[str] = []
    output_text_color: str | None = None
    for paragraph in tx_body.findall(f"{{{_DRAWINGML_NS}}}p"):
        parts: list[str] = []
        for child in list(paragraph):
            if child.tag == f"{{{_DRAWINGML_NS}}}br":
                parts.append("\n")
                continue
            if child.tag != f"{{{_DRAWINGML_NS}}}r":
                continue

            run_text = _run_text(child)
            parts.append(run_text)
            if output_text_color is None and run_text.strip():
                run_color = _run_srgb_color(child)
                if run_color is not None:
                    output_text_color = f"#{run_color}"
        paragraph_texts.append("".join(parts))

    text = "\n".join(paragraph_texts).strip()
    if not text:
        return None

    coordinates = _coordinates(shape)
    return _TextShapeExtraction(
        shape_id=_shape_id(shape) or "",
        shape_name=_shape_name(shape),
        x_emu=coordinates["x_emu"],
        y_emu=coordinates["y_emu"],
        w_emu=coordinates["w_emu"],
        h_emu=coordinates["h_emu"],
        text=text,
        line_count=len(text.splitlines()) or 1,
        char_count=len(text.replace("\n", "")),
        output_text_color=output_text_color,
    )


def _extract_non_text_shapes_from_slide(
    slide_root: Element,
) -> tuple[_RuntimeShapeGeometry, ...]:
    shapes: list[_RuntimeShapeGeometry] = []
    for shape in slide_root.findall(f".//{{{_PRESENTATION_NS}}}sp"):
        if _shape_has_visible_text(shape):
            continue
        geometry = _runtime_shape_geometry_from_shape(shape)
        if geometry is not None:
            shapes.append(geometry)
    return tuple(shapes)


def _shape_has_visible_text(shape: Element) -> bool:
    return any(
        bool((text_node.text or "").strip())
        for text_node in shape.findall(f".//{{{_DRAWINGML_NS}}}t")
    )


def _runtime_shape_geometry_from_shape(shape: Element) -> _RuntimeShapeGeometry | None:
    coordinates = _coordinates(shape)
    x_emu = coordinates["x_emu"]
    y_emu = coordinates["y_emu"]
    w_emu = coordinates["w_emu"]
    h_emu = coordinates["h_emu"]
    if x_emu is None or y_emu is None or w_emu is None or h_emu is None:
        return None
    if min(w_emu, h_emu) <= 0:
        return None
    return _RuntimeShapeGeometry(
        shape_id=_shape_id(shape) or "",
        shape_name=_shape_name(shape),
        x_emu=x_emu,
        y_emu=y_emu,
        w_emu=w_emu,
        h_emu=h_emu,
    )


def _infer_runtime_shape_relationships(
    slots: tuple[dict, ...],
    non_text_shapes: tuple[_RuntimeShapeGeometry, ...],
    *,
    runtime_slide_number: int,
) -> tuple[list[dict], list[str]]:
    text_slots = tuple(slot for slot in slots if _bbox_tuple(slot) is not None)
    if not text_slots or not non_text_shapes:
        return [], []

    container_inferences = _infer_container_shape_inferences(
        text_slots,
        non_text_shapes,
        runtime_slide_number=runtime_slide_number,
    )
    container_shape_ids = {
        str(inference.get("shape_id"))
        for inference in container_inferences
        if inference.get("shape_id") is not None
    }
    item_inferences, item_warnings = _infer_item_background_inferences(
        text_slots,
        non_text_shapes,
        excluded_shape_ids=container_shape_ids,
        runtime_slide_number=runtime_slide_number,
    )
    return [*container_inferences, *item_inferences], item_warnings


def _infer_container_shape_inferences(
    slots: tuple[dict, ...],
    non_text_shapes: tuple[_RuntimeShapeGeometry, ...],
    *,
    runtime_slide_number: int,
) -> list[dict]:
    inferences: list[dict] = []
    for shape in non_text_shapes:
        contained_slots = _container_contained_slots(slots, shape)
        if len(contained_slots) < _CONTAINER_MIN_SLOT_COUNT:
            continue

        score = _container_shape_score(contained_slots, shape)
        if score is None:
            continue

        inference = _shape_inference_base(
            "container_shape",
            shape,
            runtime_slide_index=_runtime_slide_index(contained_slots),
            runtime_slide_number=runtime_slide_number,
        )
        inference.update(
            {
                "contained_slot_ids": [
                    str(slot.get("slot_id")) for slot in _sort_slots_by_position(contained_slots)
                ],
                "match_score": score,
                "resize_linked": False,
                "allowed_actions": [],
            }
        )
        inferences.append(inference)
    return inferences


def _container_contained_slots(
    slots: tuple[dict, ...],
    shape: _RuntimeShapeGeometry,
) -> tuple[dict, ...]:
    contained: list[dict] = []
    for slot in slots:
        slot_box = _bbox_tuple(slot)
        if slot_box is None:
            continue
        if _box_coverage(slot_box, _shape_bbox(shape)) >= _CONTAINER_MIN_SLOT_COVERAGE:
            contained.append(slot)
    return tuple(contained)


def _container_shape_score(
    contained_slots: tuple[dict, ...],
    shape: _RuntimeShapeGeometry,
) -> float | None:
    shape_box = _shape_bbox(shape)
    shape_area = _box_area(shape_box)
    slot_boxes = tuple(box for slot in contained_slots if (box := _bbox_tuple(slot)) is not None)
    if not slot_boxes:
        return None

    max_slot_area = max(_box_area(slot_box) for slot_box in slot_boxes)
    if shape_area < max_slot_area * _CONTAINER_MIN_MAX_SLOT_AREA_RATIO:
        return None

    union_box = _union_bbox(slot_boxes)
    if union_box is None:
        return None
    union_area = _box_area(union_box)
    if union_area <= 0:
        return None
    if shape_area < union_area * _CONTAINER_MIN_UNION_AREA_RATIO:
        return None
    if shape_area > union_area * _CONTAINER_MAX_UNION_AREA_RATIO:
        return None

    coverage_sum = sum(_box_coverage(slot_box, shape_box) for slot_box in slot_boxes)
    average_slot_coverage = coverage_sum / len(slot_boxes)
    union_coverage = _box_coverage(union_box, shape_box)
    return round((average_slot_coverage * 0.7) + (union_coverage * 0.3), 4)


def _infer_item_background_inferences(
    slots: tuple[dict, ...],
    non_text_shapes: tuple[_RuntimeShapeGeometry, ...],
    *,
    excluded_shape_ids: set[str],
    runtime_slide_number: int,
) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    inferences: list[dict] = []
    candidates_by_slot_id: dict[str, list[tuple[float, _RuntimeShapeGeometry, dict]]] = {}

    for shape in non_text_shapes:
        if shape.shape_id in excluded_shape_ids:
            continue

        overlapping_slots = _background_overlapping_slots(slots, shape)
        candidate_scores = [
            (score, slot)
            for slot in slots
            if (score := _item_background_score(slot, shape)) >= _ITEM_BACKGROUND_MIN_SCORE
        ]
        if len(overlapping_slots) >= 2 and candidate_scores:
            ambiguous_slots = _sort_slots_by_position(overlapping_slots)
            inferences.append(
                _ambiguous_item_background_inference(
                    shape,
                    ambiguous_slots,
                    max(score for score, _slot in candidate_scores),
                    runtime_slide_number=runtime_slide_number,
                )
            )
            warnings.append(
                "runtime slide "
                f"{runtime_slide_number} shape {shape.shape_id}가 여러 text slot "
                f"({_slot_id_list_text(ambiguous_slots)})과 겹쳐 item_background 연결을 "
                "건너뜁니다."
            )
            continue

        for score, slot in candidate_scores:
            slot_id = str(slot.get("slot_id"))
            candidates_by_slot_id.setdefault(slot_id, []).append((score, shape, slot))

    pending_by_shape_id: dict[str, list[tuple[float, _RuntimeShapeGeometry, dict]]] = {}
    for slot_id, candidates in candidates_by_slot_id.items():
        del slot_id
        candidates.sort(key=lambda item: (-item[0], _shape_id_sort_key(item[1].shape_id)))
        score, shape, slot = candidates[0]
        pending_by_shape_id.setdefault(shape.shape_id, []).append((score, shape, slot))

    for shape_id, links in pending_by_shape_id.items():
        if len(links) >= 2:
            slots_for_shape = _sort_slots_by_position(tuple(slot for _score, _shape, slot in links))
            best_score = max(score for score, _shape, _slot in links)
            shape = links[0][1]
            inferences.append(
                _ambiguous_item_background_inference(
                    shape,
                    slots_for_shape,
                    best_score,
                    runtime_slide_number=runtime_slide_number,
                )
            )
            warnings.append(
                "runtime slide "
                f"{runtime_slide_number} shape {shape_id}가 여러 text slot "
                f"({_slot_id_list_text(slots_for_shape)})의 item_background 후보라 연결을 "
                "건너뜁니다."
            )
            continue

        score, shape, slot = links[0]
        inference = _shape_inference_base(
            "item_background",
            shape,
            runtime_slide_index=slot.get("slide_index"),
            runtime_slide_number=runtime_slide_number,
        )
        inference.update(
            {
                "slot_id": slot.get("slot_id"),
                "slot_shape_id": slot.get("shape_id"),
                "match_score": round(score, 4),
                "resize_linked": True,
            }
        )
        inferences.append(inference)

    return inferences, warnings


def _background_overlapping_slots(
    slots: tuple[dict, ...],
    shape: _RuntimeShapeGeometry,
) -> tuple[dict, ...]:
    overlapping: list[dict] = []
    shape_box = _shape_bbox(shape)
    for slot in slots:
        slot_box = _bbox_tuple(slot)
        if slot_box is None:
            continue
        if _box_coverage(slot_box, shape_box) >= _ITEM_BACKGROUND_AMBIGUOUS_SLOT_COVERAGE:
            overlapping.append(slot)
    return tuple(overlapping)


def _item_background_score(slot: dict, shape: _RuntimeShapeGeometry) -> float:
    slot_box = _bbox_tuple(slot)
    if slot_box is None:
        return 0.0

    slot_x, slot_y, slot_w, slot_h = slot_box
    shape_x, shape_y, shape_w, shape_h = _shape_bbox(shape)
    if min(slot_w, slot_h, shape_w, shape_h) <= 0:
        return 0.0

    slot_area = _box_area(slot_box)
    shape_area = _box_area(_shape_bbox(shape))
    if slot_area <= 0 or shape_area <= 0:
        return 0.0
    if shape_w / slot_w > _ITEM_BACKGROUND_MAX_WIDTH_RATIO:
        return 0.0
    if shape_h / slot_h > _ITEM_BACKGROUND_MAX_HEIGHT_RATIO:
        return 0.0
    if shape_area / slot_area > _ITEM_BACKGROUND_MAX_AREA_RATIO:
        return 0.0

    slot_coverage = _box_coverage(slot_box, _shape_bbox(shape))
    if slot_coverage < _ITEM_BACKGROUND_MIN_SLOT_COVERAGE:
        return 0.0

    center = _center_similarity(
        slot_x,
        slot_y,
        slot_w,
        slot_h,
        shape_x,
        shape_y,
        shape_w,
        shape_h,
    )
    size = (_axis_similarity(slot_w, shape_w) + _axis_similarity(slot_h, shape_h)) / 2
    return (slot_coverage * 0.45) + (center * 0.35) + (size * 0.2)


def _ambiguous_item_background_inference(
    shape: _RuntimeShapeGeometry,
    slots: tuple[dict, ...],
    score: float,
    *,
    runtime_slide_number: int,
) -> dict:
    inference = _shape_inference_base(
        "ambiguous_item_background",
        shape,
        runtime_slide_index=_runtime_slide_index(slots),
        runtime_slide_number=runtime_slide_number,
    )
    inference.update(
        {
            "candidate_slot_ids": [str(slot.get("slot_id")) for slot in slots],
            "match_score": round(score, 4),
            "resize_linked": False,
            "allowed_actions": [],
        }
    )
    return inference


def _infer_inline_label_groups(
    slots: tuple[dict, ...],
    shape_inferences: list[dict],
    *,
    runtime_slide_number: int,
    slide_width_emu: int | None,
) -> tuple[list[dict], list[str]]:
    """반복되는 짧은 slot row를 inline label group으로 추론한다."""
    candidate_slots = tuple(slot for slot in slots if _inline_label_slot_candidate(slot))
    if len(candidate_slots) < _INLINE_LABEL_GROUP_MIN_ITEMS:
        return [], []

    item_backgrounds_by_slot_id = _item_background_inferences_by_slot_id(shape_inferences)
    groups: list[dict] = []
    warnings: list[str] = []
    for row in _cluster_inline_label_rows(candidate_slots):
        group = _inline_label_group_from_row(
            row,
            item_backgrounds_by_slot_id,
            runtime_slide_number=runtime_slide_number,
            group_index=len(groups) + 1,
            slide_width_emu=slide_width_emu,
        )
        if group is None:
            if len(row) >= _INLINE_LABEL_GROUP_MIN_ITEMS:
                warnings.append(
                    "runtime slide "
                    f"{runtime_slide_number}의 inline_label_group 후보 "
                    f"({_slot_id_list_text(_sort_slots_by_position(row))})는 "
                    "정렬, gap, background 신뢰도가 부족해 basic_text_area로 둡니다."
                )
            continue
        groups.append(group)
    return groups, warnings


def _inline_label_slot_candidate(slot: dict) -> bool:
    if slot.get("editable") is False:
        return False
    if str(slot.get("kind") or "text").casefold() != "text":
        return False
    if _bbox_tuple(slot) is None:
        return False

    placeholder_text = str(slot.get("placeholder_text") or "").strip()
    if not placeholder_text:
        return False
    if len(placeholder_text.splitlines()) != 1:
        return False
    return len(placeholder_text) <= _INLINE_LABEL_GROUP_MAX_PLACEHOLDER_CHARS


def _item_background_inferences_by_slot_id(
    shape_inferences: list[dict],
) -> dict[str, dict]:
    backgrounds_by_slot_id: dict[str, dict] = {}
    for inference in shape_inferences:
        if inference.get("inference_type") != "item_background":
            continue
        if inference.get("resize_linked") is not True:
            continue
        slot_id = str(inference.get("slot_id") or "")
        if not slot_id:
            continue
        backgrounds_by_slot_id[slot_id] = inference
    return backgrounds_by_slot_id


def _cluster_inline_label_rows(slots: tuple[dict, ...]) -> tuple[tuple[dict, ...], ...]:
    rows: list[list[dict]] = []
    for slot in sorted(slots, key=_slot_center_y_sort_key):
        slot_box = _bbox_tuple(slot)
        if slot_box is None:
            continue

        matching_row = _matching_inline_label_row(rows, slot)
        if matching_row is None:
            rows.append([slot])
        else:
            matching_row.append(slot)

    return tuple(
        _sort_slots_by_position(tuple(row))
        for row in rows
        if len(row) >= _INLINE_LABEL_GROUP_MIN_ITEMS
    )


def _slot_center_y_sort_key(slot: dict) -> tuple[float, int, tuple[int, str]]:
    slot_box = _bbox_tuple(slot)
    if slot_box is None:
        return (float("inf"), 10**18, _shape_id_sort_key(str(slot.get("shape_id") or "")))
    x_emu, y_emu, _w_emu, h_emu = slot_box
    return (y_emu + (h_emu / 2), x_emu, _shape_id_sort_key(str(slot.get("shape_id") or "")))


def _matching_inline_label_row(rows: list[list[dict]], slot: dict) -> list[dict] | None:
    slot_box = _bbox_tuple(slot)
    if slot_box is None:
        return None

    _slot_x, slot_y, _slot_w, slot_h = slot_box
    slot_center_y = slot_y + (slot_h / 2)
    for row in rows:
        row_boxes = tuple(box for row_slot in row if (box := _bbox_tuple(row_slot)) is not None)
        if not row_boxes:
            continue
        row_center_y = sum(y + (height / 2) for _x, y, _width, height in row_boxes) / len(row_boxes)
        max_height = max(slot_h, *(height for _x, _y, _width, height in row_boxes))
        tolerance = max_height * _INLINE_LABEL_GROUP_CENTER_Y_TOLERANCE_RATIO
        if abs(slot_center_y - row_center_y) <= tolerance:
            return row
    return None


def _inline_label_group_from_row(
    row: tuple[dict, ...],
    item_backgrounds_by_slot_id: dict[str, dict],
    *,
    runtime_slide_number: int,
    group_index: int,
    slide_width_emu: int | None,
) -> dict | None:
    if len(row) < _INLINE_LABEL_GROUP_MIN_ITEMS:
        return None
    if not _inline_label_row_geometry_is_confident(row):
        return None

    linked_background_by_item = _linked_background_by_item(row, item_backgrounds_by_slot_id)
    if not _inline_label_backgrounds_are_confident(len(row), linked_background_by_item):
        return None

    gaps = _horizontal_gaps(row)
    slide_index = _runtime_slide_index(row)
    row_left_emu, row_right_emu = _inline_label_row_bounds(row)
    group_id = f"slide{runtime_slide_number}_inline_label_group{group_index}"
    return {
        "group_id": group_id,
        "slide_index": slide_index,
        "slide_number": runtime_slide_number,
        "layout_type": "inline_label_group",
        "flow": "horizontal",
        "item_slot_ids": [str(slot.get("slot_id")) for slot in row],
        "item_shape_ids": [str(slot.get("shape_id")) for slot in row],
        "row_left_emu": row_left_emu,
        "row_right_bound_emu": (
            slide_width_emu
            if slide_width_emu is not None and slide_width_emu > row_right_emu
            else row_right_emu
        ),
        "gap_emu": _median_int(gaps),
        "min_gap_emu": min(gaps),
        "wrap_allowed": False,
        "linked_background_by_item": linked_background_by_item,
    }


def _inline_label_row_geometry_is_confident(row: tuple[dict, ...]) -> bool:
    boxes = tuple(box for slot in row if (box := _bbox_tuple(slot)) is not None)
    if len(boxes) != len(row):
        return False

    widths = [width for _x, _y, width, _height in boxes]
    heights = [height for _x, _y, _width, height in boxes]
    if min(widths, default=0) <= 0 or min(heights, default=0) <= 0:
        return False
    if min(heights) / max(heights) < _INLINE_LABEL_GROUP_MIN_HEIGHT_SIMILARITY:
        return False
    if max(widths) / min(widths) > _INLINE_LABEL_GROUP_MAX_WIDTH_RATIO:
        return False

    centers_y = [y + (height / 2) for _x, y, _width, height in boxes]
    if (
        max(centers_y) - min(centers_y)
        > max(heights) * _INLINE_LABEL_GROUP_CENTER_Y_TOLERANCE_RATIO
    ):
        return False

    gaps = _horizontal_gaps(row)
    if len(gaps) != len(row) - 1:
        return False
    if min(gaps) < 0:
        return False

    median_height = _median_int(heights)
    median_width = _median_int(widths)
    max_allowed_gap = max(
        int(median_height * _INLINE_LABEL_GROUP_MAX_GAP_HEIGHT_RATIO),
        int(median_width * _INLINE_LABEL_GROUP_MAX_GAP_WIDTH_RATIO),
    )
    if max(gaps) > max_allowed_gap:
        return False
    if min(gaps) > 0 and max(gaps) / min(gaps) > _INLINE_LABEL_GROUP_MAX_GAP_VARIANCE_RATIO:
        return False
    if min(gaps) == 0 and max(gaps) > median_height:
        return False
    return True


def _horizontal_gaps(row: tuple[dict, ...]) -> list[int]:
    gaps: list[int] = []
    sorted_row = _sort_slots_by_position(row)
    for left, right in zip(sorted_row, sorted_row[1:]):
        left_box = _bbox_tuple(left)
        right_box = _bbox_tuple(right)
        if left_box is None or right_box is None:
            continue
        left_x, _left_y, left_w, _left_h = left_box
        right_x, _right_y, _right_w, _right_h = right_box
        gaps.append(right_x - (left_x + left_w))
    return gaps


def _inline_label_row_bounds(row: tuple[dict, ...]) -> tuple[int, int]:
    boxes = tuple(box for slot in row if (box := _bbox_tuple(slot)) is not None)
    if not boxes:
        return (0, 0)
    return (
        min(x for x, _y, _width, _height in boxes),
        max(x + width for x, _y, width, _height in boxes),
    )


def _linked_background_by_item(
    row: tuple[dict, ...],
    item_backgrounds_by_slot_id: dict[str, dict],
) -> dict[str, dict]:
    linked: dict[str, dict] = {}
    for slot in row:
        slot_id = str(slot.get("slot_id") or "")
        item_shape_id = str(slot.get("shape_id") or "")
        if not slot_id or not item_shape_id:
            continue
        background = item_backgrounds_by_slot_id.get(slot_id)
        if background is None:
            continue
        linked[item_shape_id] = {
            "slot_id": slot_id,
            "background_shape_id": background.get("shape_id"),
            "background_shape_name": background.get("shape_name"),
            "match_score": background.get("match_score"),
            "resize_linked": background.get("resize_linked") is True,
        }
    return linked


def _inline_label_backgrounds_are_confident(
    item_count: int,
    linked_background_by_item: dict[str, dict],
) -> bool:
    linked_count = len(linked_background_by_item)
    if linked_count < _INLINE_LABEL_GROUP_MIN_BACKGROUND_ITEMS:
        return False
    return linked_count == item_count


def _median_int(values: list[int]) -> int:
    sorted_values = sorted(values)
    if not sorted_values:
        return 0
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return sorted_values[middle]
    return round((sorted_values[middle - 1] + sorted_values[middle]) / 2)


def _shape_inference_base(
    inference_type: str,
    shape: _RuntimeShapeGeometry,
    *,
    runtime_slide_index: int | None,
    runtime_slide_number: int,
) -> dict:
    return {
        "inference_type": inference_type,
        "runtime_slide_index": runtime_slide_index,
        "runtime_slide_number": runtime_slide_number,
        "shape_id": shape.shape_id,
        "shape_name": shape.shape_name,
        "x_emu": shape.x_emu,
        "y_emu": shape.y_emu,
        "w_emu": shape.w_emu,
        "h_emu": shape.h_emu,
    }


def _runtime_slide_index(slots: tuple[dict, ...]) -> int | None:
    if not slots:
        return None
    slide_index = slots[0].get("slide_index")
    return (
        slide_index if isinstance(slide_index, int) and not isinstance(slide_index, bool) else None
    )


def _sort_slots_by_position(slots: tuple[dict, ...]) -> tuple[dict, ...]:
    return tuple(sorted(slots, key=_slot_position_sort_key))


def _slot_position_sort_key(slot: dict) -> tuple[int, int, tuple[int, str]]:
    slot_box = _bbox_tuple(slot)
    if slot_box is None:
        return (10**18, 10**18, _shape_id_sort_key(str(slot.get("shape_id") or "")))
    x_emu, y_emu, _w_emu, _h_emu = slot_box
    return (y_emu, x_emu, _shape_id_sort_key(str(slot.get("shape_id") or "")))


def _slot_id_list_text(slots: tuple[dict, ...]) -> str:
    return ", ".join(str(slot.get("slot_id")) for slot in slots)


def _match_reference_shapes(
    slots: tuple[dict, ...],
    example_shapes: tuple[_TextShapeExtraction, ...],
    *,
    runtime_slide_number: int,
    example_slide_index: int,
    example_slide_number: int,
    example_slide_part: str,
) -> tuple[list[dict], list[str], list[str]]:
    matches: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    used_shape_ids: set[str] = set()

    for slot in slots:
        candidates = [
            (_reference_match_score(slot, shape), shape)
            for shape in example_shapes
            if shape.shape_id not in used_shape_ids
        ]
        candidates.sort(key=lambda item: (-item[0], _shape_id_sort_key(item[1].shape_id)))
        if not candidates:
            _append_reference_match_failure(
                slot,
                runtime_slide_number=runtime_slide_number,
                errors=errors,
                warnings=warnings,
            )
            continue

        score, matched_shape = candidates[0]
        if score < _REFERENCE_MATCH_FAIL_SCORE:
            _append_reference_match_failure(
                slot,
                runtime_slide_number=runtime_slide_number,
                errors=errors,
                warnings=warnings,
            )
            continue

        used_shape_ids.add(matched_shape.shape_id)
        confidence = "high" if score >= _REFERENCE_MATCH_LOW_CONFIDENCE_SCORE else "low"
        if confidence == "low":
            warnings.append(
                "runtime slide "
                f"{runtime_slide_number} slot {slot.get('slot_id')}의 example shape "
                f"매칭 신뢰도가 낮습니다. (score: {score:.4f})"
            )

        output_text_color = matched_shape.output_text_color
        if output_text_color is None:
            output_text_color = _REFERENCE_COLOR_FALLBACK
            warnings.append(
                "runtime slide "
                f"{runtime_slide_number} slot {slot.get('slot_id')}의 example shape "
                f"{matched_shape.shape_id}에서 output_text_color를 찾지 못해 "
                f"{_REFERENCE_COLOR_FALLBACK}을 사용합니다."
            )

        matches.append(
            {
                "slot_id": slot.get("slot_id"),
                "runtime_slide_index": slot.get("slide_index"),
                "runtime_slide_number": runtime_slide_number,
                "runtime_shape_id": slot.get("shape_id"),
                "example_slide_index": example_slide_index,
                "example_slide_number": example_slide_number,
                "example_slide_filename": Path(example_slide_part).name,
                "example_slide_part": example_slide_part,
                "example_shape_id": matched_shape.shape_id,
                "example_shape_name": matched_shape.shape_name,
                "example_text": matched_shape.text,
                "example_char_count": matched_shape.char_count,
                "example_line_count": matched_shape.line_count,
                "output_text_color": output_text_color,
                "match_score": round(score, 4),
                "match_confidence": confidence,
            }
        )

    return matches, errors, warnings


def _append_reference_match_failure(
    slot: dict,
    *,
    runtime_slide_number: int,
    errors: list[str],
    warnings: list[str],
) -> None:
    message = _reference_match_error(runtime_slide_number, slot)
    if _slot_requires_reference_match(slot):
        errors.append(message)
        return
    warnings.append(f"{message} marker_runs slot이므로 reference match 없이 진행합니다.")


def _slot_requires_reference_match(slot: dict) -> bool:
    return slot.get("text_replacement_mode") != "marker_runs"


def _reference_match_error(runtime_slide_number: int, slot: dict) -> str:
    return (
        "runtime slide "
        f"{runtime_slide_number} slot {slot.get('slot_id')}의 example shape 매칭에 실패했습니다."
    )


def _reference_match_score(slot: dict, shape: _TextShapeExtraction) -> float:
    slot_box = _bbox_tuple(slot)
    shape_box = (shape.x_emu, shape.y_emu, shape.w_emu, shape.h_emu)
    if slot_box is None or None in shape_box:
        return _shape_id_reference_score(slot, shape)

    slot_x, slot_y, slot_w, slot_h = slot_box
    shape_x, shape_y, shape_w, shape_h = shape_box
    if shape_x is None or shape_y is None or shape_w is None or shape_h is None:
        return _shape_id_reference_score(slot, shape)
    if min(slot_w, slot_h, shape_w, shape_h) <= 0:
        return _shape_id_reference_score(slot, shape)

    overlap = _intersection_over_union(
        slot_x, slot_y, slot_w, slot_h, shape_x, shape_y, shape_w, shape_h
    )
    center = _center_similarity(
        slot_x,
        slot_y,
        slot_w,
        slot_h,
        shape_x,
        shape_y,
        shape_w,
        shape_h,
    )
    size = (_axis_similarity(slot_w, shape_w) + _axis_similarity(slot_h, shape_h)) / 2
    geometry_score = (overlap * 0.5) + (center * 0.3) + (size * 0.2)
    return max(geometry_score, _shape_id_reference_score(slot, shape))


def _shape_id_reference_score(slot: dict, shape: _TextShapeExtraction) -> float:
    slot_shape_id = str(slot.get("shape_id") or "").strip()
    if slot_shape_id and slot_shape_id == shape.shape_id:
        return 0.85
    return 0.0


def _bbox_tuple(source: dict) -> tuple[int, int, int, int] | None:
    values = (
        source.get("x_emu"),
        source.get("y_emu"),
        source.get("w_emu"),
        source.get("h_emu"),
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        return None
    return values


def _shape_bbox(shape: _RuntimeShapeGeometry) -> tuple[int, int, int, int]:
    return (shape.x_emu, shape.y_emu, shape.w_emu, shape.h_emu)


def _box_area(box: tuple[int, int, int, int]) -> int:
    _x, _y, width, height = box
    return max(width, 0) * max(height, 0)


def _box_coverage(
    inner_box: tuple[int, int, int, int],
    outer_box: tuple[int, int, int, int],
) -> float:
    inner_area = _box_area(inner_box)
    if inner_area <= 0:
        return 0.0
    return _intersection_area(inner_box, outer_box) / inner_area


def _intersection_area(
    left_box: tuple[int, int, int, int],
    right_box: tuple[int, int, int, int],
) -> int:
    left_x, left_y, left_w, left_h = left_box
    right_x, right_y, right_w, right_h = right_box
    overlap_w = max(0, min(left_x + left_w, right_x + right_w) - max(left_x, right_x))
    overlap_h = max(0, min(left_y + left_h, right_y + right_h) - max(left_y, right_y))
    return overlap_w * overlap_h


def _union_bbox(boxes: tuple[tuple[int, int, int, int], ...]) -> tuple[int, int, int, int] | None:
    if not boxes:
        return None
    min_x = min(box[0] for box in boxes)
    min_y = min(box[1] for box in boxes)
    max_x = max(box[0] + box[2] for box in boxes)
    max_y = max(box[1] + box[3] for box in boxes)
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def _intersection_over_union(
    left_x: int,
    left_y: int,
    left_w: int,
    left_h: int,
    right_x: int,
    right_y: int,
    right_w: int,
    right_h: int,
) -> float:
    overlap_w = max(0, min(left_x + left_w, right_x + right_w) - max(left_x, right_x))
    overlap_h = max(0, min(left_y + left_h, right_y + right_h) - max(left_y, right_y))
    intersection = overlap_w * overlap_h
    union = (left_w * left_h) + (right_w * right_h) - intersection
    if union <= 0:
        return 0.0
    return intersection / union


def _center_similarity(
    left_x: int,
    left_y: int,
    left_w: int,
    left_h: int,
    right_x: int,
    right_y: int,
    right_w: int,
    right_h: int,
) -> float:
    left_center_x = left_x + left_w / 2
    left_center_y = left_y + left_h / 2
    right_center_x = right_x + right_w / 2
    right_center_y = right_y + right_h / 2
    distance = (
        (left_center_x - right_center_x) ** 2 + (left_center_y - right_center_y) ** 2
    ) ** 0.5
    reference_distance = max((left_w**2 + left_h**2) ** 0.5, 1.0)
    return max(0.0, 1 - (distance / reference_distance))


def _axis_similarity(left: int, right: int) -> float:
    return min(left, right) / max(left, right)


def _shape_id_sort_key(shape_id: str) -> tuple[int, str]:
    if shape_id.isdigit():
        return (int(shape_id), shape_id)
    return (10**9, shape_id)


def _shape_id(shape: Element) -> str | None:
    cnv_pr = _cnv_pr(shape)
    return cnv_pr.attrib.get("id") if cnv_pr is not None else None


def _shape_name(shape: Element) -> str:
    cnv_pr = _cnv_pr(shape)
    return cnv_pr.attrib.get("name", "") if cnv_pr is not None else ""


def _cnv_pr(shape: Element) -> Element | None:
    return shape.find(
        f"{{{_PRESENTATION_NS}}}nvSpPr/{{{_PRESENTATION_NS}}}cNvPr",
    )


def _coordinates(shape: Element) -> dict[str, int | None]:
    xfrm = shape.find(
        f"{{{_PRESENTATION_NS}}}spPr/{{{_DRAWINGML_NS}}}xfrm",
    )
    off = xfrm.find(f"{{{_DRAWINGML_NS}}}off") if xfrm is not None else None
    ext = xfrm.find(f"{{{_DRAWINGML_NS}}}ext") if xfrm is not None else None
    return {
        "x_emu": _int_attr(off, "x"),
        "y_emu": _int_attr(off, "y"),
        "w_emu": _int_attr(ext, "cx"),
        "h_emu": _int_attr(ext, "cy"),
    }


def _run_text(run: Element) -> str:
    return "".join(text_node.text or "" for text_node in run.findall(f".//{{{_DRAWINGML_NS}}}t"))


def _run_srgb_color(run: Element) -> str | None:
    color = run.find(
        f"{{{_DRAWINGML_NS}}}rPr/{{{_DRAWINGML_NS}}}solidFill/{{{_DRAWINGML_NS}}}srgbClr"
    )
    if color is None:
        return None
    if list(color):
        return None

    raw_value = color.attrib.get("val")
    if raw_value is None:
        return None
    return raw_value.upper()


def _run_font_size_pt(run: Element) -> float | None:
    run_props = run.find(f"{{{_DRAWINGML_NS}}}rPr")
    if run_props is None:
        return None

    size = run_props.attrib.get("sz")
    if size is None:
        return None
    try:
        return int(size) / 100
    except ValueError:
        return None


def _int_attr(element: Element | None, attr_name: str) -> int | None:
    if element is None:
        return None
    value = element.attrib.get(attr_name)
    if value is None:
        return None
    return int(value)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _is_slide_part_name(part_name: str) -> bool:
    if not part_name.startswith("ppt/slides/"):
        return False
    slide_file_name = part_name.removeprefix("ppt/slides/")
    return bool(slide_file_name) and "/" not in slide_file_name and slide_file_name.endswith(".xml")


def _open_pptx(path: Path) -> zipfile.ZipFile:
    if not path.is_file():
        raise ValueError(f"PPTX 파일을 찾을 수 없습니다: {path}")
    try:
        return zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"PPTX ZIP 패키지 형식이 올바르지 않습니다: {path}") from exc


def _parse_xml(data: bytes, label: str) -> Element:
    try:
        return ElementTree.fromstring(data)
    except (ParseError, DefusedXmlException) as exc:
        raise ValueError(f"XML 파싱에 실패했습니다: {label}") from exc
