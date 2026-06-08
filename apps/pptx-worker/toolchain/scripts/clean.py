#!/usr/bin/env python3
"""PPTX 작업 디렉터리에서 presentation.xml 이 참조하지 않는 slide part 를 정리한다."""

from __future__ import annotations

import argparse
import posixpath
import sys
from pathlib import Path

import defusedxml.minidom

PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
SLIDE_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unpacked_dir", type=Path)
    return parser.parse_args()


def _relationship_id(node) -> str:
    return node.getAttributeNS(RELATIONSHIPS_NS, "id") or node.getAttribute("r:id")


def _slide_filename_from_target(target: str) -> str | None:
    if not target:
        return None
    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    else:
        normalized = posixpath.normpath(posixpath.join("ppt", target))
    prefix = "ppt/slides/"
    if not normalized.startswith(prefix):
        return None
    return normalized.removeprefix(prefix)


def _write_xml(path: Path, dom) -> None:
    path.write_bytes(dom.toxml(encoding="UTF-8"))


def _clean_presentation_relationships(root: Path) -> set[str]:
    presentation_path = root / "ppt" / "presentation.xml"
    rels_path = root / "ppt" / "_rels" / "presentation.xml.rels"
    presentation_dom = defusedxml.minidom.parse(str(presentation_path))
    rels_dom = defusedxml.minidom.parse(str(rels_path))

    slide_id_lists = presentation_dom.getElementsByTagNameNS(PRESENTATION_NS, "sldIdLst")
    if not slide_id_lists:
        raise ValueError("presentation.xml missing p:sldIdLst")
    active_rids = {
        _relationship_id(node)
        for node in slide_id_lists[0].childNodes
        if node.nodeType == node.ELEMENT_NODE and node.localName == "sldId"
    }

    active_slide_filenames: set[str] = set()
    removed_slide_filenames: set[str] = set()
    relationships = [
        node
        for node in rels_dom.getElementsByTagNameNS(PACKAGE_RELATIONSHIPS_NS, "Relationship")
        if node.getAttribute("Type") == SLIDE_RELATIONSHIP_TYPE
    ]
    for rel in relationships:
        slide_filename = _slide_filename_from_target(rel.getAttribute("Target"))
        if not slide_filename:
            continue
        if rel.getAttribute("Id") in active_rids:
            active_slide_filenames.add(slide_filename)
            continue
        removed_slide_filenames.add(slide_filename)
        rel.parentNode.removeChild(rel)
        rel.unlink()

    _write_xml(rels_path, rels_dom)
    return removed_slide_filenames - active_slide_filenames


def _remove_orphan_slide_files(root: Path, slide_filenames: set[str]) -> None:
    for slide_filename in slide_filenames:
        for path in (
            root / "ppt" / "slides" / slide_filename,
            root / "ppt" / "slides" / "_rels" / f"{slide_filename}.rels",
        ):
            path.unlink(missing_ok=True)


def _clean_content_types(root: Path, removed_slide_filenames: set[str]) -> None:
    content_types_path = root / "[Content_Types].xml"
    if not content_types_path.is_file() or not removed_slide_filenames:
        return
    removed_part_names = {f"/ppt/slides/{filename}" for filename in removed_slide_filenames}
    dom = defusedxml.minidom.parse(str(content_types_path))
    overrides = [
        node
        for node in dom.getElementsByTagNameNS(CONTENT_TYPES_NS, "Override")
        if node.getAttribute("PartName") in removed_part_names
    ]
    for override in overrides:
        override.parentNode.removeChild(override)
        override.unlink()
    _write_xml(content_types_path, dom)


def main() -> int:
    args = _parse_args()
    if not args.unpacked_dir.is_dir():
        print(f"unpacked dir not found: {args.unpacked_dir}", file=sys.stderr)
        return 2

    removed = _clean_presentation_relationships(args.unpacked_dir)
    _remove_orphan_slide_files(args.unpacked_dir, removed)
    _clean_content_types(args.unpacked_dir, removed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
