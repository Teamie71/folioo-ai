#!/usr/bin/env python3
"""PPTX 작업 디렉터리의 핵심 패키지 구조를 검증한다."""

from __future__ import annotations

import argparse
import posixpath
import sys
from pathlib import Path

import defusedxml.minidom

PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
SLIDE_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unpacked_dir", type=Path)
    parser.add_argument("--original", type=Path, default=None)
    parser.add_argument("--auto-repair", action="store_true")
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


def _parse_xml(path: Path, errors: list[str]):
    try:
        return defusedxml.minidom.parse(str(path))
    except Exception as exc:
        errors.append(f"xml parse failed: {path}: {exc}")
        return None


def _validate(root: Path) -> list[str]:
    errors: list[str] = []
    required = [
        root / "[Content_Types].xml",
        root / "_rels" / ".rels",
        root / "ppt" / "presentation.xml",
        root / "ppt" / "_rels" / "presentation.xml.rels",
    ]
    for path in required:
        if not path.is_file():
            errors.append(f"missing required part: {path.relative_to(root)}")
    if errors:
        return errors

    presentation_dom = _parse_xml(root / "ppt" / "presentation.xml", errors)
    rels_dom = _parse_xml(root / "ppt" / "_rels" / "presentation.xml.rels", errors)
    _parse_xml(root / "[Content_Types].xml", errors)
    _parse_xml(root / "_rels" / ".rels", errors)
    if errors or presentation_dom is None or rels_dom is None:
        return errors

    slide_id_lists = presentation_dom.getElementsByTagNameNS(PRESENTATION_NS, "sldIdLst")
    if not slide_id_lists:
        errors.append("presentation.xml missing p:sldIdLst")
        return errors

    slide_rids = [
        _relationship_id(node)
        for node in slide_id_lists[0].childNodes
        if node.nodeType == node.ELEMENT_NODE and node.localName == "sldId"
    ]
    if not slide_rids:
        errors.append("presentation.xml has no slides")
        return errors

    slide_targets: dict[str, str] = {}
    for rel in rels_dom.getElementsByTagNameNS(PACKAGE_RELATIONSHIPS_NS, "Relationship"):
        if rel.getAttribute("Type") != SLIDE_RELATIONSHIP_TYPE:
            continue
        slide_filename = _slide_filename_from_target(rel.getAttribute("Target"))
        if slide_filename:
            slide_targets[rel.getAttribute("Id")] = slide_filename

    for rid in slide_rids:
        slide_filename = slide_targets.get(rid)
        if not slide_filename:
            errors.append(f"missing slide relationship for {rid}")
            continue
        slide_path = root / "ppt" / "slides" / slide_filename
        if not slide_path.is_file():
            errors.append(f"missing slide part: ppt/slides/{slide_filename}")
            continue
        _parse_xml(slide_path, errors)

    return errors


def main() -> int:
    args = _parse_args()
    if not args.unpacked_dir.is_dir():
        print(f"unpacked dir not found: {args.unpacked_dir}", file=sys.stderr)
        return 2

    errors = _validate(args.unpacked_dir)
    if not errors:
        return 0

    for error in errors:
        print(error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
