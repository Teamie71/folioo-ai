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
    runtime_slides: list[dict] = []
    slide_pairs: list[dict] = []
    shape_matches: list[dict] = []
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
        layout_groups=(),
        slide_pairs=tuple(slide_pairs),
        shape_matches=tuple(shape_matches),
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
    red_font_size_pt: float | None = None

    for paragraph_index, paragraph in enumerate(tx_body.findall(f"{{{_DRAWINGML_NS}}}p")):
        if paragraph_index > 0 and red_text_parts and red_text_parts[-1] != "\n":
            red_text_parts.append("\n")

        for child in list(paragraph):
            if child.tag == f"{{{_DRAWINGML_NS}}}br":
                if red_text_parts and not non_red_text_parts and red_text_parts[-1] != "\n":
                    red_text_parts.append("\n")
                continue

            if child.tag != f"{{{_DRAWINGML_NS}}}r":
                continue

            run_text = _run_text(child)
            if not run_text:
                continue

            color = _run_srgb_color(child)
            if color == _EXACT_MARKER_RGB:
                red_text_parts.append(run_text)
                if red_font_size_pt is None:
                    red_font_size_pt = _run_font_size_pt(child)
            elif run_text.strip():
                non_red_text_parts.append(run_text)

    if not red_text_parts and not non_red_text_parts:
        return None

    shape_id = _shape_id(shape) or ""
    shape_name = _shape_name(shape)
    has_marker_candidate = bool(red_text_parts)
    if red_text_parts and non_red_text_parts:
        return _MarkerShapeExtraction(
            slots=(),
            errors=(
                "runtime slide "
                f"{slide_number} shape {shape_id or '(unknown)'}에 "
                "#FF0000 marker와 non-red run이 섞여 있습니다.",
            ),
            has_marker_candidate=True,
        )

    if not red_text_parts:
        return _MarkerShapeExtraction(slots=(), errors=(), has_marker_candidate=False)

    placeholder_text = "".join(red_text_parts).strip()
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
    return _MarkerShapeExtraction(
        slots=(slot,),
        errors=(),
        has_marker_candidate=has_marker_candidate,
    )


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
            errors.append(_reference_match_error(runtime_slide_number, slot))
            continue

        score, matched_shape = candidates[0]
        if score < _REFERENCE_MATCH_FAIL_SCORE:
            errors.append(_reference_match_error(runtime_slide_number, slot))
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


def _reference_match_error(runtime_slide_number: int, slot: dict) -> str:
    return (
        "runtime slide "
        f"{runtime_slide_number} slot {slot.get('slot_id')}의 example shape 매칭에 실패했습니다."
    )


def _reference_match_score(slot: dict, shape: _TextShapeExtraction) -> float:
    slot_box = _bbox_tuple(slot)
    shape_box = (shape.x_emu, shape.y_emu, shape.w_emu, shape.h_emu)
    if slot_box is None or None in shape_box:
        return 0.0

    slot_x, slot_y, slot_w, slot_h = slot_box
    shape_x, shape_y, shape_w, shape_h = shape_box
    if shape_x is None or shape_y is None or shape_w is None or shape_h is None:
        return 0.0
    if min(slot_w, slot_h, shape_w, shape_h) <= 0:
        return 0.0

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
    return (overlap * 0.5) + (center * 0.3) + (size * 0.2)


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
