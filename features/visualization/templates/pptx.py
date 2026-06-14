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

    if not runtime_slides:
        errors.append("runtime 대상 슬라이드가 없습니다.")

    return TemplateV2Extraction(
        runtime_slides=tuple(runtime_slides),
        slots=tuple(slots),
        layout_groups=(),
        slide_pairs=tuple(slide_pairs),
        shape_matches=(),
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
        "allowed_actions": ["text"],
    }
    return _MarkerShapeExtraction(
        slots=(slot,),
        errors=(),
        has_marker_candidate=has_marker_candidate,
    )


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
