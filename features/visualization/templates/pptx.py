"""PPTX 패키지에서 슬라이드 순서와 텍스트를 추출하는 유틸리티."""

import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree

_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SLIDE_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)
_SLIDE_FILE_PATTERN = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


@dataclass(frozen=True)
class SlideText:
    """PPTX 슬라이드별 임시 텍스트."""

    slide_index: int
    text: str


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
                continue
            target = rid_to_target.get(rid)
            if target is None:
                continue
            slide_names.append(_normalize_presentation_target(target))

        if slide_names:
            return tuple(slide_names)

        return tuple(sorted(_fallback_slide_part_names(names), key=_slide_number))


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


def _fallback_slide_part_names(names: set[str]) -> list[str]:
    return [name for name in names if _SLIDE_FILE_PATTERN.fullmatch(name)]


def _slide_number(name: str) -> int:
    match = _SLIDE_FILE_PATTERN.fullmatch(name)
    return int(match.group(1)) if match else 0


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
    except ParseError as exc:
        raise ValueError(f"XML 파싱에 실패했습니다: {label}") from exc
