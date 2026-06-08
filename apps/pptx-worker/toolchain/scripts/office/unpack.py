#!/usr/bin/env python3
"""PPTX 패키지를 작업 디렉터리에 안전하게 해제한다."""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_pptx", type=Path)
    parser.add_argument("output_dir", type=Path)
    return parser.parse_args()


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    for member in archive.infolist():
        target = Path(member.filename)
        if target.is_absolute() or ".." in target.parts:
            raise ValueError(f"unsafe zip member path: {member.filename}")
        members.append(member)
    return members


def main() -> int:
    args = _parse_args()
    if not args.input_pptx.is_file():
        print(f"input pptx not found: {args.input_pptx}", file=sys.stderr)
        return 2

    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(args.input_pptx) as archive:
        archive.extractall(args.output_dir, members=_safe_members(archive))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
